"""Build the standalone app from standalone/app.html and the repo's config.

The signal dictionary (config/signals) and rubric templates (config/rubrics) are embedded so
the standalone app and the Python app score from the same source. Two outputs:

- standalone/dist/talent-agent.html  the claude.ai artifact (page content; claude.ai adds the document skeleton)
- site/index.html                    the GitHub Pages build (full document; data in the browser, Claude via the
                                     viewer's own API key and the SDK bundle at site/vendor/anthropic.js, built by
                                     `npm run bundle` in standalone/vendor)

    python standalone/build.py
"""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taa.llm.intake import rubric_from_yaml  # noqa: E402
from taa.signals import load_signals  # noqa: E402

LOGOS = ROOT / "src" / "taa" / "web" / "static" / "img"


def _js(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _wordmark(name: str, cls: str) -> str:
    svg = (LOGOS / name).read_text(encoding="utf-8").strip()
    return svg.replace("<svg ", f'<svg class="wm {cls}" ', 1)


SAMPLE_CSV = ROOT / "data" / "samples" / "sample_candidates.csv"
MAX_POSITIONS = 3


def write_sample_csv() -> str:
    """Write the downloadable sample from the synthetic profiles. It uses every column the importer
    recognizes, so it also serves as a template."""
    data = json.loads((ROOT / "data" / "samples" / "synthetic_candidates.json").read_text(encoding="utf-8"))
    header = ["First Name", "Last Name", "Headline", "Location", "LinkedIn URL", "Email", "Clearance", "Skills", "Summary"]
    for n in range(1, MAX_POSITIONS + 1):
        header += [f"position_{n}_{f}" for f in ("title", "company", "start", "end", "description")]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for p in data["profiles"]:
        first, _, last = p["full_name"].partition(" ")
        row = [first, last, p["headline"], p["location"], p["linkedin_url"], p["email"], p["clearance"],
               "; ".join(p["skills"]), p["summary"]]
        positions = p["positions"][:MAX_POSITIONS]
        for n in range(MAX_POSITIONS):
            pos = positions[n] if n < len(positions) else {}
            row += [pos.get("title", ""), pos.get("company", ""), pos.get("start") or "", pos.get("end") or "", pos.get("description", "")]
        w.writerow(row)
    text = buf.getvalue()
    SAMPLE_CSV.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    signals = load_signals(ROOT / "config" / "signals").model_dump(mode="json")
    templates = []
    for f in sorted((ROOT / "config" / "rubrics").glob("*.yaml")):
        data = rubric_from_yaml(f.read_text(encoding="utf-8")).model_dump(mode="json")
        for c in data["criteria"]:
            if c.get("heuristic") is None:
                c.pop("heuristic", None)
        templates.append(data)
    html = (ROOT / "standalone" / "app.html").read_text(encoding="utf-8")
    sample_csv = write_sample_csv()
    html = (html.replace("__SAMPLE_CSV__", _js(sample_csv))
                .replace("__SIGNALS_JSON__", _js(signals))
                .replace("__TEMPLATES_JSON__", _js(templates))
                .replace("__WORDMARK_LIGHT__", _wordmark("astrion-wordmark-light.svg", "for-dark"))
                .replace("__WORDMARK_DARK__", _wordmark("astrion-wordmark-dark.svg", "for-light")))
    if "__" in "".join(x for x in ("__SIGNALS_JSON__", "__TEMPLATES_JSON__") if x in html):
        raise SystemExit("placeholder left unreplaced")
    out = ROOT / "standalone" / "dist" / "talent-agent.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({len(html):,} bytes, {len(signals['capabilities'])} capabilities, {len(templates)} templates)")

    # GitHub Pages: a full document whose head carries what the artifact skeleton would add
    cut = html.index("</style>") + len("</style>")
    page = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '<script>window.TAA_HOST = "github";</script>\n' + html[:cut] + "\n</head>\n<body>\n" + html[cut:] + "\n</body>\n</html>\n")
    site = ROOT / "site"
    (site / "vendor").mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text(page, encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    (site / "sample_candidates.csv").write_text(sample_csv, encoding="utf-8")
    # Word guides linked from the Legal and Help pages
    (site / "docs").mkdir(exist_ok=True)
    for name in ("Legal_and_Sourcing_Guide.docx", "User_Guide.docx"):
        (site / "docs" / name).write_bytes((ROOT / "docs" / name).read_bytes())
    print(f"wrote site/index.html ({len(page):,} bytes); run `npm run bundle` in standalone/vendor for site/vendor/anthropic.js")


if __name__ == "__main__":
    main()
