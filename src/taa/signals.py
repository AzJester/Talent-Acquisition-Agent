"""Signal dictionary: maps employers, titles and keywords to capabilities.

Profiles rarely name a product ("Lattice", "Hivemind"), so evidence comes from where people
worked, what they were called and what they wrote. Recruiters and SMEs maintain the YAML in
config/signals/; adding an entry is the main way the tool improves."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from taa.pipeline.mask import MaskedProfile

Strength = Literal["strong", "medium", "weak"]
STRENGTH_RANK = {"strong": 3, "medium": 2, "weak": 1}


class Signal(BaseModel):
    employers: list[str] = Field(default_factory=list)
    title_patterns: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    strength: Strength = "medium"
    note: str = ""


class Capability(BaseModel):
    description: str = ""
    signals: list[Signal]


class SignalFlags(BaseModel):
    equity_heavy_employers: list[str] = Field(default_factory=list)


class SignalDictionary(BaseModel):
    capabilities: dict[str, Capability] = Field(default_factory=dict)
    flags: SignalFlags = Field(default_factory=SignalFlags)

    def merge(self, other: "SignalDictionary") -> "SignalDictionary":
        caps = dict(self.capabilities)
        for cid, cap in other.capabilities.items():
            if cid in caps:
                caps[cid] = Capability(description=caps[cid].description or cap.description,
                                       signals=caps[cid].signals + cap.signals)
            else:
                caps[cid] = cap
        flags = SignalFlags(equity_heavy_employers=self.flags.equity_heavy_employers + other.flags.equity_heavy_employers)
        return SignalDictionary(capabilities=caps, flags=flags)


def load_signals(path: Path) -> SignalDictionary:
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    out = SignalDictionary()
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        data.pop("version", None)
        out = out.merge(SignalDictionary.model_validate(data))
    return out


def _word_re(term: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term.strip())}(?![A-Za-z0-9])", re.I)


def employer_matches(company: str, names: list[str]) -> str | None:
    for n in names:
        if n.strip() and _word_re(n).search(company or ""):
            return n
    return None


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


def sentence_around(text: str, start: int, end: int, limit: int = 240) -> str:
    """Return the sentence containing text[start:end], as an exact substring of text."""
    s0 = 0
    for m in _SENTENCE_SPLIT.finditer(text):
        if m.end() <= start:
            s0 = m.end()
        elif m.start() >= end:
            e0 = m.start()
            break
    else:
        e0 = len(text)
    if e0 - s0 > limit:
        s0 = max(s0, start - limit // 2)
        e0 = min(e0, end + limit // 2)
    return text[s0:e0].strip()


def find_keyword(mp: MaskedProfile, keyword: str) -> str | None:
    """Return an exact quote from the masked profile containing the keyword, or None."""
    rx = _word_re(keyword)
    for section in mp.sections():
        m = rx.search(section)
        if m:
            return sentence_around(section, m.start(), m.end())
    return None


@dataclass
class SignalMatch:
    capability: str
    strength: Strength
    quote: str
    detail: str = ""


@dataclass
class Matcher:
    dictionary: SignalDictionary
    _title_cache: dict[str, re.Pattern] = field(default_factory=dict)

    def _title_re(self, pattern: str) -> re.Pattern:
        if pattern not in self._title_cache:
            self._title_cache[pattern] = re.compile(pattern, re.I)
        return self._title_cache[pattern]

    def match(self, mp: MaskedProfile, capabilities: list[str] | None = None) -> list[SignalMatch]:
        out: list[SignalMatch] = []
        for cid, cap in self.dictionary.capabilities.items():
            if capabilities is not None and cid not in capabilities:
                continue
            for sig in cap.signals:
                out.extend(self._match_signal(cid, sig, mp))
        return out

    def _match_signal(self, cid: str, sig: Signal, mp: MaskedProfile) -> list[SignalMatch]:
        found: list[SignalMatch] = []
        if sig.employers or sig.title_patterns:
            # Position-anchored signal: every specified component must match the same position
            for p in mp.positions:
                emp = employer_matches(p.company, sig.employers) if sig.employers else ""
                if emp is None:
                    continue
                if sig.title_patterns and not any(self._title_re(t).search(p.title) for t in sig.title_patterns):
                    continue
                quote = f"{p.title} | {p.company}" if p.title else p.company
                if sig.keywords:
                    kw_quote = None
                    for kw in sig.keywords:
                        for src in (p.description, p.title):
                            m = _word_re(kw).search(src)
                            if m:
                                kw_quote = sentence_around(src, m.start(), m.end())
                                break
                        if kw_quote:
                            break
                    if kw_quote is None:
                        continue
                    found.append(SignalMatch(cid, sig.strength, quote, f"keyword in role at {p.company}"))
                    found.append(SignalMatch(cid, sig.strength, kw_quote, "keyword"))
                else:
                    found.append(SignalMatch(cid, sig.strength, quote, emp or "title"))
        else:
            for kw in sig.keywords:
                q = find_keyword(mp, kw)
                if q:
                    found.append(SignalMatch(cid, sig.strength, q, f"keyword: {kw}"))
        return found
