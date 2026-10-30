"""Calibration metrics from the plan (Section 10).

recruiter_agreement: share of the recruiter's top picks that appear in the tool's top K.
precision: share of the tool's top K that recruiters approved for outreach."""
from __future__ import annotations

import csv
from pathlib import Path


def read_keys(path: Path, column: str | None = None) -> list[str]:
    """Read candidate identifiers in rank order from a CSV (column 'key', 'linkedin_url' or 'name')."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    col = column or next((c for c in ("key", "linkedin_url", "name", "full_name") if c in rows[0]), None)
    if col is None:
        raise ValueError(f"{path} needs a key, linkedin_url or name column")
    if "rank" in rows[0]:
        rows.sort(key=lambda r: int(r["rank"] or 10**9))
    return [r[col].strip().lower().rstrip("/") for r in rows if r.get(col, "").strip()]


def recruiter_agreement(tool_ranked: list[str], recruiter_top: list[str], k_recruiter: int = 10, k_tool: int = 25) -> float | None:
    top = recruiter_top[:k_recruiter]
    if not top:
        return None
    tool = set(tool_ranked[:k_tool])
    return round(sum(1 for k in top if k in tool) / len(top), 3)


def precision_at_k(tool_ranked: list[str], approved: set[str], k: int = 25) -> float | None:
    top = tool_ranked[:k]
    if not top:
        return None
    return round(sum(1 for c in top if c in approved) / len(top), 3)


TARGETS = {"recruiter_agreement": 0.70, "precision_at_25": 0.50, "evidence_accuracy": 0.98}
