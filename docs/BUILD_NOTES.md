# Build Notes

How the Talent Acquisition Agent is deployed, how to change it, and the self-hosted path for later.

## Current deployment: standalone app on claude.ai

**Team link:** https://claude.ai/artifact/CeR86Htyv2kzoCjuo5FpYE

The app is a single page hosted by claude.ai. It needs no AWS account, no server and no API key:

- **Sign-in:** each person signs in with their own Claude account.
- **Scoring:** Claude scoring runs on the signed-in person's own Claude usage, through the page's `sample` capability.
- **Storage:** searches, candidates, rubrics, reviews and the audit log live in the app's database, hosted with the page.
- **CSV export:** handled by the page's `downloads` capability.

### Access

The database rules are set at publish time and enforced by claude.ai, not by page code.

| Who | Level | What they get |
|---|---|---|
| Owner and Editors | `admin` | Everything, including the do-not-target list and deleting searches |
| Contributors, and Astrion members of the owner's Claude organization arriving by link | `interact` | Run searches, score, review, export, edit rubrics |
| Viewers, Commenters, and anyone outside the organization (public link or email invite) | `view` | The page loads but shows no candidate data |
| Signed-out visitors | none | No data |

Rules as published: `{path: "", read: "interact", write: "interact"}` and `{path: "config", read: "interact", write: "admin"}`. These were checked after the first publish: a `view`-level read returns nothing, and an `interact`-level write to `config` is refused.

To give the team access, open the app, use **Share**, and either share it with the Astrion organization or add each person as a Contributor. A public link is safe to hand out only because of the rules above. Outside visitors see an empty app.

### What the standalone app does

It is the same pipeline as the Python app (`src/taa/`), ported to JavaScript in `standalone/app.html`:

1. **Rubrics:** add the template, paste YAML, or draft from a hiring request with Claude. Approving a version locks it; edits create a new version.
2. **Sourcing queries:** Boolean strings and company/title filters for LinkedIn Recruiter and ClearanceJobs.
3. **Candidate intake:** CSV or JSON upload. The files are parsed in the browser and duplicates across files are merged.
4. **Filters:** the do-not-target list and the rubric's location gate exclude candidates before scoring.
5. **Heuristic score:** every candidate gets a score from the signal dictionary right away.
6. **Claude scoring:** on a recruiter's click, the top N candidates go to Claude in batches of five. Claude sees masked profiles only (no name, contact details, location, schools or graduation years).
7. **Evidence check:** every quote is checked against the masked profile. A criterion left with no verified quote becomes unknown.
8. **Ranking:** a weighted total computed in code, never by the model.
9. **Review:** approve or reject with reason codes, draft outreach, and export a CSV.
10. **Audit:** rubric changes, approvals, searches, scoring runs, reviews, outreach drafts, exports and deletions are logged.

The heuristic scorer produces the same scores as the Python app. On the synthetic sample data both rank the top three at 90.0, 65.0 and 58.8.

### Changing it

The signal dictionary and rubric templates come from `config/`, so both apps score from one source.

```bash
python standalone/build.py
```

The build writes `standalone/dist/talent-agent.html`. Republish that file to the same artifact from a Claude Code session so the team link stays the same. Page code changes go in `standalone/app.html`. The signal dictionary goes in `config/signals/`, rubric templates in `config/rubrics/`.

### Limits to know

- **Storage cap:** 25,000 records in total. Each candidate is one record, so delete old searches under Governance. A 200-candidate search uses about 200.
- **Claude usage:** scoring spends each recruiter's own Claude usage. Large searches can hit a usage or rate limit. The app saves what it scored and the recruiter runs it again later to finish.
- **No People Data Labs:** the page cannot call outside services. Run People Data Labs searches elsewhere and upload the JSON (the importer reads PDL person records).
- **Audit log:** Contributors can write audit entries, so it is a record, not tamper-proof evidence. For a tamper-evident log, use the self-hosted app.
- **Data location:** candidate data is stored in claude.ai artifact storage under Astrion's Claude account. Confirm that fits Astrion's data handling policy for candidate personal data.

## Self-hosted option (Python app)

The FastAPI app in `src/taa/` is the same tool, built for Astrion's own infrastructure. Use it when Astrion wants:

- the data inside its own cloud;
- single sign-on through its identity provider;
- People Data Labs searches from the app;
- a Postgres database it controls.

### AWS deployment (when access is available)

Nothing below is set up today. This is the path once Astrion has an AWS account for the tool.

1. **Model access through Amazon Bedrock**, which keeps candidate data inside Astrion's AWS account:
   - `TAA_LLM_PROVIDER=bedrock`
   - `TAA_BEDROCK_REGION=us-east-1` (or the region Astrion uses)
   - The app calls Bedrock model ids with an `anthropic.` prefix (`anthropic.claude-opus-5-5`). Server-side refusal fallback is not available on Bedrock, so a declined call falls back to the heuristic score for that candidate.
2. **Credentials for deploying from a Claude Code session.** Store these in the cloud environment settings (session title bar, environment menu, Edit, then Network secrets or environment variables):
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_REGION`
   - Use credentials scoped to deploying this app only. Never paste them into a chat.
3. **Hosting:** build the `Dockerfile` image and run it on ECS Fargate or App Runner, with RDS Postgres (`TAA_DATABASE_URL=postgresql+psycopg://...`). `docker-compose.yml` shows the app and database pairing for a single host.
4. **Access control:** put the app behind an identity-aware proxy (an ALB with OIDC to Astrion's identity provider, or oauth2-proxy). Set `TAA_TRUSTED_USER_HEADER` to the header carrying the signed-in user. `taa serve` refuses a non-local bind without this or `TAA_USERNAME`/`TAA_PASSWORD`.
5. **Secrets:**
   - `TAA_SECRET_KEY` (a long random value)
   - Database credentials
   - `PDL_API_KEY` if People Data Labs is licensed
   - Keep all three in AWS Secrets Manager.
6. **Retention:** schedule `taa purge --older-than-days 365`.

All settings are listed in `.env.example`.

### Other hosts

Azure (Container Apps plus Azure Database for PostgreSQL) or GCP (Cloud Run plus Cloud SQL) work the same way. Use `TAA_LLM_PROVIDER=anthropic` with an Anthropic API key, or that cloud's Claude offering.

## Before relying on the rankings

There is no pilot phase. Three things still matter before the rankings drive real outreach:

1. **Calibration.** Run three to five closed requisitions and compare the app's top 25 against the recruiters' actual picks, using `taa eval` on the exported CSV. The targets:
   - recruiter agreement 70% or higher;
   - precision 50% or higher;
   - evidence verified 98% or higher.
   Until then the ranking is untested against Astrion's own hiring judgment.
2. **Do-not-target list.** Contracts fills it in under Governance from the non-solicitation terms in teaming agreements, subcontracts and NDAs. It ships empty.
3. **Employment counsel review.** State rules on AI in hiring (Illinois, California, New York City, and Colorado from January 2027) apply whether or not the tool is called a pilot. See Section 9 of `docs/PLAN.md`.
