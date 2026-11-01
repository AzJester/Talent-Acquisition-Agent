from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_site_ships_word_guides_and_video():
    subprocess.run([sys.executable, str(ROOT / "standalone" / "build.py")], check=True, capture_output=True)
    for name in ("Legal_and_Sourcing_Guide.docx", "User_Guide.docx", "Talent_Acquisition_Agent_Walkthrough.mp4"):
        assert (ROOT / "site" / "docs" / name).read_bytes() == (ROOT / "docs" / name).read_bytes()
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert 'href="#legal"' in html and "Legal_and_Sourcing_Guide.docx" in html
    # The video downloads from the GitHub site itself: the empty Searches page, the Help page's Video note and its end
    assert 'const FILM_MP4 = "docs/Talent_Acquisition_Agent_Walkthrough.mp4"' in html
    assert html.count('href="${FILM_MP4}" download') == 2 and 'docLink("Talent_Acquisition_Agent_Walkthrough.mp4"' in html
    mp4 = (ROOT / "docs" / "Talent_Acquisition_Agent_Walkthrough.mp4").read_bytes()
    assert mp4[4:8] == b"ftyp" and len(mp4) < 50_000_000  # an MP4, and small enough for the repository


def test_github_site_loads_no_third_party_scripts():
    subprocess.run([sys.executable, str(ROOT / "standalone" / "build.py")], check=True, capture_output=True)
    page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    assert "cdnjs.cloudflare.com" not in page
    assert 'src="vendor/js-yaml.min.js"' in page and 'src="vendor/papaparse.min.js"' in page
    # The startup watchdog runs before the main script and the main script marks itself started
    assert page.index("App did not start") < page.index("window.__taaStarted = true")


def test_browser_rounding_matches_python():
    """The page's totals must print exactly what src/taa/pipeline/rank.py prints, ties included."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = (ROOT / "standalone" / "app.html").read_text(encoding="utf-8")
    fn = re.search(r"function pyRound\(x, d\) \{.*?\n\}", html, re.DOTALL).group(0)
    values = [(100 * (p4 / 4) / tw, 1) for tw in range(1, 121) for p4 in range(tw * 4 + 1)]
    values += [((p4 / 4) / tw, 3) for tw in range(1, 121) for p4 in range(tw * 4 + 1)]
    values.append((21.25, 1))  # Alex Brennan in the sample data: Python prints 21.2
    script = fn + "\nconst v = JSON.parse(require('fs').readFileSync(0, 'utf8'));\nprocess.stdout.write(JSON.stringify(v.map(([x, d]) => pyRound(x, d))));"
    out = subprocess.run([node, "-e", script], input=json.dumps(values), capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)
    bad = [(x, d, g) for (x, d), g in zip(values, got) if round(x, d) != g]
    assert not bad, bad[:5]
    assert got[-1] == 21.2
