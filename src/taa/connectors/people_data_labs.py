"""People Data Labs Person Search connector (licensed API, not scraping).

Field mapping follows PDL's v5 person schema. Confirm the field set your contract returns
and PDL's data provenance terms before production use; the plan's Section 9 covers why."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from taa.models import Education, Position, Profile

PDL_SEARCH_URL = "https://api.peopledatalabs.com/v5/person/search"


def _name(v) -> str:
    if isinstance(v, dict):
        return str(v.get("name") or "")
    return str(v or "")


def _date(v) -> str | None:
    if not v:
        return None
    return str(v)[:7]


def map_person(person: dict) -> Profile:
    positions = []
    for exp in person.get("experience") or []:
        end = exp.get("end_date")
        positions.append(Position(
            title=_name(exp.get("title")).title() if _name(exp.get("title")).islower() else _name(exp.get("title")),
            company=_name(exp.get("company")).title() if _name(exp.get("company")).islower() else _name(exp.get("company")),
            start=_date(exp.get("start_date")),
            end=_date(end),
            description=str(exp.get("summary") or ""),
        ))
    education = []
    for ed in person.get("education") or []:
        education.append(Education(
            school=_name(ed.get("school")),
            degree=", ".join(ed.get("degrees") or []),
            field=", ".join(ed.get("majors") or []),
            start=_date(ed.get("start_date")),
            end=_date(ed.get("end_date")),
        ))
    li = person.get("linkedin_url") or ""
    if li and not li.startswith("http"):
        li = "https://" + li
    return Profile(
        source="pdl",
        source_id=str(person.get("id") or ""),
        full_name=(person.get("full_name") or "").title() if (person.get("full_name") or "").islower() else (person.get("full_name") or ""),
        headline=str(person.get("headline") or person.get("job_title") or ""),
        summary=str(person.get("summary") or ""),
        location=str(person.get("location_name") or ""),
        positions=positions,
        education=education,
        skills=[str(s) for s in person.get("skills") or []],
        linkedin_url=li,
        fetched_at=datetime.now(timezone.utc),
    )


class PeopleDataLabs:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self._client = httpx.Client(transport=transport, timeout=timeout,
                                    headers={"X-Api-Key": api_key, "Content-Type": "application/json"})

    def search(self, sql: str, size: int = 25) -> list[Profile]:
        """Run a PDL SQL person search. PDL bills per record returned, so keep size small."""
        r = self._client.post(PDL_SEARCH_URL, json={"sql": sql, "size": size})
        if r.status_code == 404:
            return []  # PDL returns 404 when nothing matches
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                msg = r.text
            raise RuntimeError(f"People Data Labs error {r.status_code}: {msg}")
        return [map_person(p) for p in r.json().get("data") or []]
