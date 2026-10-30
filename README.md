# Talent Acquisition Agent

AI-assisted sourcing and ranking tool for Astrion Talent Acquisition. It takes an approved hiring rubric and candidate records from licensed sources, scores every candidate on every criterion with quoted evidence, verifies each quote against the source, and gives recruiters a ranked slate to review. Recruiters make every contact decision; the tool never contacts anyone.

**Open the app:** https://azjester.github.io/Talent-Acquisition-Agent/ (no sign-in needed; data stays in your browser; Claude scoring is optional with an Anthropic API key entered under Governance).

**Sample candidate file:** [data/samples/sample_candidates.csv](data/samples/sample_candidates.csv) (fictional people; also downloadable from the app's New search and Help pages).

How each version is deployed, who can see what, how to change it, and the AWS self-hosting path are in [docs/BUILD_NOTES.md](docs/BUILD_NOTES.md). The legal concerns and how to search LinkedIn and other sites lawfully are in `docs/Legal_and_Sourcing_Guide.docx`. Instructions for users are in `docs/User_Guide.docx` and on the app's Help page.

The repository holds the same tool in two forms:

- `standalone/`: the browser app the team uses, published to GitHub Pages. Build it with `python standalone/build.py`.
- `src/taa/`: the self-hosted Python app (FastAPI), for when Astrion runs it on its own infrastructure.

Background: the build plan is in [docs/PLAN.md](docs/PLAN.md) (Word version: `docs/Talent_Acquisition_Agent_Build_Plan.docx`). `docs/pilot-brief.html` is superseded; Astrion chose direct deployment over a pilot.

## What it does

1. **Rubric intake and approval.** Start from a template in `config/rubrics/`, paste YAML, or paste a hiring request and have Claude draft the rubric. A hiring manager approves it. Approved versions are locked; edits create a new version.
2. **Sourcing queries.** For each rubric the tool generates Boolean strings and company/title filters to paste into LinkedIn Recruiter and ClearanceJobs, plus a People Data Labs SQL query.
3. **Candidate intake.** Upload CSV or JSON exports from licensed tools (LinkedIn Recruiter, ClearanceJobs, the ATS) and, optionally, run a People Data Labs search. The tool does not scrape LinkedIn, Indeed or anything else.
4. **Resolve and filter.** Duplicate records across sources merge into one candidate. The do-not-target list (non-solicitation terms, owned by Contracts) and the rubric's location gate exclude candidates before scoring.
5. **Score.** Every candidate gets a deterministic signal-dictionary score. With a model configured, the top candidates are then scored by Claude, criterion by criterion, with quotes. The scorer sees a masked profile: no name, contact details, location, schools or graduation years.
6. **Verify.** Every quote must appear in the masked profile text. Quotes that do not are removed and the criterion becomes unknown. The search page reports the verification rate.
7. **Rank in code.** Totals are a weighted sum computed in code, never a model's ranking. Unknown is shown separately from a low score.
8. **Review.** Recruiters approve or reject each candidate with a reason code, draft outreach (template or Claude), and export the slate as CSV. Reviews survive reruns.
9. **Audit.** Rubric changes and approvals, searches, model runs, candidate views, reviews and exports are logged.

## Quick start

```bash
python3 -m venv .venv
```

```bash
. .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

```bash
taa serve
```

Open http://127.0.0.1:8000. Approve the Software Factory Lead rubric under Rubrics, start a search, and upload the two files in `data/samples/` (synthetic, fictional people). Without a model key the tool runs in heuristic mode, which is enough to see the full workflow.

To enable Claude scoring, set `ANTHROPIC_API_KEY` (or the Bedrock settings) before `taa serve`. All settings are in `.env.example`.

Batch use without the web app:

```bash
taa run --rubric config/rubrics/sw_factory_lead.yaml --input data/samples/synthetic_candidates.json --input data/samples/synthetic_recruiter_export.csv --out slate.csv
```

Other commands: `taa plan-queries`, `taa eval` (calibration metrics), `taa purge` (retention), `taa check-config`.

## Configuration that Astrion owns

| File | Owner | Purpose |
|---|---|---|
| `config/signals/*.yaml` | Senior recruiter plus a technical SME | Signal dictionary: employers, titles and terms that indicate each capability. Add entries when the tool misses someone. |
| `config/rubrics/*.yaml` | Recruiter and hiring manager | Rubric templates. Loaded as drafts; each needs approval in the app. |
| `config/do_not_target.yaml` | Contracts | Non-solicitation list from teaming agreements, subcontracts and NDAs. Ships empty on purpose. If the list is sensitive, mount it at deploy time instead of committing it. |

## Model use

- Default model `claude-opus-5-5` with adaptive thinking at `high` effort for scoring and `medium` for outreach drafts. Override with `TAA_SCORING_MODEL` and the effort settings.
- Structured JSON output, with the criterion ids constrained to the rubric's ids.
- The instructions and rubric are a cached prompt prefix shared by every candidate in a search.
- On the Claude API, server-side refusal fallback is on (`fallbacks: "default"`). Turn it off with `TAA_LLM_FALLBACKS=false`. A failed or declined call falls back to the heuristic score for that candidate and flags it.
- `TAA_LLM_PROVIDER=bedrock` sends calls through Amazon Bedrock so candidate data stays inside Astrion's AWS account.
- Cost scales with the number of candidates scored. `TAA_MAX_LLM_CANDIDATES` (default 200) caps model calls per search; candidates beyond the cap keep their heuristic score and are labeled.

## Deployment

The app holds candidate PII. `taa serve` refuses to bind to a non-local address unless access control is configured:

- **Preferred:** an identity-aware proxy (Entra ID Application Proxy, oauth2-proxy) in front of the app, with `TAA_TRUSTED_USER_HEADER` naming the header that carries the signed-in user. Only expose the app through that proxy.
- **Pilot only:** `TAA_USERNAME` and `TAA_PASSWORD` for shared basic auth over HTTPS.

`docker-compose.yml` runs the app with Postgres, bound to localhost. Set `POSTGRES_PASSWORD` and `TAA_SECRET_KEY` in `.env`.

Retention: `taa purge --older-than-days 365` deletes unapproved candidates from searches older than the cutoff and clears stored source records. Schedule it.

## Calibration

Before live use, run the tool on three to five closed requisitions and compare against the recruiters' picks:

```bash
taa eval --slate slate.csv --recruiter-top recruiter_top10.csv
```

Targets from the plan: recruiter agreement of 70% or higher (recruiter's top 10 within the tool's top 25), precision of 50% or higher (approved share of the tool's top 25), and evidence verification of 98% or higher.

## Compliance controls built in

- Scoring input is masked (name, contact details, location, schools, graduation years), and the scoring prompt forbids using protected characteristics or proxies.
- Every score above unknown needs a verified quote.
- Ranking is reproducible: rubric version, prompt version, model and approver are stored with each search.
- Clearance is always labeled self-reported and unverified.
- Hard filters are limited to the do-not-target list and the location gate. Everything else is scored, not gated.
- No outreach is sent by the tool.

Employment counsel still needs to review the legal items in Section 9 of the plan (Illinois, California, NYC Local Law 144, Colorado, FCRA) before live use.

## Not built yet

- ATS read and write (the ATS is not yet identified). Use CSV export and import until then.
- ClearanceJobs API connector (confirm API or export terms first). Use CSV export.
- SSO inside the app. Use the identity proxy described above.
- Adverse impact reporting. Sourcing-stage data has no demographics; this belongs at the applicant stage in the ATS.

## Development

```bash
pytest
```

Tests cover masking, signal matching, evidence verification, entity resolution, filters, ranking, the connectors, the Claude client request shape and fallbacks (with the API mocked), and the full web workflow.

The web UI follows the Astrion Brand Kit 2026: Carbon background, Gunmetal panels, Bone text, one Ion action per section, square corners and hairlines, Archivo and JetBrains Mono, and table text set between 8 and 10 pt. Fonts are bundled under `src/taa/web/static/fonts` with their OFL licenses.
