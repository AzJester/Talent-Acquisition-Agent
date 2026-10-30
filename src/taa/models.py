"""Domain models: candidate profiles, rubrics and scores."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Position(BaseModel):
    title: str = ""
    company: str = ""
    start: str | None = None  # "YYYY" or "YYYY-MM"
    end: str | None = None  # None means current
    description: str = ""

    @property
    def is_current(self) -> bool:
        return not self.end or self.end.strip().lower() in {"present", "current", "now"}


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    field: str = ""
    start: str | None = None
    end: str | None = None


class Profile(BaseModel):
    """One candidate record, normalized across sources.

    Identity fields (name, location, contact details, education) are shown to recruiters
    but masked before any scoring (see pipeline/mask.py)."""

    source: str
    source_id: str = ""
    full_name: str = ""
    headline: str = ""
    summary: str = ""
    location: str = ""
    positions: list[Position] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    clearance: str = ""  # self-reported text, never verified
    linkedin_url: str = ""
    email: str = ""
    other_urls: list[str] = Field(default_factory=list)
    fetched_at: datetime | None = None
    sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_sources(self) -> "Profile":
        if not self.sources:
            self.sources = [self.source]
        return self

    def current_positions(self) -> list[Position]:
        return [p for p in self.positions if p.is_current]

    def current_employers(self) -> list[str]:
        return [p.company for p in self.current_positions() if p.company]


_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class Criterion(BaseModel):
    id: str
    name: str
    short: str = ""  # table header label
    type: Literal["requirement", "desirement"]
    weight: float = Field(gt=0)
    guidance: str = ""
    signals: list[str] = Field(default_factory=list)  # capability ids from the signal dictionary
    keywords: list[str] = Field(default_factory=list)
    heuristic: Literal["team_size"] | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("criterion id must be snake_case, starting with a letter")
        return v

    @property
    def label(self) -> str:
        return (self.short or self.id.replace("_", " ")).upper()


class Gates(BaseModel):
    locations: list[str] = Field(default_factory=list)  # substrings; empty means any location
    allow_unknown_location: bool = True


class Rubric(BaseModel):
    id: str
    version: int = 1
    title: str
    summary: str = ""
    role_pitch: str = ""  # one or two sentences recruiters can reuse in outreach
    target_titles: list[str] = Field(default_factory=list)
    target_companies: list[str] = Field(default_factory=list)
    gates: Gates = Field(default_factory=Gates)
    criteria: list[Criterion]

    @model_validator(mode="after")
    def _unique_ids(self) -> "Rubric":
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion ids must be unique")
        if not ids:
            raise ValueError("a rubric needs at least one criterion")
        return self

    @property
    def total_weight(self) -> float:
        return sum(c.weight for c in self.criteria)

    def criterion(self, cid: str) -> Criterion | None:
        return next((c for c in self.criteria if c.id == cid), None)


class CriterionScore(BaseModel):
    criterion_id: str
    score: int | None = None  # 0 to 4; None means unknown (no evidence)
    evidence: list[str] = Field(default_factory=list)  # verified quotes from the masked profile text
    rationale: str = ""
    removed_quotes: int = 0  # quotes dropped because they were not found in the profile

    @property
    def unknown(self) -> bool:
        return self.score is None


class ScoreCard(BaseModel):
    """Everything the scorer produced for one candidate."""

    scores: list[CriterionScore]
    summary: str = ""
    scorer: str  # "heuristic" or "llm:<model>"
    notes: list[str] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)

    def get(self, cid: str) -> CriterionScore | None:
        return next((s for s in self.scores if s.criterion_id == cid), None)


class RankedCandidate(BaseModel):
    key: str
    profile: Profile
    card: ScoreCard
    total: float  # 0 to 100
    coverage: float  # share of rubric weight with evidence, 0 to 1
    requirement_unknowns: int
    flags: list[str] = Field(default_factory=list)
    rank: int = 0


class Exclusion(BaseModel):
    key: str
    profile: Profile
    reason: str
