"""Hard filters and recruiter flags.

Hard filters run before any scoring. The do-not-target list (non-solicitation terms from
teaming agreements, subcontracts and NDAs) is owned by Contracts. Everything else is scored,
not gated, so an unusual but strong candidate is never silently dropped."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from taa.models import Exclusion, Profile, Rubric
from taa.signals import SignalDictionary, employer_matches


class DoNotTargetEntry(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    reason: str = "Non-solicitation obligation"
    lookback_months: int = 0  # former employees who left within this window are flagged

    @property
    def names(self) -> list[str]:
        return [self.name, *self.aliases]


class DoNotTarget(BaseModel):
    employers: list[DoNotTargetEntry] = Field(default_factory=list)


def load_do_not_target(path: Path) -> DoNotTarget:
    if not path.exists():
        return DoNotTarget()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return DoNotTarget.model_validate({"employers": data.get("employers") or []})


def _parse_ym(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?", value.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 12)


def months_since(value: str | None, now: datetime) -> int | None:
    ym = _parse_ym(value)
    if not ym:
        return None
    return (now.year - ym[0]) * 12 + (now.month - ym[1])


def apply_filters(resolved: dict[str, Profile], rubric: Rubric, dnt: DoNotTarget,
                  now: datetime | None = None) -> tuple[dict[str, list[str]], list[Exclusion]]:
    """Return ({key: flags} for kept candidates, exclusions)."""
    now = now or datetime.now(timezone.utc)
    kept: dict[str, list[str]] = {}
    excluded: list[Exclusion] = []
    gate = [g.lower() for g in rubric.gates.locations if g.strip()]
    for key, p in resolved.items():
        flags: list[str] = []
        reason = None
        for entry in dnt.employers:
            if any(employer_matches(c, entry.names) for c in p.current_employers()):
                reason = f"Do not target: current employer {entry.name}. {entry.reason}"
                break
            if entry.lookback_months:
                for pos in p.positions:
                    if pos.is_current or not employer_matches(pos.company, entry.names):
                        continue
                    ago = months_since(pos.end, now)
                    if ago is not None and ago <= entry.lookback_months:
                        flags.append(f"Left {entry.name} {ago} months ago; check non-solicit terms before contact")
        if reason is None and gate:
            loc = p.location.lower().strip()
            if not loc:
                if rubric.gates.allow_unknown_location:
                    flags.append("Location unknown; confirm against the role's location requirement")
                else:
                    reason = "Location unknown and the rubric requires a listed location"
            elif not any(g in loc for g in gate):
                reason = f"Location '{p.location}' is outside the role's locations"
        if reason:
            excluded.append(Exclusion(key=key, profile=p, reason=reason))
        else:
            kept[key] = flags
    return kept, excluded


def recruiter_flags(p: Profile, signals: SignalDictionary, stale_after_days: int,
                    now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    flags = []
    if p.clearance.strip():
        flags.append(f"Clearance self-reported, not verified: {p.clearance.strip()}")
    for c in p.current_employers():
        if employer_matches(c, signals.flags.equity_heavy_employers):
            flags.append(f"Comp risk: current employer {c} is likely equity-heavy")
            break
    if p.fetched_at:
        fetched = p.fetched_at if p.fetched_at.tzinfo else p.fetched_at.replace(tzinfo=timezone.utc)
        age = (now - fetched).days
        if age > stale_after_days:
            flags.append(f"Profile data is {age} days old; refresh before outreach")
    if len(p.sources) > 1:
        flags.append("Merged from sources: " + ", ".join(p.sources))
    return flags
