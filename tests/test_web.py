from __future__ import annotations

import base64
import csv
import io
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from taa.db import make_session_factory
from taa.service import Services
from taa.web.app import create_app


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


@pytest.fixture
def client(settings):
    app = create_app(settings, llm_client=None, session_factory=make_session_factory("sqlite:///:memory:"))
    return TestClient(app)


def _approve_template(client) -> int:
    page = client.get("/rubrics")
    rid = int(re.search(r'href="/rubrics/(\d+)"', page.text).group(1))
    detail = client.get(f"/rubrics/{rid}")
    r = client.post(f"/rubrics/{rid}/approve", data={"approver": "Hiring Manager", "csrf": _csrf(detail.text)},
                    follow_redirects=False)
    assert r.status_code == 303
    return rid


def test_end_to_end_search_review_export(client, sample_bytes):
    assert client.get("/healthz").text == "ok"
    rid = _approve_template(client)
    new = client.get(f"/searches/new?rubric_id={rid}")
    assert new.status_code == 200 and "Sourcing queries" in new.text
    files = [("files", (n, d, "application/octet-stream")) for n, d in sample_bytes.items()]
    r = client.post("/searches", data={"title": "Pilot", "rubric_id": rid, "csrf": _csrf(new.text)}, files=files,
                    follow_redirects=False)
    assert r.status_code == 303
    detail = client.get(r.headers["location"])
    assert "Review next" in detail.text and "Avery Lindqvist" in detail.text and "Failed" not in detail.text
    first = re.search(r'href="(/searches/\d+/results/\d+)">Avery Lindqvist', detail.text).group(1)
    cand = client.get(first)
    assert "Scores and evidence" in cand.text and "Lattice SDK" in cand.text
    review = client.post(first + "/review", data={"decision": "approve", "reason_code": "strong_fit",
                                                  "csrf": _csrf(cand.text)}, follow_redirects=False)
    assert review.status_code == 303
    out = client.post(first + "/outreach", data={"csrf": _csrf(cand.text)}, follow_redirects=True)
    assert "Hi [First name]" in out.text
    sid = first.split("/")[2]
    exported = client.get(f"/searches/{sid}/export.csv")
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert rows[0]["name"] == "Avery Lindqvist" and rows[0]["review"] == "approve"
    # Rerun keeps the review attached to the same candidate
    rr = client.post(f"/searches/{sid}/rerun", data={"csrf": _csrf(detail.text)}, follow_redirects=True)
    assert "approved" in rr.text
    audit = client.get("/audit").text
    for action in ("rubric.approved", "search.created", "search.completed", "candidate.reviewed", "search.exported"):
        assert action in audit


def test_search_requires_approved_rubric_and_csrf(client, sample_bytes):
    page = client.get("/rubrics")
    rid = int(re.search(r'href="/rubrics/(\d+)"', page.text).group(1))
    token = _csrf(client.get(f"/rubrics/{rid}").text)
    files = [("files", ("a.json", sample_bytes["synthetic_candidates.json"], "application/json"))]
    r = client.post("/searches", data={"title": "x", "rubric_id": rid, "csrf": token}, files=files)
    assert r.status_code == 400 and "approved rubric" in r.text
    r = client.post("/searches", data={"title": "x", "rubric_id": rid, "csrf": "bad"}, files=files)
    assert r.status_code == 400 and "Form expired" in r.text


def test_approved_rubric_edit_creates_new_version(client):
    rid = _approve_template(client)
    page = client.get(f"/rubrics/{rid}")
    yaml_text = re.search(r'<textarea id="yaml_text" name="yaml_text" class="code">(.*?)</textarea>', page.text, re.S).group(1)
    import html
    yaml_text = html.unescape(yaml_text).replace("weight: 20\n    heuristic", "weight: 25\n    heuristic", 1)
    r = client.post(f"/rubrics/{rid}", data={"yaml_text": yaml_text, "csrf": _csrf(page.text)}, follow_redirects=False)
    new_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert new_id != rid
    assert "v2" in client.get(f"/rubrics/{new_id}").text
    bad = client.post(f"/rubrics/{new_id}", data={"yaml_text": "criteria: []\nid: x\ntitle: y", "csrf": _csrf(page.text)})
    assert "Not saved" in bad.text


def test_basic_auth_when_configured(settings):
    s = replace(settings, app_username="ta", app_password="secret")
    c = TestClient(create_app(s, llm_client=None, session_factory=make_session_factory("sqlite:///:memory:")))
    assert c.get("/searches").status_code == 401
    good = "Basic " + base64.b64encode(b"ta:secret").decode()
    assert c.get("/searches", headers={"Authorization": good}).status_code == 200
    bad = "Basic " + base64.b64encode(b"ta:nope").decode()
    assert c.get("/searches", headers={"Authorization": bad}).status_code == 401


