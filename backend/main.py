from contextlib import asynccontextmanager
from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database import Issue, Repository, SessionLocal, TriageReport, get_db, init_db
from github_service import post_issue_comment, sync_repository_issues
from models import (
    EscrowLockRequest,
    EscrowLockResponse,
    BountyPayoutRequest,
    BountyPayoutResponse,
    IssueRead,
    IssueReplyRequest,
    IssueReplyResponse,
    RepoSyncRequest,
    RepoSyncResponse,
    RepositoryRead,
    StellarNetworkStatusResponse,
    TriageReportRead,
    calculate_bounty_xlm,
)
from stellar_service import (
    DEMO_ESCROW_PUBLIC_KEY,
    check_testnet_network_status,
    submit_bounty_payout,
)
from triage_service import run_triage_for_issue


def _seed_demo_data_if_empty() -> None:
    with SessionLocal() as db:
        existing = db.scalar(select(Repository.id).limit(1))
        if existing is not None:
            return

        repo_soroban = Repository(owner="stellar", name="stellar-soroban-env")
        repo_fastapi = Repository(owner="fastapi", name="fastapi")
        db.add_all([repo_soroban, repo_fastapi])
        db.flush()

        issue1 = Issue(
            repo_id=repo_soroban.id,
            github_issue_id=1428,
            title="HostError(Budget, ExceededLimit) during cross-contract Vec<Val> deep clone under Protocol 21",
            body=(
                "## Summary\n\n"
                "When invoking a cross-contract call via `Env::invoke_contract` that returns a nested `Vec<Val>` "
                "containing >128 `BytesN<32>` elements, the host traps with `HostError(Budget, ExceededLimit)` "
                "even though instruction metering shows <35% CPU budget consumption prior to return value marshaling.\n\n"
                "## Observed Trace\n"
                "```rust\n"
                "HostError: Error(Budget, ExceededLimit)\n"
                "  at soroban_env_host::budget::Budget::charge (budget.rs:412)\n"
                "  at soroban_env_host::host::metered_clone::MeteredClone::metered_clone (metered_clone.rs:119)\n"
                "```"
            ),
            author="kaspar-v",
            is_pr=False,
            state="open",
            bounty_xlm=150.0,
            contributor_stellar_address="GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H",
            bounty_status="escrowed",
            stellar_tx_hash=None,
        )
        issue2 = Issue(
            repo_id=repo_soroban.id,
            github_issue_id=1431,
            title="feat(vm): enforce metered linear memory growth bounds in Vm::instantiate_from_wasm",
            body=(
                "## Summary\n\n"
                "Pre-charges initial Wasm linear memory pages before host module linking inside `Vm::instantiate_from_wasm` "
                "and tightens the exported signature to require an explicit `ContractCostParams` reference.\n\n"
                "```diff\n"
                "diff --git a/soroban-env-host/src/vm.rs b/soroban-env-host/src/vm.rs\n"
                "--- a/soroban-env-host/src/vm.rs\n"
                "+++ b/soroban-env-host/src/vm.rs\n"
                "-pub fn instantiate_from_wasm(host: &Host, wasm_bytes: &[u8]) -> Result<Rc<Vm>, HostError> {\n"
                "+pub fn instantiate_from_wasm(host: &Host, wasm_bytes: &[u8], cost_params: &ContractCostParams) -> Result<Rc<Vm>, HostError> {\n"
                "+    host.charge_budget(ContractCostType::VmMemAlloc, Some(wasm_bytes.len() as u64))?;\n"
                "     let engine = Vm::new_engine(host.budget_ref())?;\n"
                "     Vm::from_parsed_module(host, engine, wasm_bytes)\n"
                " }\n"
                "```"
            ),
            author="graydon-s",
            is_pr=True,
            state="open",
            diff=(
                "diff --git a/soroban-env-host/src/vm.rs b/soroban-env-host/src/vm.rs\n"
                "--- a/soroban-env-host/src/vm.rs\n"
                "+++ b/soroban-env-host/src/vm.rs\n"
                "-pub fn instantiate_from_wasm(host: &Host, wasm_bytes: &[u8]) -> Result<Rc<Vm>, HostError> {\n"
                "+pub fn instantiate_from_wasm(host: &Host, wasm_bytes: &[u8], cost_params: &ContractCostParams) -> Result<Rc<Vm>, HostError> {\n"
            ),
            bounty_xlm=200.0,
            contributor_stellar_address="GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN",
            bounty_status="escrowed",
            stellar_tx_hash=None,
        )
        issue3 = Issue(
            repo_id=repo_soroban.id,
            github_issue_id=1435,
            title="fix(crypto): constant-time subgroup check in bls12_381_g1_msm host function",
            body=(
                "## Summary\n\n"
                "Ensures every G1 affine point passed into `bls12_381_g1_msm` undergoes constant-time subgroup "
                "membership verification prior to multi-scalar multiplication.\n\n"
                "```diff\n"
                "diff --git a/soroban-env-host/src/test/bls12_381.rs b/soroban-env-host/src/test/bls12_381.rs\n"
                "+++ b/soroban-env-host/src/test/bls12_381.rs\n"
                "+#[test]\n"
                "+fn test_bls12_381_g1_msm_rejects_non_subgroup_points() -> Result<(), HostError> {\n"
                "+    Ok(())\n"
                "+}\n"
                "```"
            ),
            author="sidharth-kr",
            is_pr=True,
            state="open",
            diff=(
                "diff --git a/soroban-env-host/src/test/bls12_381.rs b/soroban-env-host/src/test/bls12_381.rs\n"
                "+++ b/soroban-env-host/src/test/bls12_381.rs\n"
            ),
            bounty_xlm=150.0,
            contributor_stellar_address="GCEZWKCA5VLDNRLN3RPRJMRZOX3Z6G5CHCGSNFHEYVXM3XOJMDS674JZ",
            bounty_status="escrowed",
            stellar_tx_hash=None,
        )
        issue4 = Issue(
            repo_id=repo_fastapi.id,
            github_issue_id=11402,
            title="Annotated Depends() cache key collision when sub-dependency uses callable class instance",
            body=(
                "## Description\n\n"
                "When two route parameters declare `Annotated[ServiceClient, Depends(RateLimitedClient(rpm=60))]` "
                "and `Annotated[ServiceClient, Depends(RateLimitedClient(rpm=300))]`, FastAPI's request-scoped "
                "dependency cache keys solely on `call.__class__` in certain async generator sub-trees."
            ),
            author="SebasDev",
            is_pr=False,
            state="open",
            bounty_xlm=100.0,
            contributor_stellar_address="GD5DJQDDBKGAYNEAXU562HYGOOSYAEOO6AS53PZXBOZGCP5M2OPGMZV3",
            bounty_status="escrowed",
            stellar_tx_hash=None,
        )
        issue5 = Issue(
            repo_id=repo_soroban.id,
            github_issue_id=1419,
            title="perf(storage): batch footprint TTL extension ledger writes for persistent contract entries",
            body=(
                "## Summary\n\n"
                "Coalesces consecutive `extend_contract_data_ttl` host ledger mutations within the same transaction "
                "footprint into a single sorted batch write pass at host finalization.\n\n"
                "```diff\n"
                "diff --git a/soroban-env-host/src/test/storage_ttl_test.rs b/soroban-env-host/src/test/storage_ttl_test.rs\n"
                "+++ b/soroban-env-host/src/test/storage_ttl_test.rs\n"
                "+#[test]\n"
                "+fn test_batched_ttl_extension_coalesces_duplicate_keys() {}\n"
                "```"
            ),
            author="tomer-stellar",
            is_pr=True,
            state="open",
            diff=(
                "diff --git a/soroban-env-host/src/test/storage_ttl_test.rs b/soroban-env-host/src/test/storage_ttl_test.rs\n"
                "+++ b/soroban-env-host/src/test/storage_ttl_test.rs\n"
            ),
            bounty_xlm=200.0,
            contributor_stellar_address="GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H",
            bounty_status="paid",
            stellar_tx_hash="8f4e2b9c71d04a6e3b58c19f20a4d7e6b3c8a1f09e2d4b7c6a5f8e1d2c3b4a59",
        )

        db.add_all([issue1, issue2, issue3, issue4, issue5])
        db.flush()

        db.add_all(
            [
                TriageReport(
                    issue_id=issue1.id,
                    needs_repro=True,
                    is_breaking=False,
                    suggested_tags=["needs-repro", "budget-metering", "protocol-21"],
                    ai_summary=(
                        "Reports nonlinear memory/CPU charge amplification in `MeteredClone` when marshaling nested "
                        "`Vec<Val>` across contract frames. Missing a minimal `.wasm` fixture or self-contained `Host` unit test."
                    ),
                    drafted_reply=(
                        "Thanks for the detailed stack trace @kaspar-v.\n\n"
                        "To isolate whether `MeteredClone` is double-charging linear memory bytes on cross-contract return values, "
                        "could you attach:\n"
                        "1. A minimal `#[test]` harness using `Host::test_host_with_recording_footprint()`\n"
                        "2. The exact `CostType` entry in `Budget` that trips `ExceededLimit`\n\n"
                        "Once a minimal repro is attached, we will unlock the **150 XLM** bounty tier for a patch."
                    ),
                    complexity="Medium",
                    bounty_xlm=150.0,
                ),
                TriageReport(
                    issue_id=issue2.id,
                    needs_repro=False,
                    is_breaking=True,
                    suggested_tags=["missing-tests", "breaking-api", "soroban-vm"],
                    ai_summary=(
                        "Modifies public `Vm::instantiate_from_wasm` signature (breaking downstream embedders) and alters "
                        "`VmMemAlloc` budget charging order without regression unit tests in `soroban-env-host/src/test/budget.rs`."
                    ),
                    drafted_reply=(
                        "Thanks for the PR @graydon-s. Automated triage flagged two blockers before merge & bounty settlement:\n\n"
                        "1. **Missing Unit Tests (`missing-tests`)**: Please add a test in `soroban-env-host/src/test/budget.rs`.\n"
                        "2. **Public API Compatibility**: Keep `Vm::instantiate_from_wasm(host, wasm_bytes)` intact."
                    ),
                    complexity="High",
                    bounty_xlm=200.0,
                ),
                TriageReport(
                    issue_id=issue3.id,
                    needs_repro=False,
                    is_breaking=False,
                    suggested_tags=["ready", "crypto-bls12-381", "security-verified"],
                    ai_summary=(
                        "Clean, non-breaking cryptographic hardening patch for `bls12_381_g1_msm`. Includes negative subgroup "
                        "test vectors in `test/bls12_381.rs` and preserves host interface stability."
                    ),
                    drafted_reply=(
                        "LGTM @sidharth-kr — subgroup verification placement prior to `G1Projective::msm_unchecked` is clean "
                        "and the new negative test vectors in `test/bls12_381.rs` cover small-cofactor points.\n\n"
                        "Approving for merge and releasing the **150 XLM** bounty via Stellar Horizon Testnet."
                    ),
                    complexity="Medium",
                    bounty_xlm=150.0,
                ),
                TriageReport(
                    issue_id=issue4.id,
                    needs_repro=True,
                    is_breaking=False,
                    suggested_tags=["needs-repro", "missing-tests", "dependency-injection"],
                    ai_summary=(
                        "Describes a plausible cache key collision in `fastapi.dependencies.utils.get_flat_dependant`, "
                        "but lacks a runnable `TestClient` reproduction script showing how `__hash__` is implemented."
                    ),
                    drafted_reply=(
                        "Hi @SebasDev, thanks for the report.\n\n"
                        "Could you share a self-contained 20-line snippet showing the definition of `RateLimitedClient` "
                        "(specifically whether `__hash__` and `__eq__` are defined)?"
                    ),
                    complexity="Low",
                    bounty_xlm=100.0,
                ),
                TriageReport(
                    issue_id=issue5.id,
                    needs_repro=False,
                    is_breaking=False,
                    suggested_tags=["ready", "storage-footprint", "perf"],
                    ai_summary=(
                        "High-impact storage footprint optimization with deterministic BTreeMap batch ordering and "
                        "comprehensive regression tests in `storage_ttl_test.rs`. Verified and settled on Stellar Testnet."
                    ),
                    drafted_reply=(
                        "Verified and merged @tomer-stellar!\n\n"
                        "The `BTreeMap` key ordering guarantees deterministic footprint emission across validators. "
                        "The **200 XLM** bounty has been settled on Stellar Testnet Horizon."
                    ),
                    complexity="High",
                    bounty_xlm=200.0,
                ),
            ]
        )
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    _seed_demo_data_if_empty()
    yield


