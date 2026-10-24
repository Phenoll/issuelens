from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ComplexityLevel = Literal["Low", "Medium", "High"]
BountyStatusType = Literal["unfunded", "escrowed", "locked", "released", "refunded", "paid"]

COMPLEXITY_BOUNTY_XLM: dict[str, float] = {
    "Low": 25.0,
    "Medium": 75.0,
    "High": 150.0,
}

VALID_BOUNTY_STATUSES: tuple[str, ...] = ("unfunded", "escrowed", "locked", "released", "refunded", "paid")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def calculate_bounty_xlm(
    complexity: str | None = None,
    *,
    is_breaking: bool = False,
    needs_repro: bool = False,
    suggested_tags: list[str] | None = None,
    title: str = "",
    body: str = "",
    diff: str | None = None,
) -> tuple[ComplexityLevel, float]:
    """
    Auto-calculate complexity ('Low' | 'Medium' | 'High') and Stellar XLM bounty
    (Low=25 XLM, Medium=75 XLM, High=150 XLM).
    """
    if complexity:
        normalized = complexity.strip().capitalize()
        if normalized in COMPLEXITY_BOUNTY_XLM:
            level: ComplexityLevel = normalized  # type: ignore[assignment]
            return level, COMPLEXITY_BOUNTY_XLM[level]

    tags = [t.lower() for t in (suggested_tags or [])]
    combined = f"{title}\n{body}\n{diff or ''}".lower()

    if is_breaking or any(
        kw in combined
        for kw in (
            "breaking",
            "security",
            "consensus",
            "hydration",
            "scheduler",
            "concurrency",
            "memory leak",
            "race condition",
            "architecture",
        )
    ):
        return "High", COMPLEXITY_BOUNTY_XLM["High"]

    if "good-first-issue" in tags or any(
        kw in combined
        for kw in ("typo", "docs", "documentation", "good first issue", "badge", "minor", "readme")
    ):
        return "Low", COMPLEXITY_BOUNTY_XLM["Low"]

    if needs_repro and "bug" in tags:
        return "Medium", COMPLEXITY_BOUNTY_XLM["Medium"]

    return "Medium", COMPLEXITY_BOUNTY_XLM["Medium"]


class Base(DeclarativeBase):
    """SQLAlchemy 2.x Declarative Base."""

    pass


