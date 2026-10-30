"""Query planner: turns an approved rubric and the signal dictionary into search strings
recruiters paste into licensed tools (LinkedIn Recruiter, ClearanceJobs) and a People Data
Labs SQL query. The tool never queries LinkedIn itself."""
from __future__ import annotations

from dataclasses import dataclass, field

from taa.models import Rubric
from taa.signals import SignalDictionary


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        k = i.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(i.strip())
    return out


def _q(term: str) -> str:
    term = term.replace('"', "")
    return f'"{term}"' if (" " in term or "-" in term) else term


def _or(terms: list[str]) -> str:
    return "(" + " OR ".join(_q(t) for t in terms) + ")" if terms else ""


def _sql_str(s: str) -> str:
    return "'" + s.lower().replace("'", "''") + "'"


@dataclass
class QueryPlan:
    titles: list[str]
    companies: list[str]
    keyword_groups: dict[str, list[str]] = field(default_factory=dict)
    linkedin_boolean: str = ""
    clearancejobs_boolean: str = ""
    pdl_sql: str = ""


def plan_queries(rubric: Rubric, signals: SignalDictionary, max_terms: int = 12) -> QueryPlan:
    companies = list(rubric.target_companies)
    groups: dict[str, list[str]] = {}
    for crit in rubric.criteria:
        terms = list(crit.keywords)
        for cid in crit.signals:
            cap = signals.capabilities.get(cid)
            if not cap:
                continue
            for s in cap.signals:
                if s.strength == "strong" and s.employers:
                    companies.append(s.employers[0])
                if not s.employers and not s.title_patterns:
                    terms.extend(s.keywords)
        terms = _dedupe(terms)[:max_terms]
        if terms:
            groups[crit.id] = terms
    companies = _dedupe(companies)
    titles = _dedupe(rubric.target_titles)

    req_terms = _dedupe([t for c in rubric.criteria if c.type == "requirement" for t in groups.get(c.id, [])])[:max_terms]
    parts = [p for p in (_or(titles), _or(req_terms)) if p]
    boolean = " AND ".join(parts)

    where = ["location_country='united states'"]
    if companies:
        where.append("experience.company.name IN (" + ", ".join(_sql_str(c) for c in companies[:40]) + ")")
    if titles:
        where.append("(" + " OR ".join(f"job_title LIKE {_sql_str('%' + t + '%')}" for t in titles[:10]) + ")")
    sql = "SELECT * FROM person\nWHERE " + "\n  AND ".join(where)
    return QueryPlan(titles=titles, companies=companies, keyword_groups=groups,
                     linkedin_boolean=boolean, clearancejobs_boolean=boolean, pdl_sql=sql)
