"""Deterministic scorer built on the signal dictionary.

Runs with no model access, pre-ranks candidates before LLM scoring, and serves as the
fallback when an LLM call fails. Every quote it produces is an exact substring of the
masked profile, so its evidence always verifies.

The same rules run in the browser app (heuristicCard in standalone/app.html); the two must
give identical scores. Keyword rules are approximate by nature: Claude scoring reads the nuance."""
from __future__ import annotations

import re

from taa.models import CriterionScore, Profile, Rubric, ScoreCard
from taa.pipeline.mask import MaskedProfile, masked_view
from taa.signals import STRENGTH_RANK, Matcher, _word_re, find_keyword, sentence_around

_NUM_V1 = r"(\d{1,3}(?:,\d{3})*|\d+)"  # rules 1 keep the pattern they were approved with
_NUM = r"([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)"
_PEOPLE_V1 = r"(?:-\s*person|person|people|engineers|developers|staff|employees|software engineers|engineering staff|team members|FTEs?)"
_PEOPLE = _PEOPLE_V1
_REPORTS = r"(?:engineers|developers|staff|software engineers|engineering staff|team members|FTEs?)"  # not people or employees: company size
_LEADER = r"(?:director|s?vp|evp|vice\s+president|head\s+of|chief|cto|manager)\b"
# A leadership title in title position: start of the field, or after "|", "," or "(", with at most three words before it
_TITLE_POS = r"(?:^|[|,(])\s*(?:[A-Za-z0-9&/-]+\s+){0,3}?"
# Text between a title or verb and a head count, stopping at words that mean the count is not the person's own team
_GAP = (r"(?:(?!\b(?:to|for|at|by|under|reporting|supporting|hired|hiring|recruited|recruiting|onboarded|trained|training|"
        r"interviewed|presented|served|used|community|startup|company|firm|customers|users)\b)[^.\n;]){0,80}?")
_TEAM_V1 = [
    re.compile(r"\b(?:led|lead|leading|manag(?:e|ed|es|ing)|built|grew|scaled|oversaw|oversee|oversees|direct(?:ed|s|ing)?|ran|run|runs)\b"
               r"[^.\n;]{0,60}?\b" + _NUM_V1 + r"\+?\s*" + _PEOPLE_V1 + r"\b", re.I),
    re.compile(r"\b(?:team|org|organization|organisation|department|group|workforce) of\s+" + _NUM_V1 + r"\+?", re.I),
    re.compile(r"\b" + _NUM_V1 + r"\+?\s*(?:-\s*person|person)\s+(?:team|org|organization|engineering)", re.I),
]
# Scoring rules 2: ASCII digits, "overseeing", and head counts tied to the person's own leadership:
# "Leads 120 engineers", "Director of DevSecOps over 6 teams (65 engineers)", "VP, Software | 200+ engineers"
_TEAM_V2 = [
    re.compile(r"\b(?:led|lead|leading|manag(?:e|ed|es|ing)|built|grew|scaled|oversaw|oversee|oversees|overseeing|direct(?:ed|s|ing)?|ran|run|runs)\b"
               r"[^.\n;]{0,60}?\b" + _NUM + r"\+?\s*" + _PEOPLE + r"\b", re.I),
    re.compile(r"\b(?:team|org|organization|organisation|department|group|workforce) of\s+" + _NUM + r"\+?", re.I),
    re.compile(r"\b" + _NUM + r"\+?\s*(?:-\s*person|person)\s+(?:team|org|organization|engineering)", re.I),
    re.compile(r"\b(?:leads|heads)\s+(?:(?:a|an|the|over|about|nearly|approximately|more\s+than|up\s+to)\s+)?" + _NUM + r"\+?\s*" + _PEOPLE + r"\b", re.I),
    re.compile(_TITLE_POS + r"\b" + _LEADER + _GAP + r"\(\s*" + _NUM + r"\+?\s*" + _REPORTS + r"\s*\)", re.I),
    re.compile(r"\b(?:led|leads|leading|manages|managed|managing|oversees|oversaw|overseeing|directs|directed|directing)\b" + _GAP
               + r"\(\s*" + _NUM + r"\+?\s*" + _REPORTS + r"\s*\)", re.I),
    re.compile(_TITLE_POS + r"\b" + _LEADER + _GAP + r"(?:\|\s*" + _NUM + r"\+?|\b" + _NUM + r"\+)\s*" + _REPORTS + r"\b", re.I),
]

_PUNCT = re.compile(r"[,:;()]")


def _ascii_word_re(term: str) -> re.Pattern:
    """Like signals._word_re, but folding case for ASCII letters only, as the browser's "i" flag does, so the two
    apps agree on letters such as the dotless i and the Kelvin sign (rules 2)."""
    return re.compile(_word_re(term).pattern, re.I | re.A)
_NEG_BEFORE = {"no", "not", "without", "never", "lacks", "lack", "lacking", "limited"}
_ACTIVE_NEG = {"not", "no", "none", "never", "was", "formerly", "previously", "once"}


def team_size(mp: MaskedProfile, rules: int = 2) -> tuple[int, str] | None:
    best: tuple[int, str] | None = None
    for section in mp.sections():
        for rx in (_TEAM_V2 if rules >= 2 else _TEAM_V1):
            for m in rx.finditer(section):
                n = int(next(g for g in m.groups() if g).replace(",", ""))
                if best is None or n > best[0]:
                    best = (n, sentence_around(section, m.start(), m.end()))
    return best


