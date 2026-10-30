"""Scoring agent: one Claude call per candidate, structured output, verified evidence."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from taa.evidence import apply_verification
from taa.llm.client import LLMClient, LLMError
from taa.llm.prompts import SCORING_PROMPT_VERSION, SCORING_SYSTEM, rubric_block, scoring_schema
from taa.models import CriterionScore, Rubric, ScoreCard
from taa.pipeline.mask import MaskedProfile
from taa.signals import SignalDictionary

log = logging.getLogger(__name__)


class LLMScorer:
    def __init__(self, client: LLMClient, rubric: Rubric, signals: SignalDictionary, model: str, effort: str):
        self.client = client
        self.rubric = rubric
        self.model = model
        self.effort = effort
        # Stable prefix (instructions, then rubric) cached across every candidate in a search
        self.system = [
            {"type": "text", "text": SCORING_SYSTEM},
            {"type": "text", "text": rubric_block(rubric, signals), "cache_control": {"type": "ephemeral"}},
        ]
        self.schema = scoring_schema(rubric)

    def score(self, mp: MaskedProfile) -> ScoreCard:
        user = f"<profile>\n{mp.text}\n</profile>\n\nScore this profile against every criterion in the rubric."
        resp = self.client.complete(model=self.model, system=self.system, user=user,
                                    effort=self.effort, json_schema=self.schema)
        data = resp.json()
        by_id = {}
        for item in data.get("criteria", []):
            cid = item.get("criterion_id")
            if cid in by_id or self.rubric.criterion(cid) is None:
                continue
            unknown = item.get("status") != "scored"
            by_id[cid] = CriterionScore(
                criterion_id=cid,
                score=None if unknown else max(0, min(4, int(item.get("score", 0)))),
                evidence=[] if unknown else [str(q) for q in item.get("evidence", [])][:5],
                rationale=str(item.get("rationale", ""))[:600],
            )
        scores = [by_id.get(c.id) or CriterionScore(criterion_id=c.id, rationale="Model returned no score for this criterion.")
                  for c in self.rubric.criteria]
        card = ScoreCard(scores=scores, summary=str(data.get("summary", ""))[:400],
                         scorer=f"llm:{resp.model}", usage=resp.usage,
                         notes=[f"prompt {SCORING_PROMPT_VERSION}", f"request {resp.request_id}"] if resp.request_id
                         else [f"prompt {SCORING_PROMPT_VERSION}"])
        return apply_verification(card, mp.text)


def score_many(items: dict[str, MaskedProfile], scorer: LLMScorer, fallback: Callable[[str], ScoreCard],
               concurrency: int = 4, progress: Callable[[int, int], None] | None = None) -> dict[str, ScoreCard]:
    """Score candidates in parallel. A failed or refused call falls back to the heuristic card
    for that candidate, with a note saying so."""
    out: dict[str, ScoreCard] = {}
    keys = list(items)

    def one(key: str) -> tuple[str, ScoreCard]:
        try:
            return key, scorer.score(items[key])
        except (LLMError, ValueError, TypeError) as e:
            log.warning("LLM scoring failed for %s: %s", key, e)
            card = fallback(key)
            return key, card.model_copy(update={"notes": card.notes + [f"LLM scoring failed ({e}); heuristic score shown"]})

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for i, (key, card) in enumerate(pool.map(one, keys), 1):
            out[key] = card
            if progress:
                progress(i, len(keys))
    return out
