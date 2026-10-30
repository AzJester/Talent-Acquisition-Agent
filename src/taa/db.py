"""Persistence. SQLite by default; set TAA_DATABASE_URL for Postgres."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class RubricRow(Base):
    __tablename__ = "rubrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    yaml_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft, approved, retired
    origin: Mapped[str] = mapped_column(String(20), default="manual")  # template, intake, manual, edit
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SearchRow(Base):
    __tablename__ = "searches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    rubric_id: Mapped[int] = mapped_column(ForeignKey("rubrics.id"))
    rubric_yaml: Mapped[str] = mapped_column(Text)  # snapshot at search time
    rubric_version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued, running, complete, failed
    progress: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdl_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdl_size: Mapped[int] = mapped_column(Integer, default=0)
    inputs: Mapped[list["SearchInputRow"]] = relationship(back_populates="search", cascade="all, delete-orphan")
    results: Mapped[list["ResultRow"]] = relationship(back_populates="search", cascade="all, delete-orphan")


class SearchInputRow(Base):
    __tablename__ = "search_inputs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("searches.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(80))
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    profiles: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    search: Mapped[SearchRow] = relationship(back_populates="inputs")


class ResultRow(Base):
    __tablename__ = "results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("searches.id"), index=True)
    key: Mapped[str] = mapped_column(String(300))
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    requirement_unknowns: Mapped[int] = mapped_column(Integer, default=0)
    profile: Mapped[dict] = mapped_column(JSON)
    card: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    outreach_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    search: Mapped[SearchRow] = relationship(back_populates="results")
    reviews: Mapped[list["ReviewRow"]] = relationship(back_populates="result", cascade="all, delete-orphan",
                                                      order_by="ReviewRow.created_at")

    @property
    def latest_review(self) -> "ReviewRow | None":
        return self.reviews[-1] if self.reviews else None


class ReviewRow(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("results.id"), index=True)
    decision: Mapped[str] = mapped_column(String(20))  # approve, reject
    reason_code: Mapped[str] = mapped_column(String(60), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    result: Mapped[ResultRow] = relationship(back_populates="reviews")


class AuditRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(80))
    search_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


def make_session_factory(url: str) -> sessionmaker:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    if url.startswith("sqlite:///:memory:"):
        from sqlalchemy.pool import StaticPool
        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def audit(session, actor: str, action: str, search_id: int | None = None, **detail) -> None:
    session.add(AuditRow(actor=actor, action=action, search_id=search_id, detail=detail))
