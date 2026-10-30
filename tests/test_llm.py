from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import FakeLLM, LLMRefusal, make_profile

from taa.llm.client import ClaudeClient, LLMError
from taa.llm.intake import draft_rubric
from taa.llm.outreach import draft_outreach, offline_draft
from taa.llm.scoring import LLMScorer
from taa.pipeline.filters import DoNotTarget
from taa.pipeline.mask import masked_view
from taa.pipeline.run import run_pipeline


def _scoring_response(rubric, overrides):
    items = []
    for c in rubric.criteria:
        items.append(overrides.get(c.id, {"criterion_id": c.id, "status": "unknown", "score": 0, "evidence": [], "rationale": ""}))
    return {"criteria": items, "summary": "Strong platform leader."}


def test_llm_scorer_verifies_quotes_and_masks_input(factory_lead, rubric, signals):
    def responder(system, user, schema):
        return _scoring_response(rubric, {
            "large_org_leadership": {"criterion_id": "large_org_leadership", "status": "scored", "score": 4,
                                     "evidence": ["Led 120 engineers."], "rationale": "120 engineers."},
            "defense_autonomy_product": {"criterion_id": "defense_autonomy_product", "status": "scored", "score": 4,
                                         "evidence": ["VP of Lattice at Anduril"], "rationale": "Fabricated."},
        })

    fake = FakeLLM(responder)
    scorer = LLMScorer(fake, rubric, signals, "claude-opus-5-5", "high")
    card = scorer.score(masked_view(factory_lead))
    call = fake.calls[0]
    assert "Robin" not in call["user"] and "robin@example.com" not in call["user"]
    assert call["user"].startswith("<profile>")
    assert call["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "large_org_leadership" in call["system"][1]["text"]
    assert call["schema"]["properties"]["criteria"]["items"]["properties"]["criterion_id"]["enum"][0] == "large_org_leadership"
    assert card.get("large_org_leadership").score == 4
    fabricated = card.get("defense_autonomy_product")
    assert fabricated.score is None and fabricated.removed_quotes == 1
    assert card.scorer == "llm:claude-opus-5-5" and card.usage["cache_read_input_tokens"] == 800


def test_pipeline_falls_back_to_heuristic_on_refusal(factory_lead, rubric, signals, settings):
    other = make_profile(source_id="2", full_name="Other Person", positions=[{"title": "Engineer", "company": "Acme"}])

    def responder(system, user, schema):
        if "Platform One" in user:
            return LLMRefusal("declined")
        return _scoring_response(rubric, {})

    s = replace(settings, llm_provider="anthropic")
    result = run_pipeline([factory_lead, other], rubric, signals, DoNotTarget(), s, FakeLLM(responder))
    lead = next(c for c in result.ranked if c.profile.full_name == "Robin Example")
    assert lead.card.scorer == "heuristic"
    assert any("Model scoring failed" in f for f in lead.flags)
    assert result.stats["llm_failures"] == 1 and result.stats["llm_scored"] == 2
    assert result.ranked[0].profile.full_name == "Robin Example"


def test_pipeline_caps_llm_candidates(rubric, signals, settings):
    people = [make_profile(source_id=str(i), full_name=f"P{i} Q", positions=[{"title": "Director of Engineering", "company": f"Co{i}"}])
              for i in range(5)]
    fake = FakeLLM(lambda s, u, sc: _scoring_response(rubric, {}))
    s = replace(settings, llm_provider="anthropic", max_llm_candidates=2)
    result = run_pipeline(people, rubric, signals, DoNotTarget(), s, fake)
    assert len(fake.calls) == 2 and result.stats["llm_scored"] == 2


def test_intake_builds_valid_rubric(signals):
    def responder(system, user, schema):
        assert "SW Factory Lead" in user
        return {"title": "SW Factory Lead", "summary": "s", "role_pitch": "p", "target_titles": ["Director"],
                "target_companies": ["Anduril Industries"], "locations": [],
                "criteria": [
                    {"id": "Org Size", "name": "Large org", "short": "Org", "type": "requirement", "weight": 60,
                     "guidance": "4 = 100+", "signals": ["large_org_leadership", "not_a_signal"], "keywords": []},
                    {"id": "autonomy", "name": "Autonomy", "short": "Auto", "type": "desirement", "weight": 60,
                     "guidance": "4 = Lattice", "signals": ["defense_autonomy_product_leadership"], "keywords": ["Lattice"]},
                ]}

    r = draft_rubric(FakeLLM(responder), "SW Factory Lead with Lattice experience", signals, "claude-opus-5-5", "high")
    assert r.id == "sw_factory_lead" and r.criteria[0].id == "org_size"
    assert r.criteria[0].signals == ["large_org_leadership"]
    assert sum(c.weight for c in r.criteria) == 100


def test_outreach_drafts(factory_lead, rubric, signals):
    from taa.pipeline.heuristic import heuristic_card
    from taa.signals import Matcher
    card = heuristic_card(factory_lead, rubric, Matcher(signals))
    text = offline_draft(card, rubric)
    assert text.startswith("Hi [First name]") and "[Recruiter name]" in text
    fake = FakeLLM(lambda s, u, sc: "Hi [First name] — saw your work.")
    out = draft_outreach(fake, card, rubric, "claude-opus-5-5", "medium")
    assert "—" not in out
    assert "VERIFIED EVIDENCE" in fake.calls[0]["user"] and "Robin" not in fake.calls[0]["user"]


class _StubMessages:
    def __init__(self, resp):
        self.resp, self.kwargs = resp, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def _resp(stop_reason="end_turn", text='{"ok": true}'):
    return SimpleNamespace(stop_reason=stop_reason, model="claude-opus-5-5", _request_id="req_1",
                           content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0),
                           stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None)


def test_claude_client_request_shape(settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    client = ClaudeClient(replace(settings, llm_provider="anthropic", fallbacks=True))
    stub = _StubMessages(_resp())
    client._client = SimpleNamespace(beta=SimpleNamespace(messages=stub), messages=None)
    out = client.complete(model="claude-opus-5-5", system=[{"type": "text", "text": "x"}], user="u", effort="high",
                          json_schema={"type": "object"})
    assert out.json() == {"ok": True}
    kw = stub.kwargs
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": {"type": "object"}}}


def test_claude_client_refusal_and_bedrock_ids(settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    client = ClaudeClient(replace(settings, llm_provider="anthropic", fallbacks=False))
    stub = _StubMessages(_resp("refusal"))
    client._client = SimpleNamespace(messages=stub)
    with pytest.raises(LLMRefusal):
        client.complete(model="claude-opus-5-5", system=[], user="u", effort="high")
    assert "fallbacks" not in stub.kwargs
    client.provider = "bedrock"
    assert client._model_id("claude-opus-5-5") == "anthropic.claude-opus-5-5"
    with pytest.raises(LLMError):
        ClaudeClient(replace(settings, llm_provider="bedrock", bedrock_region=None))