app = FastAPI(
    title="IssueLens API",
    description="AI-Powered Issue & PR Triage Dashboard with Stellar XLM Bounties & Drips Ecosystem Integration",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "issuelens-backend"}


@app.get("/api/stellar/network-status", response_model=StellarNetworkStatusResponse)
async def stellar_network_status_endpoint() -> StellarNetworkStatusResponse:
    status_info = await check_testnet_network_status()
    return StellarNetworkStatusResponse(**status_info)


@app.post("/api/repos/sync", response_model=RepoSyncResponse)
async def sync_repo(
    payload: RepoSyncRequest,
    db: Session = Depends(get_db),
) -> RepoSyncResponse:
    try:
        repo, synced_issues = await sync_repository_issues(
            db=db,
            repo_url=payload.repo_url,
            limit=20,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"GitHub API error ({exc.response.status_code}) while syncing '{payload.repo_url}'",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to communicate with GitHub API: {exc}",
        ) from exc

    return RepoSyncResponse(
        repository=repo,
        synced_count=len(synced_issues),
        issues=synced_issues,
    )


@app.get("/api/repos", response_model=list[RepositoryRead])
def list_repositories(db: Session = Depends(get_db)) -> list[Repository]:
    stmt = select(Repository).order_by(Repository.scanned_at.desc(), Repository.id.desc())
    return list(db.scalars(stmt).all())


