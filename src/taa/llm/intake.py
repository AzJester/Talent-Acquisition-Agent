"""Intake agent: free-text hiring request to a draft rubric for hiring-manager approval."""
from __future__ import annotations

import re

import yaml

from taa.llm.client import LLMClient
from taa.llm.prompts import INTAKE_SYSTEM, intake_schema
from taa.models import Rubric
from taa.signals import SignalDictionary


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:48] or "role"


def _snake(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "c_" + s
    return s[:40]


def draft_rubric(client: LLMClient, request_text: str, signals: SignalDictionary, model: str, effort: str) -> Rubric:
    caps = sorted(signals.capabilities)
    cap_list = "\n".join(f"- {cid}: {signals.capabilities[cid].description}" for cid in caps)
    system = [{"type": "text", "text": INTAKE_SYSTEM},
              {"type": "text", "text": "SIGNAL GUIDE IDS:\n" + cap_list}]
    user = f"<request>\n{request_text.strip()}\n</request>\n\nDraft the rubric."
    data = client.complete(model=model, system=system, user=user, effort=effort,
                           json_schema=intake_schema(caps)).json()
    criteria = []
    seen = set()
    for c in data.get("criteria", []):
        cid = _snake(c.get("id") or c.get("name", ""))
        while cid in seen:
            cid += "_2"
        seen.add(cid)
        criteria.append({
            "id": cid, "name": c.get("name", cid), "short": c.get("short", ""),
            "type": c.get("type", "desirement"), "weight": max(1, int(c.get("weight", 10))),
            "guidance": c.get("guidance", ""),
            "signals": [s for s in c.get("signals", []) if s in signals.capabilities],
            "keywords": c.get("keywords", []),
        })
    total = sum(c["weight"] for c in criteria) or 1
    for c in criteria:  # normalize weights to sum to roughly 100
        c["weight"] = max(1, round(100 * c["weight"] / total))
    return Rubric.model_validate({
        "id": _slug(data.get("title", "role")),
        "version": 1,
        "title": data.get("title", "Untitled role"),
        "summary": data.get("summary", ""),
        "role_pitch": data.get("role_pitch", ""),
        "target_titles": data.get("target_titles", []),
        "target_companies": data.get("target_companies", []),
        "gates": {"locations": data.get("locations", [])},
        "criteria": criteria,
    })


def rubric_to_yaml(rubric: Rubric) -> str:
    data = rubric.model_dump(mode="json", exclude_defaults=False)
    for c in data["criteria"]:
        if c.get("heuristic") is None:
            c.pop("heuristic", None)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def rubric_from_yaml(text: str) -> Rubric:
    return Rubric.model_validate(yaml.safe_load(text) or {})
