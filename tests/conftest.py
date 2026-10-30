from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from taa.config import load_settings
from taa.llm.client import LLMError, LLMRefusal, LLMResponse
from taa.llm.intake import rubric_from_yaml
from taa.models import Position, Profile
from taa.signals import load_signals

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PDL_API_KEY", raising=False)
    s = load_settings()
    return replace(s, database_url="sqlite:///:memory:", config_dir=ROOT / "config", llm_provider="off",
                   app_username=None, app_password=None, trusted_user_header=None, pdl_api_key=None)


@pytest.fixture
def signals():
    return load_signals(ROOT / "config" / "signals")


@pytest.fixture
def rubric():
    return rubric_from_yaml((ROOT / "config" / "rubrics" / "sw_factory_lead.yaml").read_text())


@pytest.fixture
def sample_bytes():
    return {
        "synthetic_candidates.json": (ROOT / "data/samples/synthetic_candidates.json").read_bytes(),
        "synthetic_recruiter_export.csv": (ROOT / "data/samples/synthetic_recruiter_export.csv").read_bytes(),
    }


def make_profile(**kw) -> Profile:
    base = dict(source="test", source_id="1", full_name="Test Person")
    base.update(kw)
    if "positions" in base:
        base["positions"] = [p if isinstance(p, Position) else Position(**p) for p in base["positions"]]
    return Profile(**base)


@pytest.fixture
def factory_lead():
    return make_profile(
        full_name="Robin Example", email="robin@example.com", linkedin_url="https://www.linkedin.com/in/example-robin",
        location="Huntsville, AL", headline="Software factory leader",
        summary="Robin Example builds teams. Reach me at robin@example.com or 256-555-0100.",
        clearance="Active TS/SCI",
        positions=[
            {"title": "Director of Engineering", "company": "Platform One", "start": "2019-01", "end": None,
             "description": "Led 120 engineers. Took the platform to a continuous ATO across multiple programs."},
            {"title": "Product Manager", "company": "Atlassian", "start": "2014-01", "end": "2018-12",
             "description": "Owned the product roadmap for an enterprise SaaS product."},
        ],
        education=[{"school": "Example University", "degree": "BS", "field": "CS", "start": "2004", "end": "2008"}],
    )


class FakeLLM:
    """Stands in for ClaudeClient. `responder(system, user, schema)` returns a dict or raises."""

    provider = "fake"

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def complete(self, *, model, system, user, effort, max_tokens=16000, json_schema=None):
        self.calls.append({"model": model, "system": system, "user": user, "effort": effort, "schema": json_schema})
        out = self.responder(system, user, json_schema)
        if isinstance(out, Exception):
            raise out
        text = out if isinstance(out, str) else json.dumps(out)
        return LLMResponse(text=text, model=model, stop_reason="end_turn", request_id="req_test",
                           usage={"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 800,
                                  "cache_creation_input_tokens": 0})


@pytest.fixture
def fake_llm_factory():
    return FakeLLM


__all__ = ["make_profile", "FakeLLM", "LLMError", "LLMRefusal"]
