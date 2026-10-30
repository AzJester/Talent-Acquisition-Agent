"""Entity resolution: one person, many records."""
from __future__ import annotations

import re

from taa.models import Position, Profile


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def normalize_linkedin(url: str) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www\.|[a-z]{2}\.)", "", u)
    u = u.split("?")[0].rstrip("/")
    return u if "linkedin.com/" in u else ""


def candidate_key(p: Profile) -> str:
    li = normalize_linkedin(p.linkedin_url)
    if li:
        return "li:" + li
    if p.email.strip():
        return "em:" + p.email.strip().lower()
    company = p.current_employers()[0] if p.current_employers() else (p.positions[0].company if p.positions else "")
    if p.full_name.strip():
        return "nc:" + _norm(p.full_name) + "|" + _norm(company)
    return f"src:{p.source}:{p.source_id}"


def _same_position(a: Position, b: Position) -> bool:
    if _norm(a.title) != _norm(b.title) or _norm(a.company) != _norm(b.company):
        return False
    return not a.start or not b.start or a.start[:4] == b.start[:4]


def merge(a: Profile, b: Profile) -> Profile:
    positions = list(a.positions)
    for p in b.positions:
        i = next((i for i, cur in enumerate(positions) if _same_position(cur, p)), None)
        if i is None:
            positions.append(p)
            continue
        cur = positions[i]
        positions[i] = cur.model_copy(update={
            "start": cur.start or p.start,
            "end": cur.end if cur.start else p.end,
            "description": p.description if len(p.description) > len(cur.description) else cur.description,
        })
    positions.sort(key=lambda p: (p.is_current, p.start or ""), reverse=True)
    pick = lambda x, y: x if len(x or "") >= len(y or "") else y  # noqa: E731
    skills = list(dict.fromkeys(a.skills + b.skills))
    fetched = max([d for d in (a.fetched_at, b.fetched_at) if d], default=None)
    return Profile(
        source=a.source if a.source == b.source else f"{a.source}+{b.source}",
        source_id=a.source_id or b.source_id,
        full_name=pick(a.full_name, b.full_name),
        headline=pick(a.headline, b.headline),
        summary=pick(a.summary, b.summary),
        location=pick(a.location, b.location),
        positions=positions,
        education=a.education or b.education,
        skills=skills,
        clearance=pick(a.clearance, b.clearance),
        linkedin_url=a.linkedin_url or b.linkedin_url,
        email=a.email or b.email,
        other_urls=list(dict.fromkeys(a.other_urls + b.other_urls)),
        fetched_at=fetched,
        sources=list(dict.fromkeys(a.sources + b.sources)),
    )


def resolve(profiles: list[Profile]) -> dict[str, Profile]:
    """Merge records that refer to the same person. Returns {key: profile}."""
    out: dict[str, Profile] = {}
    alias: dict[str, str] = {}
    for p in profiles:
        keys = [candidate_key(p)]
        li, em = normalize_linkedin(p.linkedin_url), p.email.strip().lower()
        if li:
            keys.append("li:" + li)
        if em:
            keys.append("em:" + em)
        if p.full_name.strip() and p.current_employers():
            keys.append("nc:" + _norm(p.full_name) + "|" + _norm(p.current_employers()[0]))
        existing = next((alias[k] for k in keys if k in alias), None)
        if existing:
            out[existing] = merge(out[existing], p)
            target = existing
        else:
            target = keys[0]
            out[target] = p
        for k in keys:
            alias.setdefault(k, target)
    return out