@app.get("/api/repos/{repo_id}/issues", response_model=list[IssueRead])
def list_repository_issues(repo_id: int, db: Session = Depends(get_db)) -> list[Issue]:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repository {repo_id} not found")
    stmt = (
        select(Issue)
        .options(selectinload(Issue.triage_report), selectinload(Issue.repository))
        .where(Issue.repo_id == repo_id)
        .order_by(Issue.created_at.desc(), Issue.id.desc())
    )
    return list(db.scalars(stmt).all())


@app.get("/api/issues", response_model=list[IssueRead])
def list_issues(
    repo_id: int | None = None,
    is_pr: bool | None = None,
    db: Session = Depends(get_db),
) -> list[Issue]:
    stmt = (
        select(Issue)
        .options(selectinload(Issue.triage_report), selectinload(Issue.repository))
        .order_by(Issue.created_at.desc(), Issue.id.desc())
    )
    if repo_id is not None:
        stmt = stmt.where(Issue.repo_id == repo_id)
    if is_pr is not None:
        stmt = stmt.where(Issue.is_pr == is_pr)
    return list(db.scalars(stmt).all())


@app.post("/api/issues/{id}/triage", response_model=TriageReportRead)
def triage_issue_endpoint(
    id: int,
    db: Session = Depends(get_db),
) -> TriageReport:
    issue = db.scalar(
        select(Issue)
        .options(selectinload(Issue.triage_report), selectinload(Issue.repository))
        .where(Issue.id == id)
    )
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {id} not found")

    report = run_triage_for_issue(db=db, issue=issue)
    return report


