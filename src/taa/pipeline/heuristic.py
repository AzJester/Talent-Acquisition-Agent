"""Deterministic scorer built on the signal dictionary.

Runs with no model access, pre-ranks candidates before LLM scoring, and serves as the
fallback when an LLM call fails. Every quote it produces is an exact substring of the
masked profile, so its evidence always verifies."""
from __future__ import annotations

import re

from taa.models import CriterionScore, Profile, Rubric, ScoreCard
from taa.pipeline.mask import MaskedProfile, masked_view
from taa.signals import STRENGTH_RANK, Matcher, find_keyword, sentence_around

_NUM = r"(\d{1,3}(?:,\d{3})*|\d+)"
_TEAM_PATTERNS = [
    re.compile(r"\b(?:led|lead|leading|manag(?:e|ed|es|ing)|built|grew|scaled|oversaw|oversee|oversees|direct(?:ed|s|ing)?|ran|run|runs)\b"
               r"[^.\n;]{0,60}?\b" + _NUM + r"\+?\s*(?:-\s*person|person|people|engineers|developers|staff|employees|software engineers|engineering staff|team members|FTEs?)\b", re.I),
    re.compile(r"\b(?:team|org|organization|organisation|department|group|workforce) of\s+" + _NUM + r"\+?", re.I),
    re.compile(r"\b" + _NUM + r"\+?\s*(?:-\s*person|person)\s+(?:team|org|organization|engineering)", re.I),
]


def team_size(mp: MaskedProfile) -> tuple[int, str] | None:
    best: tuple[int, str] | None = None
    for section in mp.sections():
        for rx in _TEAM_PATTERNS:
            for m in rx.finditer(section):
                n = int(m.group(1).replace(",", ""))
                if best is None or n > best[0]:
                    best = (n, sentence_around(section, m.start(), m.end()))
    return best


def _team_score(n: int) -> int:
    if n >= 100:
        return 4
    if n >= 50:
        return 3
    if n >= 20:
        return 2
    if n >= 5:
        return 1
    return 0


def heuristic_card(profile: Profile, rubric: Rubric, matcher: Matcher, mp: MaskedProfile | None = None) -> ScoreCard:
    mp = mp or masked_view(profile)
    all_caps = sorted({c for crit in rubric.criteria for c in crit.signals})
    matches = matcher.match(mp, all_caps) if all_caps else []
    scores: list[CriterionScore] = []
    for crit in rubric.criteria:
        hits: list[tuple[int, str, str]] = []  # (score, quote, why)
        for m in matches:
            if m.capability in crit.signals:
                hits.append((STRENGTH_RANK[m.strength], m.quote, f"{m.capability} ({m.strength})"))
        for kw in crit.keywords:
            q = find_keyword(mp, kw)
            if q:
                hits.append((2, q, f"keyword '{kw}'"))
        if crit.heuristic == "team_size":
            ts = team_size(mp)
            if ts:
                hits.append((_team_score(ts[0]), ts[1], f"team size {ts[0]}"))
        if not hits:
            scores.append(CriterionScore(criterion_id=crit.id, score=None, rationale="No matching evidence found."))
            continue
        hits.sort(key=lambda h: -h[0])
        best = hits[0][0]
        strong_quotes = {q for s, q, _ in hits if s >= 3}
        if best == 3 and len(strong_quotes) >= 2:
            best = 4
        evidence: list[str] = []
        for _, q, _ in hits:
            if q not in evidence:
                evidence.append(q)
            if len(evidence) == 3:
                break
        why = sorted({w for _, _, w in hits})
        scores.append(CriterionScore(criterion_id=crit.id, score=best, evidence=evidence,
                                     rationale="Matched " + ", ".join(why[:4]) + "."))
    known = [s for s in scores if s.score is not None]
    summary = f"Signal match on {len(known)} of {len(scores)} criteria."
    return ScoreCard(scores=scores, summary=summary, scorer="heuristic")
