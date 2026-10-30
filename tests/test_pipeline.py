from __future__ import annotations

from datetime import datetime, timezone

from conftest import make_profile

from taa.connectors.file_import import load_file
from taa.evidence import apply_verification, normalize, verify_quotes
from taa.models import CriterionScore, ScoreCard
from taa.pipeline.filters import DoNotTarget, DoNotTargetEntry, apply_filters, recruiter_flags
from taa.pipeline.heuristic import heuristic_card, team_size
from taa.pipeline.mask import masked_view
from taa.pipeline.rank import totals
from taa.pipeline.resolve import resolve
from taa.pipeline.run import run_pipeline
from taa.query_planner import plan_queries
from taa.signals import Matcher

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def test_mask_removes_identity_and_keeps_work(factory_lead):
    mp = masked_view(factory_lead)
    assert "Robin" not in mp.text and "Example" not in mp.text
    assert "robin@example.com" not in mp.text and "256-555-0100" not in mp.text
    assert "Huntsville" not in mp.text
    assert "Example University" not in mp.text and "2008" not in mp.text
    assert "[CANDIDATE] builds teams" in mp.text
    assert "Director of Engineering | Platform One | 2019-01 to present" in mp.text
    assert "continuous ATO" in mp.text


def test_signal_matching(factory_lead, signals):
    matches = Matcher(signals).match(masked_view(factory_lead))
    caps = {m.capability for m in matches}
    assert {"dod_software_factory", "ato_delivery", "multi_program_delivery", "product_management"} <= caps
    sf = [m for m in matches if m.capability == "dod_software_factory"]
    assert any(m.strength == "strong" and "Platform One" in m.quote for m in sf)


def test_employer_match_is_word_bounded(signals):
    p = make_profile(positions=[{"title": "Director of Engineering", "company": "Kessel Runners Club"}])
    caps = {m.capability for m in Matcher(signals).match(masked_view(p))}
    assert "dod_software_factory" not in caps


def test_team_size_extraction(factory_lead):
    n, quote = team_size(masked_view(factory_lead))
    assert n == 120 and "Led 120 engineers" in quote


def test_heuristic_evidence_always_verifies(factory_lead, rubric, signals):
    mp = masked_view(factory_lead)
    card = heuristic_card(factory_lead, rubric, Matcher(signals), mp)
    for s in card.scores:
        kept, removed = verify_quotes(s.evidence, mp.text)
        assert removed == 0, (s.criterion_id, s.evidence)
    by = {s.criterion_id: s for s in card.scores}
    assert by["large_org_leadership"].score == 4
    assert by["software_factory_leadership"].score >= 3
    assert by["defense_autonomy_product"].score is None


def test_verification_removes_fabricated_quotes():
    text = "EXPERIENCE:\n[1] Director | Platform One | 2019 to present\nLed 120 engineers."
    card = ScoreCard(scorer="test", scores=[
        CriterionScore(criterion_id="a", score=4, evidence=["Led 120 engineers"]),
        CriterionScore(criterion_id="b", score=3, evidence=["Led 400 engineers at Anduril"]),
        CriterionScore(criterion_id="c", score=2, evidence=["“Director  |  Platform One”", "invented"]),
    ])
    out = apply_verification(card, text)
    assert out.get("a").score == 4
    assert out.get("b").score is None and out.get("b").removed_quotes == 1
    assert out.get("c").score == 2 and out.get("c").evidence == ["“Director  |  Platform One”"]
    assert out.get("c").removed_quotes == 1
    assert normalize("...Led 120 engineers.") == "led 120 engineers"


def test_resolve_merges_duplicates_across_sources(sample_bytes):
    profiles = []
    for name, data in sample_bytes.items():
        profiles.extend(load_file(name, data))
    resolved = resolve(profiles)
    assert len(profiles) == 13 and len(resolved) == 12
    avery = next(p for p in resolved.values() if p.full_name == "Avery Lindqvist")
    assert len(avery.sources) == 2
    titles = [(p.title, p.company) for p in avery.positions]
    assert titles.count(("VP Software", "Anduril Industries")) == 1


def test_do_not_target_and_location_gate(rubric):
    dnt = DoNotTarget(employers=[DoNotTargetEntry(name="Partner Co", aliases=["PartnerCo"], reason="Teaming agreement",
                                                  lookback_months=12)])
    current = make_profile(source_id="1", full_name="A One", location="Huntsville, AL",
                           positions=[{"title": "Director", "company": "Partner Co", "start": "2020-01"}])
    former = make_profile(source_id="2", full_name="B Two", location="Huntsville, AL",
                          positions=[{"title": "Director", "company": "Other", "start": "2026-03"},
                                     {"title": "Lead", "company": "PartnerCo", "start": "2020-01", "end": "2026-02"}])
    far = make_profile(source_id="3", full_name="C Three", location="Seattle, WA",
                       positions=[{"title": "Director", "company": "Other"}])
    unknown = make_profile(source_id="4", full_name="D Four", positions=[{"title": "Director", "company": "Other"}])
    gated = rubric.model_copy(update={"gates": rubric.gates.model_copy(update={"locations": ["Huntsville", "Remote"]})})
    kept, excluded = apply_filters(resolve([current, former, far, unknown]), gated, dnt, NOW)
    reasons = {e.profile.full_name: e.reason for e in excluded}
    assert "Partner Co" in reasons["A One"]
    assert "outside" in reasons["C Three"]
    flags = {resolve([former])[k].full_name: v for k, v in kept.items() if k in resolve([former])}
    assert any("Left Partner Co 8 months ago" in f for f in flags["B Two"])
    assert len(kept) == 2


def test_recruiter_flags(signals, factory_lead):
    p = factory_lead.model_copy(update={"fetched_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
                                        "positions": [factory_lead.positions[0].model_copy(update={"company": "Anduril"})]})
    flags = recruiter_flags(p, signals, 180, NOW)
    assert any(f.startswith("Clearance self-reported") for f in flags)
    assert any(f.startswith("Comp risk") for f in flags)
    assert any("days old" in f for f in flags)


def test_totals_and_unknowns(rubric):
    card = ScoreCard(scorer="t", scores=[CriterionScore(criterion_id="large_org_leadership", score=4)])
    total, coverage, req_unknown = totals(card, rubric)
    assert total == 20.0 and coverage == 0.2 and req_unknown == 3


def test_pipeline_ranks_samples_sensibly(sample_bytes, rubric, signals, settings):
    profiles = []
    for name, data in sample_bytes.items():
        profiles.extend(load_file(name, data))
    result = run_pipeline(profiles, rubric, signals, DoNotTarget(), settings, None, now=NOW)
    names = [c.profile.full_name for c in result.ranked]
    assert names[0] == "Avery Lindqvist"
    assert names.index("Riley Castellanos") < names.index("Morgan Feld")
    assert names[-1] == "Sam Ferreira"
    assert result.stats["quotes_removed"] == 0 and result.stats["scorer"] == "heuristic"


def test_query_plan(rubric, signals):
    plan = plan_queries(rubric, signals)
    assert "Anduril Industries" in plan.companies and "Kessel Run" in plan.companies
    assert '"Software Factory Lead"' in plan.linkedin_boolean and " AND " in plan.linkedin_boolean
    assert plan.pdl_sql.startswith("SELECT * FROM person") and "'anduril industries'" in plan.pdl_sql