class Repository(Base):
    """Tracked GitHub repository."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=True
    )

    issues: Mapped[list["Issue"]] = relationship(
        "Issue",
        back_populates="repository",
        cascade="all, delete-orphan",
    )


class Issue(Base):
    """GitHub Issue or Pull Request belonging to a Repository with Stellar XLM bounty tracking."""

    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    github_issue_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    is_pr: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state: Mapped[str] = mapped_column(String(50), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Supplemental fields storing raw PR diff and issue comments from GitHub sync
    diff: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    comments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True, default=list)

    # Stellar & Drips Bounty Ecosystem Fields
    bounty_xlm: Mapped[float] = mapped_column(Float, default=25.0, nullable=False)
    contributor_stellar_address: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    bounty_status: Mapped[str] = mapped_column(
        String(32), default="unfunded", nullable=False
    )
    stellar_tx_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None
    )
    escrow_contract_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    escrow_status: Mapped[str] = mapped_column(
        String(32), default="unfunded", nullable=False
    )

    repository: Mapped["Repository"] = relationship("Repository", back_populates="issues")
    triage_report: Mapped["TriageReport | None"] = relationship(
        "TriageReport",
        back_populates="issue",
        uselist=False,
        cascade="all, delete-orphan",
    )


class TriageReport(Base):
    """AI-generated triage report and complexity-based Stellar bounty estimation."""

    __tablename__ = "triage_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    needs_repro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_breaking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suggested_tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    drafted_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    complexity: Mapped[str] = mapped_column(String(32), default="Medium", nullable=False)
    bounty_xlm: Mapped[float] = mapped_column(Float, default=75.0, nullable=False)

    issue: Mapped["Issue"] = relationship("Issue", back_populates="triage_report")


# ---------------------------------------------------------------------------
# Pydantic v2 Request / Response Schemas
# ---------------------------------------------------------------------------


class RepoSyncRequest(BaseModel):
    repo_url: str = Field(
        ...,
        min_length=3,
        examples=["https://github.com/fastapi/fastapi", "pallets/flask"],
        description="GitHub repository URL or 'owner/name' identifier.",
    )


class IssueReplyRequest(BaseModel):
    body: str | None = Field(
        default=None,
        description="Optional override for the comment body. Defaults to TriageReport.drafted_reply.",
    )
    reply: str | None = Field(
        default=None,
        description="Optional alias for the comment body.",
    )


class IssueReplyResponse(BaseModel):
    issue_id: int
    github_issue_id: int
    posted_body: str
    comment_url: str | None = None
    github_comment_id: int | None = None


class BountyPayoutRequest(BaseModel):
    contributor_stellar_address: str | None = Field(
        default=None,
        description="Contributor's Stellar 'G...' wallet address. Falls back to issue.contributor_stellar_address.",
    )
    bounty_xlm: float | None = Field(
        default=None,
        description="Optional XLM bounty override. Defaults to issue.bounty_xlm (Low=25, Medium=75, High=150).",
    )
    escrow_secret: str | None = Field(
        default=None,
        description="Optional Stellar Testnet 'S...' secret seed. Uses demo-simulation mode if omitted.",
    )


class BountyPayoutResponse(BaseModel):
    issue_id: int
    github_issue_id: int
    bounty_xlm: float
    contributor_stellar_address: str
    bounty_status: BountyStatusType
    stellar_tx_hash: str
    tx_hash: str
    explorer_url: str
    horizon_tx_url: str
    memo: str
    network: str = "TESTNET"
    simulated: bool
    mode: str


class StellarNetworkStatusResponse(BaseModel):
    status: str
    network: str
    horizon_url: str
    network_passphrase: str
    history_latest_ledger: int
    core_latest_ledger: int
    current_protocol_version: int
    horizon_version: str
    core_version: str
    demo_simulation_enabled: bool
    detail: str | None = None


class TriageReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_id: int
    needs_repro: bool
    is_breaking: bool
    suggested_tags: list[str] = Field(default_factory=list)
    ai_summary: str | None = None
    drafted_reply: str | None = None
    complexity: str = "Medium"
    bounty_xlm: float = 75.0


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner: str
    name: str
    scanned_at: datetime | None = None


class IssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repo_id: int
    github_issue_id: int
    title: str
    body: str | None = None
    author: str
    is_pr: bool
    state: str
    created_at: datetime
    diff: str | None = None
    comments: list[dict[str, Any]] | None = None
    bounty_xlm: float = 25.0
    contributor_stellar_address: str | None = None
    bounty_status: str = "unfunded"
    stellar_tx_hash: str | None = None
    triage_report: TriageReportRead | None = None
    repository: RepositoryRead | None = None


class RepoSyncResponse(BaseModel):
    repository: RepositoryRead
    synced_count: int
    issues: list[IssueRead]


class EscrowLockRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    escrow_contract_id: str | None = Field(default=None, description="Soroban escrow contract address (C...)")
    amount_xlm: float | None = Field(default=None, ge=1.0, description="Amount of XLM to lock in escrow")
    contributor_stellar_address: str | None = Field(default=None, description="Contributor G... public address")
    tx_hash: str | None = Field(default=None, description="Submitted on-chain transaction hash")

class EscrowLockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    issue_id: int
    github_issue_id: int
    escrow_contract_id: str | None
    escrow_status: str
    amount_xlm: float
    tx_hash: str
    unsigned_xdr: str | None = None
    explorer_url: str | None = None
