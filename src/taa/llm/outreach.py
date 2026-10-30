"""Outreach drafts. The recruiter edits and sends; the tool never contacts anyone."""
from __future__ import annotations

from taa.llm.client import LLMClient
from taa.llm.prompts import OUTREACH_SYSTEM
from taa.models import Rubric, ScoreCard


def _evidence_lines(card: ScoreCard, rubric: Rubric, limit: int = 4) -> list[str]:
    pairs = []
    for c in sorted(rubric.criteria, key=lambda c: -c.weight):
        s = card.get(c.id)
        if s and s.score and s.evidence:
            pairs.append(f"{c.name}: {s.evidence[0]}")
    return pairs[:limit]


def offline_draft(card: ScoreCard, rubric: Rubric) -> str:
    ev = _evidence_lines(card, rubric, 1)
    if ev:
        quote = ev[0].split(": ", 1)[1].strip().rstrip(".")
        hook = f'Your background caught my attention, in particular this from your profile: "{quote}."'
    else:
        hook = "Your background caught my attention."
    pitch = rubric.role_pitch or f"Astrion is hiring a {rubric.title}."
    return (f"Hi [First name],\n\n{hook} {pitch}\n\n"
            "Would you be open to a 20-minute call in the next week or two to hear more?\n\n[Recruiter name]")


def draft_outreach(client: LLMClient | None, card: ScoreCard, rubric: Rubric, model: str, effort: str) -> str:
    if client is None:
        return offline_draft(card, rubric)
    ev = _evidence_lines(card, rubric)
    user = (f"ROLE: {rubric.title}\nROLE PITCH: {rubric.role_pitch or rubric.summary}\n"
            "VERIFIED EVIDENCE FROM THE CANDIDATE'S PROFILE:\n" + "\n".join(f"- {e}" for e in ev) +
            "\n\nDraft the message.")
    resp = client.complete(model=model, system=[{"type": "text", "text": OUTREACH_SYSTEM}], user=user,
                           effort=effort, max_tokens=8000)
    return resp.text.strip().replace("—", ", ")
