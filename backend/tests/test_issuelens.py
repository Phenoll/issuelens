import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure backend directory is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import Base, Issue, Repository, TriageReport, get_db
from github_service import fetch_recent_issues_and_prs, parse_repo_url, sync_repository_issues
from main import app
from schemas import IssueRead, TriageReportRead
from triage_service import (
    TriageAnalysisSchema,
    analyze_issue_or_pr_with_gemini,
    detect_breaking_changes_in_diff,
    detect_unit_tests_in_diff,
    run_triage_for_issue,
)


@pytest.fixture()
def db_session():
    """Provide an isolated in-memory SQLite session for each test."""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client(db_session):
    """FastAPI TestClient wired to the isolated in-memory database session."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Mocked GitHub API Issue & PR Fetching Tests
# ---------------------------------------------------------------------------


def test_parse_repo_url_variants():
    assert parse_repo_url("facebook/react") == ("facebook", "react")
    assert parse_repo_url("https://github.com/fastapi/fastapi") == ("fastapi", "fastapi")
    assert parse_repo_url("https://github.com/pallets/flask.git/") == ("pallets", "flask")


def test_mocked_github_api_issue_fetching(db_session):
    """Verify fetching open issues and PRs (with comments and diffs) from mocked GitHub API."""
    mock_issues_payload = [
        {
            "id": 10001,
            "number": 42,
            "title": "Crash when parsing empty config file",
            "body": "App crashes on startup when config.yaml is empty.",
            "user": {"login": "alice"},
            "state": "open",
            "created_at": "2026-10-15T10:00:00Z",
            "comments": 1,
            "comments_url": "https://api.github.com/repos/octocat/hello-world/issues/42/comments",
        },
        {
            "id": 10002,
            "number": 43,
            "title": "feat: update public handler signature",
            "body": "Refactors public handler.",
            "user": {"login": "bob"},
            "state": "open",
            "created_at": "2026-10-15T11:30:00Z",
            "comments": 0,
            "pull_request": {
                "url": "https://api.github.com/repos/octocat/hello-world/pulls/43",
            },
        },
    ]

    mock_comments_payload = [
        {
            "id": 501,
            "user": {"login": "maintainer"},
            "body": "Can you share your stack trace?",
            "created_at": "2026-10-15T10:15:00Z",
        }
    ]

    mock_diff_text = (
        "diff --git a/app/api.py b/app/api.py\n"
        "--- a/app/api.py\n"
        "+++ b/app/api.py\n"
        "-def handle_request(req, strict=False):\n"
        "+def handle_request(req):\n"
        "     return req\n"
    )

    async def mock_get(url, headers=None, params=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if url.endswith("/issues") and params and params.get("state") == "open":
            resp.json.return_value = mock_issues_payload
        elif url.endswith("/issues/42/comments"):
            resp.json.return_value = mock_comments_payload
        elif url.endswith("/pulls/43"):
            resp.text = mock_diff_text
        else:
            resp.json.return_value = []
            resp.text = ""
        return resp

    mock_async_client = AsyncMock()
    mock_async_client.get.side_effect = mock_get
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__.return_value = mock_async_client
    mock_client_cm.__aexit__.return_value = None

    with patch("github_service.httpx.AsyncClient", return_value=mock_client_cm):
        repo, synced_issues = asyncio.run(
            sync_repository_issues(db_session, "https://github.com/octocat/hello-world", limit=20)
        )

    assert repo.owner == "octocat"
    assert repo.name == "hello-world"
    assert len(synced_issues) == 2

    issue_item = next(i for i in synced_issues if i.github_issue_id == 42)
    pr_item = next(i for i in synced_issues if i.github_issue_id == 43)

    assert issue_item.is_pr is False
    assert issue_item.author == "alice"
    assert "Can you share your stack trace?" in (issue_item.body or "")
    assert issue_item.comments is not None and len(issue_item.comments) == 1

    assert pr_item.is_pr is True
    assert pr_item.author == "bob"
    assert pr_item.diff is not None
    assert "-def handle_request(req, strict=False):" in pr_item.diff


# ---------------------------------------------------------------------------
# 2. Triage Service Output Parsing & Schema Validation Tests
# ---------------------------------------------------------------------------


def test_triage_service_output_parsing_and_schema_validation(db_session):
    """Validate parsing of Gemini structured JSON output into TriageAnalysisSchema and TriageReport."""
    repo = Repository(owner="pallets", name="flask")
    db_session.add(repo)
    db_session.flush()

    issue = Issue(
        repo_id=repo.id,
        github_issue_id=5012,
        title="Session cookie disappears intermittently on mobile Safari",
        body="Users report getting logged out randomly on iOS.",
        author="ios-user",
        is_pr=False,
        state="open",
    )
    db_session.add(issue)
    db_session.commit()

    mock_gemini_json = json.dumps(
        {
            "needs_repro": True,
            "is_breaking": False,
            "has_unit_tests": False,
            "suggested_tags": ["bug", "repro-needed"],
            "ai_summary": "Bug report missing reproduction steps, cookie configuration snippet, and browser logs.",
            "drafted_reply": "Hi @ios-user, could you share a minimal Flask app reproducing this session behavior?",
        }
    )

    mock_interaction = SimpleNamespace(output_text=mock_gemini_json)
    mock_client = MagicMock()
    mock_client.interactions.create.return_value = mock_interaction

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}), patch(
        "triage_service.genai.Client", return_value=mock_client
    ):
        parsed = analyze_issue_or_pr_with_gemini(issue)
        assert isinstance(parsed, TriageAnalysisSchema)
        assert parsed.needs_repro is True
        assert parsed.is_breaking is False
        assert "bug" in parsed.suggested_tags
        assert "repro-needed" in parsed.suggested_tags

        saved_report = run_triage_for_issue(db_session, issue)

    # Validate SQLAlchemy model -> Pydantic v2 TriageReportRead schema
    validated_schema = TriageReportRead.model_validate(saved_report)
    assert validated_schema.issue_id == issue.id
    assert validated_schema.needs_repro is True
    assert validated_schema.is_breaking is False
    assert validated_schema.suggested_tags == ["bug", "repro-needed"]
    assert "missing reproduction steps" in (validated_schema.ai_summary or "").lower()
    assert "@ios-user" in (validated_schema.drafted_reply or "")


def test_pr_diff_heuristics_for_breaking_changes_and_unit_tests():
    diff_with_breaking_and_tests = (
        "diff --git a/src/api.py b/src/api.py\n"
        "-def fetch_users(limit: int, offset: int):\n"
        "+def fetch_users(limit: int):\n"
        "diff --git a/tests/test_api.py b/tests/test_api.py\n"
        "+++ b/tests/test_api.py\n"
        "+def test_fetch_users(): pass\n"
    )
    assert detect_breaking_changes_in_diff(diff_with_breaking_and_tests) is True
    assert detect_unit_tests_in_diff(diff_with_breaking_and_tests) is True


# ---------------------------------------------------------------------------
# 3. GET /api/issues Endpoint Tests
# ---------------------------------------------------------------------------


def test_get_api_issues_returns_issues_with_triage_reports(client, db_session):
    """Verify GET /api/issues returns scanned issues along with their nested TriageReports."""
    repo = Repository(owner="facebook", name="react")
    db_session.add(repo)
    db_session.flush()

    issue = Issue(
        repo_id=repo.id,
        github_issue_id=9001,
        title="Hydration mismatch when rendering suspense fallback",
        body="Happens in production builds without stack trace.",
        author="react-dev",
        is_pr=False,
        state="open",
    )
    db_session.add(issue)
    db_session.flush()

    report = TriageReport(
        issue_id=issue.id,
        needs_repro=True,
        is_breaking=False,
        suggested_tags=["bug", "repro-needed"],
        ai_summary="Missing reproduction repository and hydration error log.",
        drafted_reply="Please provide a minimal reproduction.",
    )
    db_session.add(report)
    db_session.commit()

    response = client.get("/api/issues")
    assert response.status_code == 200

    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 1

    item = payload[0]
    validated_issue = IssueRead.model_validate(item)
    assert validated_issue.github_issue_id == 9001
    assert validated_issue.title == "Hydration mismatch when rendering suspense fallback"
    assert validated_issue.triage_report is not None
    assert validated_issue.triage_report.needs_repro is True
    assert validated_issue.triage_report.suggested_tags == ["bug", "repro-needed"]
    assert validated_issue.triage_report.ai_summary == "Missing reproduction repository and hydration error log."


# ---------------------------------------------------------------------------
# 4. Stellar XLM Bounty & Testnet Horizon Integration Tests
# ---------------------------------------------------------------------------


def test_complexity_bounty_xlm_auto_calculation():
    from models import calculate_bounty_xlm

    assert calculate_bounty_xlm("Low") == ("Low", 25.0)
    assert calculate_bounty_xlm("Medium") == ("Medium", 75.0)
    assert calculate_bounty_xlm("High") == ("High", 150.0)
    assert calculate_bounty_xlm(is_breaking=True) == ("High", 150.0)
    assert calculate_bounty_xlm(suggested_tags=["good-first-issue"]) == ("Low", 25.0)


def test_stellar_bounty_payout_and_network_status_endpoints(client, db_session):
    """Verify POST /api/issues/{issue_id}/bounty/payout and GET /api/stellar/network-status."""
    repo = Repository(owner="stellar", name="py-stellar-base")
    db_session.add(repo)
    db_session.flush()

    issue = Issue(
        repo_id=repo.id,
        github_issue_id=1088,
        title="Add Soroban transaction footprint helper",
        body="Enhancement adding footprint helper for Soroban smart contracts.",
        author="stellar-dev",
        is_pr=True,
        state="open",
        bounty_xlm=75.0,
        contributor_stellar_address="GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H",
        bounty_status="escrowed",
    )
    db_session.add(issue)
    db_session.commit()

    payout_resp = client.post(
        f"/api/issues/{issue.id}/bounty/payout",
        json={
            "contributor_stellar_address": "GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H",
        },
    )
    assert payout_resp.status_code == 200
    payout_data = payout_resp.json()
    assert payout_data["issue_id"] == issue.id
    assert payout_data["github_issue_id"] == 1088
    assert payout_data["bounty_xlm"] == 75.0
    assert payout_data["bounty_status"] == "paid"
    assert payout_data["memo"] == "IssueLens-Bounty-#1088"
    assert len(payout_data["stellar_tx_hash"]) == 64
    assert payout_data["explorer_url"].startswith(
        "https://stellar.expert/explorer/testnet/tx/"
    )

    net_resp = client.get("/api/stellar/network-status")
    assert net_resp.status_code == 200
    net_data = net_resp.json()
    assert net_data["network"] == "TESTNET"
    assert net_data["horizon_url"] == "https://horizon-testnet.stellar.org"




import hmac
import hashlib
import json

def test_github_webhook_hmac_verification(client):
    secret = "demo-webhook-secret-token"
    payload = {
        "action": "opened",
        "issue": {
            "number": 999,
            "title": "Webhook test issue for automated triage",
            "body": "This is a test issue payload for IssueLens automated webhook ingestion.",
            "user": {"login": "stellar-dev"}
        },
        "repository": {
            "owner": {"login": "stellar"},
            "name": "stellar-soroban-env"
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    
    # Test 1: Missing signature -> 401
    resp_no_sig = client.post("/api/webhooks/github", content=body_bytes)
    assert resp_no_sig.status_code == 401

    # Test 2: Invalid signature -> 401
    resp_bad_sig = client.post("/api/webhooks/github", content=body_bytes, headers={"X-Hub-Signature-256": "sha256=invalidhex"})
    assert resp_bad_sig.status_code == 401

    # Test 3: Valid signature -> 200 Accepted
    mac = hmac.new(secret.encode("utf-8"), msg=body_bytes, digestmod=hashlib.sha256)
    valid_sig = f"sha256={mac.hexdigest()}"
    resp_valid = client.post("/api/webhooks/github", content=body_bytes, headers={"X-Hub-Signature-256": valid_sig, "X-GitHub-Event": "issues"})
    assert resp_valid.status_code == 200
    assert resp_valid.json()["status"] == "accepted"
