"""Build the standalone claude.ai app from standalone/app.html and the repo's config.

The signal dictionary (config/signals) and rubric templates (config/rubrics) are embedded so
the standalone app and the Python app score from the same source. Output: standalone/dist/talent-agent.html

    python standalone/build.py
"""
from __future__ import annotations

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
    html = (html.replace("__SIGNALS_JSON__", _js(signals))
                .replace("__TEMPLATES_JSON__", _js(templates))
                .replace("__WORDMARK_LIGHT__", _wordmark("astrion-wordmark-light.svg", "for-dark"))
                .replace("__WORDMARK_DARK__", _wordmark("astrion-wordmark-dark.svg", "for-light")))
    if "__" in "".join(x for x in ("__SIGNALS_JSON__", "__TEMPLATES_JSON__") if x in html):
        raise SystemExit("placeholder left unreplaced")
    out = ROOT / "standalone" / "dist" / "talent-agent.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({len(html):,} bytes, {len(signals['capabilities'])} capabilities, {len(templates)} templates)")


if __name__ == "__main__":
    main()
