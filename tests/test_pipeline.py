from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import make_profile

from taa.connectors.file_import import load_file
from taa.evidence import apply_verification, normalize, verify_quotes
from taa.llm.intake import rubric_from_yaml
from taa.models import CriterionScore, Position, Profile, ScoreCard
from taa.pipeline.filters import (
    DoNotTarget,
    DoNotTargetEntry,
    apply_filters,
    recruiter_flags,
)
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


def _keyword_rubric():
    return rubric_from_yaml("""
id: program_analyst
title: Program Analyst
scoring: 2
criteria:
  - id: budgeting
    name: Budget and cost analysis
    type: requirement
    weight: 40
    keywords: [budget, cost analysis, EVM]
  - id: clearance
    name: Clearance
    type: desirement
    weight: 60
    keywords: {TS/SCI: 4, Top Secret: 3, Secret: 2}
""")


def _profile(summary, title="Program Analyst", company="SAIC"):
    return Profile(source="test", source_id="1", full_name="Test Person", summary=summary,
                   positions=[Position(title=title, company=company, start="2020-01")])


def test_keyword_mapping_loads_as_list_plus_scores():
    crit = _keyword_rubric().criteria[1]
    assert crit.keywords == ["TS/SCI", "Top Secret", "Secret"]
    assert crit.keyword_scores == {"TS/SCI": 4, "Top Secret": 3, "Secret": 2}


def test_keyword_score_out_of_range_is_refused():
    with pytest.raises(ValueError):
        rubric_from_yaml("""
id: r
title: R
criteria:
  - {id: c, name: C, type: requirement, weight: 10, keywords: {TS/SCI: 5}}
""")


def test_tiered_keywords_take_the_highest_grade_and_never_bump(signals):
    rubric = _keyword_rubric()
    card = heuristic_card(_profile("Holds Top Secret. Active Top Secret clearance since 2019."), rubric, Matcher(signals))
    # two Top Secret quotes stay a 3: keyword hits never lift a 3 to a 4
    assert card.get("clearance").score == 3
    card = heuristic_card(_profile("Active TS/SCI."), rubric, Matcher(signals))
    assert card.get("clearance").score == 4 and card.get("clearance").basis is None


def test_plain_keywords_on_a_keyword_only_item_count_as_found(signals):
    rubric = _keyword_rubric()
    card = heuristic_card(_profile("Owns the program budget and EVM reporting. Active TS/SCI."), rubric, Matcher(signals))
    budget = card.get("budgeting")
    assert budget.score == 4 and budget.basis == "found"
    assert totals(card, rubric)[0] == 100.0


def test_template_clearance_follows_its_guidance(rubric, signals):
    graded = {"Active TS/SCI.": 4, "Active Top Secret clearance.": 3, "Active Secret clearance.": 2}
    for text, want in graded.items():
        assert heuristic_card(_profile(text), rubric, Matcher(signals)).get("active_clearance").score == want, text


def test_team_size_phrasings(rubric, signals):
    cases = {"VP, Software Engineering | 200+ engineers": 4, "Director of DevSecOps over 6 teams (65 engineers).": 3,
             "Leads 120 engineers across three programs.": 4}
    for text, want in cases.items():
        assert heuristic_card(_profile(text, title="Director"), rubric, Matcher(signals)).get("large_org_leadership").score == want, text


def test_rubrics_without_scoring_keep_the_old_rules(signals):
    """A rubric approved before beta 1.2.4 has no scoring line; its scores must not change."""
    old = rubric_from_yaml("""
id: old
title: Old
criteria:
  - {id: clearance, name: Clearance, type: desirement, weight: 10, keywords: [TS/SCI, Secret clearance]}
  - {id: org, name: Org, type: requirement, weight: 10, heuristic: team_size}
""")
    assert old.scoring == 1
    card = heuristic_card(_profile("Active Secret clearance. VP, Software | 200+ engineers"), old, Matcher(signals))
    assert card.get("clearance").score == 2 and card.get("clearance").basis is None
    assert card.get("org").score is None  # the 200+ phrasing arrived with rules 2


def test_lapsed_or_eligible_clearances_are_capped(rubric, signals):
    cases = {"Inactive TS/SCI (expired 2017).": 1, "Formerly held TS/SCI; no current clearance.": 1,
             "TS/SCI eligible; no current clearance.": 1, "Active Top Secret (SCI eligible).": 3,
             "Active TS/SCI with CI poly; previously held Secret.": 4, "Clearance not active: TS/SCI.": 1}
    for text, want in cases.items():
        assert heuristic_card(_profile(text), rubric, Matcher(signals)).get("active_clearance").score == want, text


def test_team_size_ignores_audiences_hires_and_company_size(rubric, signals):
    for text in ["Supported 200+ engineers with laptop provisioning.", "Technical recruiter who hired 150+ engineers.",
                 "Trained 500+ engineers on Kubernetes.", "Our platform is used by 3,000+ developers.",
                 "Backend engineer at a defense prime with 50,000+ employees.", "Gave heads-up briefings to 900 staff.",
                 "Head of Developer Relations for a community of 10,000 developers.", "Mentored 25+ developers.",
                 "Program Manager at Lockheed Martin (110,000 employees)."]:
        assert heuristic_card(_profile(text, title="Engineer"), rubric, Matcher(signals)).get("large_org_leadership").score is None, text


def test_keyword_input_is_strict():
    for bad in ["keywords: TS/SCI", "keywords: [{TS/SCI: 4}]", "keywords: {TS/SCI: true}", "keywords: {TS/SCI: 2.5}"]:
        with pytest.raises(ValueError):
            rubric_from_yaml(f"id: r\ntitle: R\ncriteria:\n  - {{id: c, name: C, type: requirement, weight: 10, {bad}}}\n")


def test_a_keyword_removed_from_the_list_loses_its_score():
    r = rubric_from_yaml("""
id: r
title: R
criteria:
  - id: c
    name: C
    type: requirement
    weight: 10
    keywords: [TS/SCI]
    keyword_scores: {TS/SCI: 4, Secret: 2}
""")
    assert r.criteria[0].keywords == ["TS/SCI"] and r.criteria[0].keyword_scores == {"TS/SCI": 4}
