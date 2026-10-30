"""Import candidate files that recruiters export from licensed tools (LinkedIn Recruiter,
ClearanceJobs, the ATS) or that the tool's own export produced.

Supported:
- CSV with flexible headers (see HEADER_ALIASES). Positions can be given as current title and
  company, or as position_N_title / position_N_company / position_N_start / position_N_end /
  position_N_description columns.
- JSON or JSON Lines: a list of Profile-shaped objects, or People Data Labs person records."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from taa.connectors.people_data_labs import map_person
from taa.models import Position, Profile

HEADER_ALIASES = {
    "full_name": ["name", "full name", "candidate name", "candidate"],
    "first_name": ["first name", "firstname", "given name"],
    "last_name": ["last name", "lastname", "surname", "family name"],
    "headline": ["headline", "profile headline", "professional headline"],
    "summary": ["summary", "about", "bio", "notes summary"],
    "location": ["location", "city", "current location", "candidate location"],
    "current_title": ["current title", "title", "job title", "current position", "position"],
    "current_company": ["current company", "company", "employer", "current employer", "organization"],
    "linkedin_url": ["linkedin url", "linkedin", "profile url", "public profile url", "linkedin profile", "linkedin profile url"],
    "email": ["email", "email address", "e-mail", "primary email"],
    "clearance": ["clearance", "security clearance", "clearance level", "active clearance"],
    "skills": ["skills", "top skills", "keywords"],
    "experience_text": ["experience", "work history", "resume text", "resume"],
}

_POS_RE = re.compile(r"^position[ _]?(\d+)[ _](title|company|start|end|description)$")


def _canon(header: str) -> str:
    h = re.sub(r"\s+", " ", header.strip().lower().replace("_", " "))
    for key, names in HEADER_ALIASES.items():
        if h == key.replace("_", " ") or h in names:
            return key
    m = _POS_RE.match(h.replace(" ", "_"))
    if m:
        return f"position_{m.group(1)}_{m.group(2)}"
    return h


def _split_list(value: str) -> list[str]:
    return [s.strip() for s in re.split(r"[;|\n]", value or "") if s.strip()]


def load_csv(text: str, source: str) -> list[Profile]:
    reader = csv.DictReader(io.StringIO(text))
    now = datetime.now(timezone.utc)
    out = []
    for i, raw in enumerate(reader, 1):
        row = {_canon(k): (v or "").strip() for k, v in raw.items() if k}
        name = row.get("full_name") or " ".join(x for x in (row.get("first_name"), row.get("last_name")) if x)
        positions: dict[int, dict] = {}
        for k, v in row.items():
            m = re.match(r"^position_(\d+)_(\w+)$", k)
            if m and v:
                positions.setdefault(int(m.group(1)), {})[m.group(2)] = v
        pos_list = [Position(**{k: v for k, v in positions[n].items()}) for n in sorted(positions)]
        if not pos_list and (row.get("current_title") or row.get("current_company")):
            pos_list = [Position(title=row.get("current_title", ""), company=row.get("current_company", ""),
                                 description=row.get("experience_text", ""))]
        elif row.get("experience_text") and pos_list:
            pos_list[0] = pos_list[0].model_copy(update={"description": (pos_list[0].description + "\n" + row["experience_text"]).strip()})
        out.append(Profile(
            source=source, source_id=str(i), full_name=name, headline=row.get("headline", ""),
            summary=row.get("summary", ""), location=row.get("location", ""), positions=pos_list,
            skills=_split_list(row.get("skills", "")), clearance=row.get("clearance", ""),
            linkedin_url=row.get("linkedin_url", ""), email=row.get("email", ""), fetched_at=now,
        ))
    return out


def _from_obj(obj: dict, source: str, idx: int) -> Profile:
    if "experience" in obj and isinstance(obj.get("experience"), list) and "positions" not in obj:
        p = map_person(obj)
        return p.model_copy(update={"source": source, "sources": [source]})
    data = dict(obj)
    data.setdefault("source", source)
    data.setdefault("source_id", str(idx))
    return Profile.model_validate(data)


def load_json(text: str, source: str) -> list[Profile]:
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        items = json.loads(text)
    elif text.startswith("{"):
        try:
            obj = json.loads(text)
            items = obj.get("profiles") or obj.get("data") or obj.get("candidates") or [obj]
        except json.JSONDecodeError:
            items = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        raise ValueError("Unrecognized JSON layout")
    return [_from_obj(o, source, i) for i, o in enumerate(items, 1) if isinstance(o, dict)]


def load_file(filename: str, data: bytes, source: str | None = None) -> list[Profile]:
    name = filename.lower()
    text = data.decode("utf-8-sig", errors="replace")
    label = source or re.sub(r"\.(csv|json|jsonl)$", "", filename.rsplit("/", 1)[-1])[:60] or "upload"
    if name.endswith(".csv"):
        return load_csv(text, label)
    if name.endswith((".json", ".jsonl")):
        return load_json(text, label)
    raise ValueError(f"Unsupported file type: {filename} (use .csv, .json or .jsonl)")