def _team_score(n: int) -> int:
    if n >= 100:
        return 4
    if n >= 50:
        return 3
    if n >= 20:
        return 2
    if n >= 5:
        return 1
    return 0


def _words(text: str) -> list[str]:
    return [_PUNCT.sub("", w.lower()) for w in text.split()]


def _words_before(text: str, idx: int, n: int) -> list[str]:
    """Up to n words just before idx, within the same sentence."""
    seg = text[:idx]
    seg = seg[max(seg.rfind(c) for c in ".;!?") + 1:]
    return seg.split()[-n:]


def _attached(text: str, end: int) -> str:
    """Words attached to a keyword: a parenthesis right after it, then the rest of its clause."""
    rest = text[end:]
    m = re.match(r"\s*\([^)]*\)", rest)
    paren = m.group(0) if m else ""
    return paren + re.split(r"[,;.()]", rest[len(paren):], maxsplit=1)[0]


def _capped(window: str, cap) -> bool:
    """True when the words around a keyword hit have a "when" term (inactive, expired...) and no "unless" term
    (active) that is itself un-negated: not "was active", "formerly active", "no longer active", "active until"."""
    if not any(_ascii_word_re(t).search(window) for t in cap.when):
        return False
    for t in cap.unless:
        for m in _ascii_word_re(t).finditer(window):
            pre = _words(window[:m.start()])[-3:]
            post = _words(window[m.end():])[:1]
            if not (any(w in _ACTIVE_NEG for w in pre) or (post and post[0] in {"until", "through"})):
                return False
    return True


def _keyword_hit(mp: MaskedProfile, kw: str, base: int, why: str, cap) -> tuple[int, str, str] | None:
    """Scoring rules 2: look at every mention, skip negated ones ("no SQL experience"), cap each by the words
    attached to it, and keep the best."""
    best: tuple[int, str, str] | None = None
    rx = _ascii_word_re(kw)
    for section in mp.sections():
        for m in rx.finditer(section):
            before = _words_before(section, m.start(), 4)
            if any(w in _NEG_BEFORE for w in _words(" ".join(before))[-2:]):
                continue
            score, reason = base, why
            if cap and score > cap.score:
                window = " ".join(before) + " " + section[m.start():m.end()] + _attached(section, m.end())
                if _capped(window, cap):
                    score, reason = cap.score, why + f", capped at {cap.score}"
            if best is None or score > best[0]:
                best = (score, sentence_around(section, m.start(), m.end()), reason)
    return best


def heuristic_card(profile: Profile, rubric: Rubric, matcher: Matcher, mp: MaskedProfile | None = None) -> ScoreCard:
    mp = mp or masked_view(profile)
    v2 = rubric.scoring >= 2
    all_caps = sorted({c for crit in rubric.criteria for c in crit.signals})
    matches = matcher.match(mp, all_caps) if all_caps else []
    scores: list[CriterionScore] = []
    for crit in rubric.criteria:
        hits: list[tuple[int, str, str, str]] = []  # (score, quote, why, kind)
        for m in matches:
            if m.capability in crit.signals:
                hits.append((STRENGTH_RANK[m.strength], m.quote, f"{m.capability} ({m.strength})", "signal"))
        # Keywords with their own score use it. Under scoring rules 2 a plain keyword on an item with no signals,
        # no team-size rule and no scored keywords is a found/not-found check worth full credit; otherwise it
        # supports at 2. (Rules 1, for rubrics approved before beta 1.2.4: first mention only, every plain keyword 2.)
        found_only = v2 and not crit.signals and not crit.heuristic and not crit.keyword_scores
        for kw in crit.keywords:
            if kw in crit.keyword_scores:
                base, why = crit.keyword_scores[kw], f"keyword '{kw}' ({crit.keyword_scores[kw]} of 4)"
            else:
                base, why = (4 if found_only else 2), f"keyword '{kw}'"
            if v2:
                hit = _keyword_hit(mp, kw, base, why, crit.keyword_cap)
                if hit:
                    hits.append((*hit, "keyword"))
            else:
                q = find_keyword(mp, kw)
                if q:
                    hits.append((base, q, why, "keyword"))
        if crit.heuristic == "team_size":
            ts = team_size(mp, rubric.scoring)
            if ts:
                hits.append((_team_score(ts[0]), ts[1], f"team size {ts[0]}", "team"))
        if not hits:
            scores.append(CriterionScore(criterion_id=crit.id, score=None, rationale="No matching evidence found."))
            continue
        hits.sort(key=lambda h: -h[0])
        best = hits[0][0]
        # Two strong signal or team-size quotes lift a 3 to a 4; keyword hits never do
        strong_quotes = {q for s, q, _, kind in hits if s >= 3 and kind != "keyword"}
        if best == 3 and len(strong_quotes) >= 2:
            best = 4
        evidence: list[str] = []
        for _, q, _, _ in hits:
            if q not in evidence:
                evidence.append(q)
            if len(evidence) == 3:
                break
        why = sorted({w for _, _, w, _ in hits})
        if found_only and best == 4:
            scores.append(CriterionScore(criterion_id=crit.id, score=best, evidence=evidence, basis="found",
                                         rationale="Found " + ", ".join(why[:4]) + "."))
            continue
        scores.append(CriterionScore(criterion_id=crit.id, score=best, evidence=evidence,
                                     rationale=("Found " if found_only else "Matched ") + ", ".join(why[:4]) + "."))
    known = [s for s in scores if s.score is not None]
    summary = f"Signal match on {len(known)} of {len(scores)} criteria."
    return ScoreCard(scores=scores, summary=summary, scorer="heuristic")