def test_trusted_header_identity(settings):
    s = replace(settings, trusted_user_header="X-Forwarded-User")
    c = TestClient(create_app(s, llm_client=None, session_factory=make_session_factory("sqlite:///:memory:")))
    assert c.get("/searches").status_code == 401
    r = c.get("/searches", headers={"X-Forwarded-User": "jdoe@astrion.us"})
    assert r.status_code == 200 and "jdoe@astrion.us" in r.text


def test_purge_keeps_approved(settings, sample_bytes):
    from datetime import timedelta

    from taa.db import SearchRow, utcnow
    sf = make_session_factory("sqlite:///:memory:")
    app = create_app(settings, llm_client=None, session_factory=sf)
    c = TestClient(app)
    rid = _approve_template(c)
    new = c.get(f"/searches/new?rubric_id={rid}")
    files = [("files", (n, d, "application/octet-stream")) for n, d in sample_bytes.items()]
    loc = c.post("/searches", data={"title": "Old", "rubric_id": rid, "csrf": _csrf(new.text)}, files=files,
                 follow_redirects=False).headers["location"]
    detail = c.get(loc).text
    first = re.search(r'href="(/searches/\d+/results/\d+)">', detail).group(1)
    c.post(first + "/review", data={"decision": "approve", "reason_code": "strong_fit", "csrf": _csrf(detail)})
    with sf() as s:
        for row in s.query(SearchRow):
            row.created_at = utcnow() - timedelta(days=400)
        s.commit()
    svc: Services = app.state.services
    out = svc.purge(365)
    assert out["results_removed"] == 11 and out["inputs_cleared"] == 2


def test_model_path_intake_scoring_outreach(settings, sample_bytes):
    from conftest import FakeLLM

    def responder(system, user, schema):
        if schema is None:
            return "Hi [First name], your platform work stood out. [Recruiter name]"
        props = schema["properties"]
        if "role_pitch" in props:
            return {"title": "SW Factory Lead", "summary": "Lead the factory", "role_pitch": "Build it",
                    "target_titles": ["Director of Engineering"], "target_companies": ["Anduril Industries"], "locations": [],
                    "criteria": [{"id": "org_size", "name": "Large org", "short": "Org", "type": "requirement", "weight": 50,
                                  "guidance": "4 = 100+", "signals": ["large_org_leadership"], "keywords": []},
                                 {"id": "factory", "name": "Factory", "short": "Factory", "type": "requirement", "weight": 50,
                                  "guidance": "4 = led one", "signals": ["dod_software_factory"], "keywords": []}]}
        ids = props["criteria"]["items"]["properties"]["criterion_id"]["enum"]
        quote = "Led 140 engineers building the Big Bang DevSecOps baseline and Iron Bank hardened containers."
        hit = quote.split(" building")[0] in user
        return {"summary": "Fit summary.", "criteria": [
            {"criterion_id": cid, "status": "scored" if hit else "unknown", "score": 4 if hit else 0,
             "evidence": [quote] if hit else [], "rationale": "r"} for cid in ids]}

    s = replace(settings, llm_provider="anthropic")
    c = TestClient(create_app(s, llm_client=FakeLLM(responder), session_factory=make_session_factory("sqlite:///:memory:")))
    page = c.get("/rubrics/new")
    r = c.post("/rubrics/intake", data={"request_text": "SW Factory Lead", "csrf": _csrf(page.text)}, follow_redirects=False)
    rid = int(r.headers["location"].rsplit("/", 1)[1])
    detail = c.get(f"/rubrics/{rid}")
    assert "intake" in c.get("/rubrics").text and "Large org" in detail.text
    c.post(f"/rubrics/{rid}/approve", data={"approver": "HM", "csrf": _csrf(detail.text)})
    new = c.get(f"/searches/new?rubric_id={rid}")
    files = [("files", (n, d, "application/octet-stream")) for n, d in sample_bytes.items()]
    loc = c.post("/searches", data={"title": "Model run", "rubric_id": rid, "csrf": _csrf(new.text)}, files=files,
                 follow_redirects=False).headers["location"]
    page = c.get(loc).text
    assert "llm:claude-opus-5-5" in page and "100.0%" in page
    first = re.search(r'href="(/searches/\d+/results/\d+)">Avery Lindqvist', page).group(1)
    cand = c.get(first).text
    assert "Fit summary." in cand
    out = c.post(first + "/outreach", data={"csrf": _csrf(cand)}, follow_redirects=True).text
    assert "your platform work stood out" in out
