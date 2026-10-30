"""Ranking is computed in code from criterion scores and weights, never by asking a model
to rank. That keeps rankings reproducible and auditable."""
from __future__ import annotations

from taa.models import RankedCandidate, Rubric, ScoreCard


def totals(card: ScoreCard, rubric: Rubric) -> tuple[float, float, int]:
    """Return (total 0-100, coverage 0-1, unknown requirement count)."""
    tw = rubric.total_weight
    points = covered = 0.0
    req_unknown = 0
    for crit in rubric.criteria:
        s = card.get(crit.id)
        if s is None or s.score is None:
            if crit.type == "requirement":
                req_unknown += 1
            continue
        points += crit.weight * s.score / 4
        covered += crit.weight
    return round(100 * points / tw, 1), round(covered / tw, 3), req_unknown


def rank(cands: list[RankedCandidate]) -> list[RankedCandidate]:
    ordered = sorted(cands, key=lambda c: (-c.total, -c.coverage, c.requirement_unknowns, c.key))
    for i, c in enumerate(ordered, 1):
        c.rank = i
    return ordered
