from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_standalone_build_embeds_config():
    subprocess.run([sys.executable, str(ROOT / "standalone" / "build.py")], check=True, capture_output=True)
    html = (ROOT / "standalone" / "dist" / "talent-agent.html").read_text(encoding="utf-8")
    assert "__SIGNALS_JSON__" not in html and "__WORDMARK_LIGHT__" not in html
    signals = json.loads(re.search(r"const SIGNALS = (\{.*?\});\nconst TEMPLATES", html, re.S).group(1))
    assert "dod_software_factory" in signals["capabilities"]
    templates = json.loads(re.search(r"const TEMPLATES = (\[.*?\]);\n", html, re.S).group(1))
    assert templates[0]["id"] == "sw_factory_lead" and len(templates[0]["criteria"]) == 7
    assert html.count('class="wm ') == 2


def test_github_pages_build_is_a_full_document():
    subprocess.run([sys.executable, str(ROOT / "standalone" / "build.py")], check=True, capture_output=True)
    page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and 'window.TAA_HOST = "github"' in page
    assert page.index("</style>") < page.index("</head>") < page.index("<body>") < page.index('<main id="view">')
    assert (ROOT / "site" / ".nojekyll").exists()


def test_sample_csv_imports_like_the_json_sample(rubric, signals, settings):
    from taa.connectors.file_import import load_file
    from taa.pipeline.filters import DoNotTarget
    from taa.pipeline.run import run_pipeline

    subprocess.run([sys.executable, str(ROOT / "standalone" / "build.py")], check=True, capture_output=True)
    csv_path = ROOT / "data" / "samples" / "sample_candidates.csv"
    from_csv = load_file(csv_path.name, csv_path.read_bytes())
    json_path = ROOT / "data" / "samples" / "synthetic_candidates.json"
    from_json = load_file(json_path.name, json_path.read_bytes())
    assert [p.full_name for p in from_csv] == [p.full_name for p in from_json]
    assert [len(p.positions) for p in from_csv] == [min(len(p.positions), 3) for p in from_json]
    a = run_pipeline(from_csv, rubric, signals, DoNotTarget(), settings)
    b = run_pipeline(from_json, rubric, signals, DoNotTarget(), settings)
    assert [(c.profile.full_name, c.total) for c in a.ranked] == [(c.profile.full_name, c.total) for c in b.ranked]
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert "const SAMPLE_CSV = " in html and "Avery,Lindqvist" in html
    assert (ROOT / "site" / "sample_candidates.csv").read_text(encoding="utf-8") == csv_path.read_text(encoding="utf-8")


def test_site_ships_word_guides():
    subprocess.run([sys.executable, str(ROOT / "standalone" / "build.py")], check=True, capture_output=True)
    for name in ("Legal_and_Sourcing_Guide.docx", "User_Guide.docx"):
        assert (ROOT / "site" / "docs" / name).read_bytes() == (ROOT / "docs" / name).read_bytes()
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert 'href="#legal"' in html and "Legal_and_Sourcing_Guide.docx" in html
