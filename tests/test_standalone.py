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
    templates = json.loads(re.search(r"const TEMPLATES = (\[.*?\]);\nconst APP_VERSION", html, re.S).group(1))
    assert templates[0]["id"] == "sw_factory_lead" and len(templates[0]["criteria"]) == 7
    assert html.count('class="wm ') == 2
