"""Command line: run the web app, run batch searches, plan queries, calibrate, purge."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from taa.config import load_settings


def _load_profiles(paths: list[str]):
    from taa.connectors.file_import import load_file
    profiles = []
    for p in paths:
        path = Path(p)
        profiles.extend(load_file(path.name, path.read_bytes()))
    return profiles


def cmd_serve(args) -> int:
    import uvicorn
    from taa.web.app import create_app
    settings = load_settings()
    if args.host not in {"127.0.0.1", "localhost"} and not (settings.app_password or settings.trusted_user_header):
        print("Refusing to listen on a non-local address without TAA_USERNAME/TAA_PASSWORD or TAA_TRUSTED_USER_HEADER. "
              "Candidate data is PII.", file=sys.stderr)
        return 2
    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


def cmd_run(args) -> int:
    from taa.llm.client import make_client
    from taa.llm.intake import rubric_from_yaml
    from taa.pipeline.filters import load_do_not_target
    from taa.pipeline.run import run_pipeline
    from taa.signals import load_signals

    settings = load_settings()
    rubric = rubric_from_yaml(Path(args.rubric).read_text(encoding="utf-8"))
    signals = load_signals(settings.signals_path)
    dnt = load_do_not_target(Path(args.do_not_target) if args.do_not_target else settings.do_not_target_path)
    client = None if args.heuristic else make_client(settings)
    profiles = _load_profiles(args.input)
    result = run_pipeline(profiles, rubric, signals, dnt, settings, client,
                          progress=lambda m: print(f"  {m}", file=sys.stderr))
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    w = csv.writer(out)
    w.writerow(["rank", "name", "linkedin_url", "total", "coverage", "requirement_unknowns"]
               + [c.id for c in rubric.criteria] + ["flags", "key"])
    for c in result.ranked:
        row = [c.rank, c.profile.full_name, c.profile.linkedin_url, c.total, c.coverage, c.requirement_unknowns]
        for crit in rubric.criteria:
            s = c.card.get(crit.id)
            row.append("" if s is None or s.score is None else s.score)
        w.writerow(row + ["; ".join(c.flags), c.key])
    if args.out:
        out.close()
    print(json.dumps({k: v for k, v in result.stats.items()}, indent=2), file=sys.stderr)
    for e in result.excluded:
        print(f"EXCLUDED {e.profile.full_name or e.key}: {e.reason}", file=sys.stderr)
    return 0


def cmd_plan(args) -> int:
    from taa.llm.intake import rubric_from_yaml
    from taa.query_planner import plan_queries
    from taa.signals import load_signals
    settings = load_settings()
    plan = plan_queries(rubric_from_yaml(Path(args.rubric).read_text(encoding="utf-8")), load_signals(settings.signals_path))
    print("BOOLEAN KEYWORDS (LinkedIn Recruiter, ClearanceJobs):\n" + (plan.linkedin_boolean or "(none)"))
    print("\nCOMPANY FILTER:\n" + ", ".join(plan.companies))
    print("\nTITLE FILTER:\n" + ", ".join(plan.titles))
    print("\nPEOPLE DATA LABS SQL:\n" + plan.pdl_sql)
    return 0


def cmd_eval(args) -> int:
    from taa.evals import TARGETS, precision_at_k, read_keys, recruiter_agreement
    tool = read_keys(Path(args.slate), args.column)
    out = {}
    if args.recruiter_top:
        out["recruiter_agreement"] = recruiter_agreement(tool, read_keys(Path(args.recruiter_top), args.column),
                                                         args.k_recruiter, args.k_tool)
    if args.approved:
        out["precision_at_25"] = precision_at_k(tool, set(read_keys(Path(args.approved), args.column)), args.k_tool)
    for k, v in out.items():
        target = TARGETS.get(k)
        verdict = "n/a" if v is None else ("meets target" if v >= target else "below target")
        print(f"{k}: {v} (target {target}, {verdict})")
    return 0


def cmd_purge(args) -> int:
    from taa.db import make_session_factory
    from taa.service import Services
    settings = load_settings()
    svc = Services(settings, make_session_factory(settings.database_url))
    print(json.dumps(svc.purge(args.older_than_days)))
    return 0


def cmd_check(args) -> int:
    from taa.llm.intake import rubric_from_yaml
    from taa.pipeline.filters import load_do_not_target
    from taa.signals import load_signals
    settings = load_settings()
    signals = load_signals(settings.signals_path)
    print(f"Signal dictionary: {len(signals.capabilities)} capabilities")
    ok = True
    for f in sorted(settings.rubrics_path.glob("*.yaml")):
        r = rubric_from_yaml(f.read_text(encoding="utf-8"))
        missing = sorted({s for c in r.criteria for s in c.signals if s not in signals.capabilities})
        print(f"Rubric {f.name}: {len(r.criteria)} criteria, weight {r.total_weight:g}" + (f", unknown signals {missing}" if missing else ""))
        ok &= not missing
    print(f"Do-not-target entries: {len(load_do_not_target(settings.do_not_target_path).employers)}")
    print(f"LLM provider: {settings.llm_provider} / scoring model {settings.scoring_model}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taa", description="Astrion Talent Acquisition Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the recruiter web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("run", help="score candidate files against a rubric and write a ranked CSV")
    p.add_argument("--rubric", required=True)
    p.add_argument("--input", action="append", required=True, help="CSV or JSON file; repeat for several")
    p.add_argument("--out", help="output CSV (default stdout)")
    p.add_argument("--do-not-target", help="override config/do_not_target.yaml")
    p.add_argument("--heuristic", action="store_true", help="skip the model even if one is configured")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("plan-queries", help="print sourcing queries for a rubric")
    p.add_argument("--rubric", required=True)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("eval", help="calibration metrics against recruiter picks")
    p.add_argument("--slate", required=True, help="tool output CSV (from run or the web export)")
    p.add_argument("--recruiter-top", help="CSV of the recruiter's top picks in order")
    p.add_argument("--approved", help="CSV of candidates approved for outreach")
    p.add_argument("--column", help="identifier column shared by the files (default: key, linkedin_url or name)")
    p.add_argument("--k-recruiter", type=int, default=10)
    p.add_argument("--k-tool", type=int, default=25)
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("purge", help="retention: delete unapproved candidates from old searches")
    p.add_argument("--older-than-days", type=int, default=365)
    p.set_defaults(fn=cmd_purge)

    p = sub.add_parser("check-config", help="validate signal dictionary, rubrics and do-not-target list")
    p.set_defaults(fn=cmd_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
