"""Evidence verification: every quote must appear in the masked profile text.

A quote the model cannot point to in the source is treated as fabricated. It is removed,
and a criterion left with no verified quote becomes unknown. The plan treats a fabricated
quote reaching a recruiter as a stop-work issue, so this check is not optional."""
from __future__ import annotations

import re

from taa.models import CriterionScore, ScoreCard

_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                            "–": "-", "—": "-", " ": " "})
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    t = text.translate(_TRANSLATE)
    t = _WS.sub(" ", t).strip().lower()
    t = t.strip("\"'` ")
    t = re.sub(r"^(\.\.\.|…)\s*|\s*(\.\.\.|…)$", "", t)
    return t.rstrip(" .,;:")


def verify_quotes(quotes: list[str], source_text: str) -> tuple[list[str], int]:
    hay = normalize(source_text)
    kept, removed = [], 0
    for q in quotes:
        n = normalize(q)
        if len(n) >= 2 and n in hay:
            if q.strip() not in kept:
                kept.append(q.strip())
        else:
            removed += 1
    return kept, removed


def apply_verification(card: ScoreCard, source_text: str) -> ScoreCard:
    out: list[CriterionScore] = []
    for s in card.scores:
        kept, removed = verify_quotes(s.evidence, source_text)
        score, rationale = s.score, s.rationale
        if score is not None and not kept:
            score = None
            if removed:
                rationale = (rationale + " " if rationale else "") + "Evidence could not be verified in the profile; marked unknown."
        out.append(s.model_copy(update={"score": score, "evidence": kept, "rationale": rationale,
                                        "removed_quotes": s.removed_quotes + removed}))
    return card.model_copy(update={"scores": out})


def quote_stats(cards: list[ScoreCard]) -> dict:
    kept = sum(len(s.evidence) for c in cards for s in c.scores)
    removed = sum(s.removed_quotes for c in cards for s in c.scores)
    total = kept + removed
    return {"quotes_kept": kept, "quotes_removed": removed,
            "evidence_accuracy": round(kept / total, 4) if total else None}