@app.post("/api/issues/{issue_id}/bounty/payout", response_model=BountyPayoutResponse)
def payout_issue_bounty_endpoint(
    issue_id: int,
    payload: BountyPayoutRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> BountyPayoutResponse:
    issue = db.scalar(
        select(Issue)
        .options(selectinload(Issue.triage_report), selectinload(Issue.repository))
        .where(Issue.id == issue_id)
    )
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {issue_id} not found")

    destination = (
        (payload.contributor_stellar_address if payload and payload.contributor_stellar_address else None)
        or issue.contributor_stellar_address
        or DEMO_ESCROW_PUBLIC_KEY
    )

    if payload and payload.bounty_xlm is not None:
        amount_xlm = float(payload.bounty_xlm)
    elif issue.bounty_xlm and issue.bounty_xlm > 0:
        amount_xlm = float(issue.bounty_xlm)
    else:
        _, amount_xlm = calculate_bounty_xlm(
            issue.triage_report.complexity if issue.triage_report else None,
            is_breaking=bool(issue.triage_report.is_breaking) if issue.triage_report else False,
            needs_repro=bool(issue.triage_report.needs_repro) if issue.triage_report else False,
            suggested_tags=issue.triage_report.suggested_tags if issue.triage_report else None,
            title=issue.title or "",
            body=issue.body or "",
            diff=issue.diff,
        )

    try:
        payout_result = submit_bounty_payout(
            contributor_stellar_address=destination,
            amount_xlm=amount_xlm,
            issue_number=issue.github_issue_id,
            escrow_secret=payload.escrow_secret if payload else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    issue.contributor_stellar_address = payout_result["destination"]
    issue.bounty_xlm = payout_result["amount_xlm"]
    issue.bounty_status = "paid"
    issue.stellar_tx_hash = payout_result["tx_hash"]
    if issue.triage_report is not None:
        issue.triage_report.bounty_xlm = payout_result["amount_xlm"]

    db.commit()
    db.refresh(issue)

    return BountyPayoutResponse(
        issue_id=issue.id,
        github_issue_id=issue.github_issue_id,
        bounty_xlm=issue.bounty_xlm,
        contributor_stellar_address=issue.contributor_stellar_address,
        bounty_status="paid",
        stellar_tx_hash=payout_result["tx_hash"],
        tx_hash=payout_result["tx_hash"],
        explorer_url=payout_result["explorer_url"],
        horizon_tx_url=payout_result["horizon_tx_url"],
        memo=payout_result["memo"],
        network=payout_result["network"],
        simulated=payout_result["simulated"],
        mode=payout_result["mode"],
    )


@app.post("/api/issues/{id}/reply", response_model=IssueReplyResponse)
async def reply_to_issue_endpoint(
    id: int,
    payload: IssueReplyRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> IssueReplyResponse:
    issue = db.scalar(
        select(Issue)
        .options(selectinload(Issue.triage_report), selectinload(Issue.repository))
        .where(Issue.id == id)
    )
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {id} not found")

    if issue.repository is None:
        raise HTTPException(status_code=400, detail=f"Repository for issue {id} not found")

    custom_body = (payload.body or payload.reply) if payload else None
    reply_text = (custom_body or (issue.triage_report.drafted_reply if issue.triage_report else "") or "").strip()

    if not reply_text:
        raise HTTPException(
            status_code=400,
            detail="No drafted reply available for this issue. Run POST /api/issues/{id}/triage first or provide a reply body.",
        )

    try:
        gh_comment = await post_issue_comment(
            owner=issue.repository.owner,
            name=issue.repository.name,
            github_issue_id=issue.github_issue_id,
            body=reply_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"GitHub API error ({exc.response.status_code}) while posting comment",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to communicate with GitHub API: {exc}",
        ) from exc

    if issue.triage_report and custom_body:
        issue.triage_report.drafted_reply = reply_text
        db.commit()

    return IssueReplyResponse(
        issue_id=issue.id,
        github_issue_id=issue.github_issue_id,
        posted_body=reply_text,
        comment_url=gh_comment.get("html_url"),
        github_comment_id=gh_comment.get("id"),
    )



@app.post("/api/issues/{id}/bounty/lock", response_model=EscrowLockResponse)
async def lock_bounty_in_escrow_endpoint(
    id: int,
    payload: EscrowLockRequest | None = Body(default=None),
    db: Session = Depends(get_db)
) -> EscrowLockResponse:
    issue = db.scalar(select(Issue).where(Issue.id == id))
    if not issue:
        raise HTTPException(status_code=404, detail=f"Issue {id} not found")

    amount = payload.amount_xlm if (payload and payload.amount_xlm) else issue.bounty_xlm
    contract_id = (payload.escrow_contract_id if payload else None) or "CA76M4CWXMI5ZWTF2H6PVI6KGV323HCBIM6G6EHFNRFDZ2G2WPPWARE"
    
    from stellar_service import build_unsigned_escrow_lock_xdr
    lock_result = build_unsigned_escrow_lock_xdr(
        source_public_key=issue.contributor_stellar_address or DEMO_ESCROW_PUBLIC_KEY,
        escrow_contract_id=contract_id,
        amount_xlm=amount,
        issue_number=issue.github_issue_id
    )

    issue.escrow_contract_id = contract_id
    issue.escrow_status = "locked"
    issue.bounty_status = "escrowed"
    issue.stellar_tx_hash = payload.tx_hash if (payload and payload.tx_hash) else lock_result["tx_hash"]
    db.commit()

    return EscrowLockResponse(
        issue_id=issue.id,
        github_issue_id=issue.github_issue_id,
        escrow_contract_id=contract_id,
        escrow_status="locked",
        amount_xlm=float(amount),
        tx_hash=issue.stellar_tx_hash,
        unsigned_xdr=lock_result["unsigned_xdr"],
        explorer_url=lock_result["explorer_url"]
    )
