"""Build the masked view of a profile that scorers see.

The scorer never sees the candidate's name, contact details, location, schools or
graduation years. Graduation year is an age proxy and school names can proxy for other
protected characteristics. Every evidence quote must be a substring of `MaskedProfile.text`,
which is how quotes are verified."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from taa.models import Profile

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"(?:https?://|www\.)\S+|\blinkedin\.com/\S+", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")


def scrub(text: str, names: list[str]) -> str:
    if not text:
        return ""
    out = _EMAIL.sub("[EMAIL]", text)
    out = _URL.sub("[URL]", out)
    out = _PHONE.sub("[PHONE]", out)
    for name in sorted({n for n in names if len(n) >= 3}, key=len, reverse=True):
        out = re.sub(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", "[CANDIDATE]", out, flags=re.I)
    return out


def _name_parts(full_name: str) -> list[str]:
    full = full_name.strip()
    if not full:
        return []
    return [full] + [p.strip(",.") for p in full.split() if len(p.strip(",.")) >= 3]


@dataclass
class MaskedPosition:
    index: int
    title: str
    company: str
    header: str
    description: str


@dataclass
class MaskedProfile:
    headline: str
    summary: str
    clearance: str
    skills: list[str]
    positions: list[MaskedPosition] = field(default_factory=list)
    text: str = ""

    def sections(self) -> list[str]:
        """Free-text sections searched for keyword evidence, in display order."""
        parts = [self.headline, self.summary, self.clearance]
        for p in self.positions:
            parts.extend([p.header, p.description])
        parts.extend(self.skills)
        return [s for s in parts if s]


def masked_view(profile: Profile) -> MaskedProfile:
    names = _name_parts(profile.full_name)
    s = lambda t: scrub(t, names).strip()  # noqa: E731
    positions = []
    for i, p in enumerate(profile.positions, 1):
        title, company = s(p.title), s(p.company)
        header = f"{title} | {company} | {p.start or 'unknown'} to {p.end or 'present'}"
        positions.append(MaskedPosition(i, title, company, header, s(p.description)))
    mp = MaskedProfile(
        headline=s(profile.headline),
        summary=s(profile.summary),
        clearance=s(profile.clearance),
        skills=[s(x) for x in profile.skills if x.strip()],
        positions=positions,
    )
    lines = []
    if mp.headline:
        lines.append(f"HEADLINE: {mp.headline}")
    if mp.summary:
        lines.append(f"SUMMARY: {mp.summary}")
    if mp.clearance:
        lines.append(f"CLEARANCE (SELF-REPORTED, UNVERIFIED): {mp.clearance}")
    if mp.skills:
        lines.append("SKILLS: " + "; ".join(mp.skills))
    if positions:
        lines.append("EXPERIENCE:")
        for p in positions:
            lines.append(f"[{p.index}] {p.header}")
            if p.description:
                lines.append(p.description)
    mp.text = "\n".join(lines)
    return mp
