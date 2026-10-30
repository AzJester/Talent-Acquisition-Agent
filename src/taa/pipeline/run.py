"""End-to-end pipeline: resolve, filter, score, verify, rank."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from taa.config import Settings
from taa.evidence import apply_verification, quote_stats
from taa.llm.client import LLMClient
from taa.llm.prompts import SCORING_PROMPT_VERSION
from taa.llm.scoring import LLMScorer, score_many
from taa.models import Exclusion, Profile, RankedCandidate, Rubric, ScoreCard
from taa.pipeline.filters import DoNotTarget, apply_filters, recruiter_flags
from taa.pipeline.heuristic import heuristic_card
from taa.pipeline.mask import masked_view
from taa.pipeline.rank import rank, totals
from taa.pipeline.resolve import resolve
from taa.signals import Matcher, SignalDictionary


@dataclass
class RunResult:
    ranked: list[RankedCandidate]
    excluded: list[Exclusion]
    stats: dict = field(default_factory=dict)


def run_pipeline(profiles: list[Profile], rubric: Rubric, signals: SignalDictionary, dnt: DoNotTarget,
                 settings: Settings, llm_client: LLMClient | None = None, now: datetime | None = None,
                 progress: Callable[[str], None] | None = None) -> RunResult:
    now = now or datetime.now(timezone.utc)
    say = progress or (lambda msg: None)
    resolved = resolve(profiles)
    say(f"Resolved {len(profiles)} records into {len(resolved)} candidates")
    kept, excluded = apply_filters(resolved, rubric, dnt, now)
    say(f"Filtered: {len(kept)} kept, {len(excluded)} excluded")

    matcher = Matcher(signals)
    masked = {k: masked_view(resolved[k]) for k in kept}
    cards: dict[str, ScoreCard] = {
        k: apply_verification(heuristic_card(resolved[k], rubric, matcher, masked[k]), masked[k].text) for k in kept}

    scorer_label = "heuristic"
    llm_keys: list[str] = []
    if llm_client is not None and kept:
        pre = sorted(kept, key=lambda k: (-totals(cards[k], rubric)[0], k))
        llm_keys = pre[: settings.max_llm_candidates]
        scorer = LLMScorer(llm_client, rubric, signals, settings.scoring_model, settings.scoring_effort)
        say(f"Scoring {len(llm_keys)} candidates with {settings.scoring_model}")
        llm_cards = score_many({k: masked[k] for k in llm_keys}, scorer, fallback=lambda k: cards[k],
                               concurrency=settings.llm_concurrency,
                               progress=lambda i, n: say(f"Scored {i} of {n}"))
        cards.update(llm_cards)
        scorer_label = f"llm:{settings.scoring_model}"
        for k in pre[settings.max_llm_candidates:]:
            cards[k] = cards[k].model_copy(update={"notes": cards[k].notes + [
                f"Not sent to the model (outside the top {settings.max_llm_candidates} by heuristic pre-rank)"]})

    ranked = []
    for k, filter_flags in kept.items():
        card = cards[k]
        total, coverage, req_unknown = totals(card, rubric)
        flags = filter_flags + recruiter_flags(resolved[k], signals, settings.stale_after_days, now)
        if any(n.startswith("LLM scoring failed") for n in card.notes):
            flags.append("Model scoring failed for this candidate; heuristic score shown")
        ranked.append(RankedCandidate(key=k, profile=resolved[k], card=card, total=total, coverage=coverage,
                                      requirement_unknowns=req_unknown, flags=flags))
    ranked = rank(ranked)

    usage: dict[str, int] = {}
    for c in cards.values():
        for uk, uv in c.usage.items():
            usage[uk] = usage.get(uk, 0) + int(uv or 0)
    stats = {
        "records_in": len(profiles),
        "candidates": len(resolved),
        "kept": len(kept),
        "excluded": len(excluded),
        "llm_scored": len(llm_keys),
        "llm_failures": sum(1 for c in cards.values() if any(n.startswith("LLM scoring failed") for n in c.notes)),
        "scorer": scorer_label,
        "prompt_version": SCORING_PROMPT_VERSION if llm_keys else None,
        "rubric_id": rubric.id,
        "rubric_version": rubric.version,
        "usage": usage,
        # Evidence accuracy is measured on model-scored cards; heuristic quotes are exact by construction
        **quote_stats([cards[k] for k in llm_keys] if llm_keys else list(cards.values())),
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    say("Done")
    return RunResult(ranked=ranked, excluded=excluded, stats=stats)
