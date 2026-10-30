from __future__ import annotations

import json

import httpx

from taa.connectors.file_import import load_file
from taa.connectors.people_data_labs import PeopleDataLabs, map_person

PDL_PERSON = {
    "id": "abc123",
    "full_name": "jane sample",
    "headline": "vp engineering",
    "location_name": "huntsville, alabama, united states",
    "linkedin_url": "linkedin.com/in/example-jane-sample",
    "skills": ["devsecops", "kubernetes"],
    "experience": [
        {"company": {"name": "platform one"}, "title": {"name": "director of engineering"},
         "start_date": "2019-04", "end_date": None, "is_primary": True, "summary": "Led 90 engineers."},
        {"company": {"name": "atlassian"}, "title": {"name": "engineering manager"},
         "start_date": "2014-01", "end_date": "2019-03"},
    ],
    "education": [{"school": {"name": "example university"}, "degrees": ["bachelors"], "majors": ["computer science"],
                   "start_date": "2004", "end_date": "2008"}],
}


def test_map_pdl_person():
    p = map_person(PDL_PERSON)
    assert p.source == "pdl" and p.full_name == "Jane Sample"
    assert p.linkedin_url == "https://linkedin.com/in/example-jane-sample"
    assert p.positions[0].company == "Platform One" and p.positions[0].is_current
    assert p.positions[1].end == "2019-03"
    assert p.education[0].school == "example university"


def test_pdl_search_uses_api_key_and_sql():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("X-Api-Key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": 200, "data": [PDL_PERSON], "total": 1})

    pdl = PeopleDataLabs("k-test", transport=httpx.MockTransport(handler))
    out = pdl.search("SELECT * FROM person WHERE job_title='x'", size=5)
    assert seen["key"] == "k-test" and seen["body"]["size"] == 5 and "SELECT" in seen["body"]["sql"]
    assert len(out) == 1 and out[0].source_id == "abc123"


def test_pdl_no_matches_is_empty():
    pdl = PeopleDataLabs("k", transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"status": 404})))
    assert pdl.search("SELECT * FROM person") == []


def test_csv_header_aliases_and_position_columns():
    csv_text = (
        "Name,Title,Company,LinkedIn URL,Security Clearance,Top Skills,"
        "position_1_title,position_1_company,position_1_start,position_1_end,position_1_description\n"
        "Lee Example,Director,Acme,https://linkedin.com/in/example-lee,Active Secret,Go; Rust,"
        "Director,Acme,2020-01,,Led 30 engineers\n"
    )
    [p] = load_file("export.csv", csv_text.encode())
    assert p.full_name == "Lee Example" and p.clearance == "Active Secret"
    assert p.skills == ["Go", "Rust"]
    assert p.positions[0].description == "Led 30 engineers" and p.positions[0].is_current


def test_json_accepts_pdl_records_and_wrapped_profiles():
    out = load_file("pdl.json", json.dumps({"data": [PDL_PERSON]}).encode())
    assert out[0].full_name == "Jane Sample" and out[0].sources == ["pdl"]
    out = load_file("p.json", json.dumps({"profiles": [{"full_name": "X Y", "positions": [{"title": "T", "company": "C"}]}]}).encode())
    assert out[0].full_name == "X Y" and out[0].source == "p"
