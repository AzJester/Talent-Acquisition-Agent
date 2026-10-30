"""Application services shared by the web app and the CLI."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from taa.config import Settings
from taa.connectors.people_data_labs import PeopleDataLabs
from taa.db import ResultRow, ReviewRow, RubricRow, SearchInputRow, SearchRow, audit, utcnow
from taa.llm.client import LLMClient
from taa.llm.intake import rubric_from_yaml
from taa.models import Profile, Rubric
from taa.pipeline.filters import load_do_not_target
from taa.pipeline.run import run_pipeline
from taa.signals import load_signals

log = logging.getLogger(__name__)

REJECT_REASONS = {
    "not_senior_enough": "Not senior enough",
    "wrong_domain": "Wrong domain or experience",
    "comp_risk": "Compensation mismatch likely",
    "location": "Location or on-site constraint",
    "already_engaged": "Already contacted or in process",
    "non_solicit": "Non-solicitation concern",
    "evidence_wrong": "Evidence or score is wrong",
    "other": "Other (see note)",
}
APPROVE_REASONS = {
    "strong_fit": "Strong fit",
    "worth_a_call": "Worth a call",
    "pipeline": "Keep in pipeline for later",
}


class Services:
    def __init__(self, settings: Settings, session_factory: sessionmaker, llm_client: LLMClient | None = None,
                 pdl_transport=None):
        self.settings = settings
        self.Session = session_factory
        self.llm = llm_client
        self.pdl_transport = pdl_transport
        self.reload_config()

    def reload_config(self) -> None:
        self.signals = load_signals(self.settings.signals_path)
        self.dnt = load_do_not_target(self.settings.do_not_target_path)

    @property
    def pdl_enabled(self) -> bool:
        return bool(self.settings.pdl_api_key)

    def seed_templates(self, actor: str = "system") -> int:
        """Load config/rubrics/*.yaml as draft rubrics if that id and version are not present."""
        added = 0
        with self.Session() as s:
            for f in sorted(self.settings.rubrics_path.glob("*.yaml")):
                text = f.read_text(encoding="utf-8")
                r = rubric_from_yaml(text)
                exists = s.scalar(select(RubricRow).where(RubricRow.slug == r.id, RubricRow.version == r.version))
                if exists:
                    continue
                s.add(RubricRow(slug=r.id, version=r.version, title=r.title, yaml_text=text,
                                status="draft", origin="template", created_by=actor))
                audit(s, actor, "rubric.seeded", rubric=r.id, version=r.version)
                added += 1
            s.commit()
        return added

    def execute_search(self, search_id: int, actor: str) -> None:
        """Run (or rerun) a search. Reviews carry over to the same candidate on rerun."""
        with self.Session() as s:
            search = s.get(SearchRow, search_id)
            if search is None:
                return
            search.status, search.progress, search.error = "running", "Starting", None
            audit(s, actor, "search.started", search_id)
            s.commit()
            try:
                rubric = rubric_from_yaml(search.rubric_yaml)
                if search.pdl_sql and self.pdl_enabled and not any(i.source == "pdl" for i in search.inputs):
                    pdl = PeopleDataLabs(self.settings.pdl_api_key, transport=self.pdl_transport)
                    size = min(search.pdl_size or self.settings.pdl_max_results, self.settings.pdl_max_results)
                    found = pdl.search(search.pdl_sql, size)
                    s.add(SearchInputRow(search_id=search.id, filename="People Data Labs search", source="pdl",
                                         record_count=len(found), profiles=[p.model_dump(mode="json") for p in found]))
                    audit(s, actor, "pdl.search", search_id, records=len(found), size=size)
                    s.commit()
                    s.refresh(search)
                profiles = [Profile.model_validate(p) for i in search.inputs for p in i.profiles]

                def progress(msg: str) -> None:
                    search.progress = msg
                    s.commit()

                result = run_pipeline(profiles, rubric, self.signals, self.dnt, self.settings, self.llm, progress=progress)

                old_reviews: dict[str, list[ReviewRow]] = {}
                for r in list(search.results):
                    old_reviews[r.key] = [ReviewRow(decision=v.decision, reason_code=v.reason_code, note=v.note,
                                                    reviewer=v.reviewer, created_at=v.created_at) for v in r.reviews]
                search.results.clear()
                s.flush()
                for c in result.ranked:
                    row = ResultRow(search_id=search.id, key=c.key, rank=c.rank, total=c.total, coverage=c.coverage,
                                    requirement_unknowns=c.requirement_unknowns,
                                    profile=c.profile.model_dump(mode="json"), card=c.card.model_dump(mode="json"),
                                    flags=c.flags)
                    row.reviews = old_reviews.get(c.key, [])
                    search.results.append(row)
                for e in result.excluded:
                    search.results.append(ResultRow(search_id=search.id, key=e.key, excluded=True, exclusion_reason=e.reason,
                                                    profile=e.profile.model_dump(mode="json"), flags=[]))
                search.stats = result.stats
                search.status, search.progress = "complete", "Done"
                audit(s, actor, "search.completed", search_id, **{k: v for k, v in result.stats.items() if k != "usage"},
                      usage=result.stats.get("usage", {}))
                s.commit()
            except Exception as e:  # noqa: BLE001 - surface any failure to the recruiter
                log.exception("Search %s failed", search_id)
                s.rollback()
                search = s.get(SearchRow, search_id)
                search.status, search.error = "failed", str(e)[:2000]
                audit(s, actor, "search.failed", search_id, error=str(e)[:500])
                s.commit()

    def purge(self, older_than_days: int, actor: str = "system") -> dict:
        """Retention: for searches older than the cutoff, delete candidates nobody approved and
        drop stored source records. Approved candidates and the audit log are kept."""
        cutoff = utcnow() - timedelta(days=older_than_days)
        removed = cleared = 0
        with self.Session() as s:
            for search in s.scalars(select(SearchRow).where(SearchRow.created_at < cutoff)):
                for r in list(search.results):
                    lr = r.latest_review
                    if not (lr and lr.decision == "approve"):
                        s.delete(r)
                        removed += 1
                for i in search.inputs:
                    if i.profiles:
                        i.profiles = []
                        cleared += 1
            audit(s, actor, "retention.purge", None, older_than_days=older_than_days,
                  results_removed=removed, inputs_cleared=cleared)
            s.commit()
        return {"results_removed": removed, "inputs_cleared": cleared}


def parse_rubric_row(row: RubricRow) -> Rubric:
    return rubric_from_yaml(row.yaml_text)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
