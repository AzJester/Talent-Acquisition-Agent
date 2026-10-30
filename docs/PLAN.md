# Talent Acquisition Agent: Build Plan

Prepared for Astrion HR Talent Acquisition. Date: 2026-10-30.

## 1. Bottom line first

1. **The tool cannot "search LinkedIn and Indeed" by scraping them.** Both prohibit it in their terms, and LinkedIn enforces. Proxycurl, the largest LinkedIn data API, shut down in July 2025 about six months after LinkedIn sued its parent company. A tool built on scraped LinkedIn data puts Astrion's name on the next complaint. The workable design is: **license the data, build the ranking.** Section 4 lists the licensed routes.
2. **The value Astrion can own is the ranking layer, not the search layer.** LinkedIn Recruiter (with its Hiring Assistant add-on), SeekOut, hireEZ, Juicebox, and Gem already do AI search. None of them knows what "ran a DoD software factory through a cATO across two services" is worth versus "PM on a commercial autonomy SDK." A defense-specific scoring rubric with evidence citations, applied across several data sources plus Astrion's own data, is the differentiator.
3. **Candidate profiles almost never name the product.** Very few people write "Lattice" or "Hivemind" on their profile, and many cleared people keep thin profiles on purpose. The tool needs a signal dictionary (Section 6) that maps employers, teams, programs, and adjacent terms to the capability you want. Expect recall on product names alone to be poor.
4. **Check teaming agreements before targeting Anduril, Applied Intuition, or Shield AI staff.** If Astrion has teaming agreements, subcontracts, or NDAs with any of them, non-solicitation clauses may apply. Contracts should produce a do-not-target list that the tool enforces as a hard filter.
5. **Ranking candidates with AI is a regulated activity in several states.** Illinois (in force since 1/1/2026), California (CRD regulations since 10/1/2025), NYC Local Law 144, and Colorado (rewritten as SB 26-189, effective 1/1/2027, with enforcement tangled in federal litigation) all touch this. Federal Title VII disparate-impact liability applies regardless. This is manageable with the controls in Section 9, but legal review belongs in Phase 0, not at launch.

## 2. What the tool does

**Input:** a requisition. Title, location and on-site requirements, clearance requirement, must-haves (requirements), nice-to-haves (desirements), target companies, excluded companies, comp band.

**Output:** a ranked slate (default top 25, with a longer tail available) where each candidate has:

- A total score and a score per criterion.
- The evidence behind each criterion score, quoted from the source with a link (for example: "Director of Engineering, Platform One, 2021 to 2024, 140 engineers" from their profile).
- Criteria marked **unknown** where no evidence exists. Unknown is not zero, and it is shown separately so a recruiter can follow up.
- Flags: likely clearance (self-reported only, never verified), possible non-solicit conflict, comp-risk indicators (for example, pre-IPO equity holder), and data freshness.
- A draft outreach message the recruiter edits and sends. The tool never contacts candidates on its own.

**Humans stay in the loop.** The tool produces a sourcing slate. A recruiter decides who gets contacted, and hiring managers make every screening decision. This matters for both quality and legal exposure.

## 3. Pressure test on the SW Factory Lead example

The desirements as written will rank commercial defense-tech product leaders highest. That pool has three problems.

- **They may not have run a DoD software factory.** A software factory lead at a services contractor is judged on cATO and RMF, Iron Bank and hardened containers, DevSecOps pipelines on government enclaves, the Software Acquisition Pathway (DoDI 5000.87), and running multiple government customers with different authorizing officials. Anduril, Applied Intuition, and Shield AI build products. They mostly sell products. Those are different jobs.
- **Comp mismatch.** Senior leaders at those three companies typically hold meaningful private equity. Unless Astrion is prepared to compete on that, the response rate will be low. This is inference, not data; the pilot will measure it.
- **The pool is small.** Senior software leaders at those three companies number in the low hundreds at most (estimate). A ranked list of that pool is useful, but the tool should widen the net by default.

Recommended changes to the rubric for this role:

- Add as requirements: led a DoD or IC software factory or enterprise DevSecOps platform; delivered software through an ATO or cATO.
- Add adjacent pools as positive signals: Kessel Run, Platform One, BESPIN, Army Software Factory, Space CAMP, Kobayashi Maru, LevelUP, Navy PMW software teams; Palantir, Scale AI public sector, Rebellion Defense, Govini, Saronic, Epirus, Skydio; software leadership at primes (Lockheed, RTX, Northrop, L3Harris, GD, Leidos, SAIC, Booz Allen).
- Keep the three named companies as a strong desirement, not a gate.
- Treat "multiple programs, branches" as evidence of multi-customer delivery (for example, a platform serving Air Force and Army programs), which is checkable from profile text.

## 4. Data sources (licensed routes only)

| Source | What you get | Access route | Notes |
|---|---|---|---|
| LinkedIn Recruiter (Corporate) + Hiring Assistant | Best coverage of the target pool | Recruiter seats; Hiring Assistant is a paid add-on | No general API. Recruiter System Connect (RSC) only syncs with an approved ATS. The agent does not read LinkedIn directly; recruiters run searches and export or push profiles into the ATS, which the agent then reads. |
| ClearanceJobs | Self-reported clearance, cleared defense workforce | Recruiter license | Most relevant cleared-talent source for Astrion. Confirm API or export terms with ClearanceJobs before building a connector. |
| People data providers (People Data Labs, Coresignal, similar) | Bulk profile data, employment history, skills | Commercial API license | Read each vendor's data provenance and indemnity terms. Some resell scraped LinkedIn data; that legal risk passes to Astrion. FCRA analysis needed if any data could be used to determine eligibility (Section 9). |
| SeekOut | AI search with clearance and government-experience filters | Subscription, API on some tiers | Strong candidate to buy instead of build the search layer. |
| Indeed | Applicants to Astrion postings; resume search | Indeed employer products | Indeed resume search has no general third-party API that I know of. Treat Indeed as an inbound applicant channel feeding the ATS. Verify with Indeed sales. |
| Public signals | GitHub, conference talks (AFCEA TechNet, AUVSI XPONENTIAL, SOF Week), patents, company press releases, DoD software factory announcements | Public web, respecting robots.txt and site terms | Useful for enrichment and for finding people with thin profiles. Low volume, high signal. |
| Astrion internal data | ATS history (past applicants, silver medalists), employee referrals, key personnel resumes from past proposals, BD contacts | Internal | Often the best source and the one competitors cannot buy. Requires consent and retention review for old applicant data. |

The architecture treats every source as a connector behind one interface, so swapping vendors does not change the ranking layer.

## 5. Architecture

```
 Recruiter UI (web)
   |  requisition + rubric
   v
 Intake Agent ------------------> Rubric (versioned, approved by hiring manager)
   |
   v
 Query Planner Agent  (expands rubric with signal dictionary into per-source queries)
   |
   v
 Source Connectors  [ATS] [ClearanceJobs] [People-data API] [SeekOut] [Public web]
   |
   v
 Normalize + Entity Resolution  (one person, many records; dedupe on name+employer+dates)
   |
   v
 Enrichment  (public signals, tenure math, team-size extraction)
   |
   v
 Hard Filters  (do-not-target list, location/on-site, citizenship-required roles, exclusions)
   |
   v
 Scoring Agent  (per-criterion score + quoted evidence + confidence; identity fields masked)
   |
   v
 Ranker + Calibration  (weighted sum, tie-breaks, diversity-of-source check)
   |
   v
 Recruiter Review UI  -> approve / reject / notes  -> ATS push + outreach draft
   |
   v
 Audit Log + Feedback Store  (every score, every override, used for calibration and bias audits)
```

### Component choices

- **Orchestration:** Claude Agent SDK (Python) or a plain tool-use loop on the Claude API. Each agent above is a prompt plus a small set of tools. Avoid a heavy framework until it earns its place.
- **Models:** Use the strongest available model (currently Claude Opus 5.5) for rubric construction and final scoring, where reasoning quality drives ranking quality. Use Sonnet 5.5 for bulk extraction and normalization, and Haiku 5.5 for classification and dedupe checks. Re-evaluate on cost after the pilot.
- **Hosting:** Run in Astrion's existing cloud tenant. If Astrion is on AWS, call Claude through Amazon Bedrock so candidate PII stays inside the Astrion account and contract. Candidate data is PII, not CUI, so a commercial region is acceptable unless Astrion policy says otherwise. Confirm with IT security.
- **Data store:** Postgres with pgvector for profiles, embeddings, scores, and audit log. Object storage for raw source records with retention tags.
- **UI:** A simple internal web app (Next.js or similar) behind Astrion SSO. Recruiters need a slate view, an evidence drill-down, and approve/reject buttons. Nothing fancier for the pilot.
- **ATS integration:** Read and write through the ATS API (Astrion's ATS is unknown; Section 12).

## 6. Signal dictionary

Maintained as versioned YAML in the repo, owned by a recruiter plus a technical SME. Each entry maps evidence to a capability with a strength.

```yaml
capability: defense_autonomy_product_leadership
signals:
  - employer: Anduril Industries
    title_patterns: ["(Director|Head|VP|Lead).*(Software|Engineering|Product)"]
    keywords: ["Lattice", "Lattice SDK", "Lattice Mesh"]
    strength: strong
  - employer: Shield AI
    keywords: ["Hivemind", "Hivemind EdgeOS", "Hivemind Pilot"]
    strength: strong
  - employer: Applied Intuition
    keywords: ["autonomy", "defense", "EpiSci"]   # EpiSci acquisition: verify
    strength: strong
  - employer: [Palantir, Scale AI, Saronic, Epirus, Skydio, Rebellion Defense]
    strength: medium
capability: dod_software_factory
signals:
  - employer_or_unit: [Kessel Run, Platform One, BESPIN, Army Software Factory, Space CAMP, Kobayashi Maru, LevelUP]
    strength: strong
  - keywords: ["cATO", "continuous ATO", "Iron Bank", "Big Bang", "RMF", "DevSecOps", "Software Acquisition Pathway"]
    strength: medium
```

The Query Planner uses this to generate source queries. The Scoring Agent uses it to recognize evidence. Recruiters add entries when they see the tool miss someone, which is the main way the system improves.

## 7. Scoring design

Two stages.

**Stage 1, hard gates.** Binary, applied before any AI scoring: do-not-target list, location and on-site constraints, and role-specific legal requirements (for example, U.S. citizenship where a clearance is a condition of the role). Everything else is scored, not gated, so a strong candidate with an unusual history is not silently dropped.

**Stage 2, weighted criteria.** Example rubric for SW Factory Lead (weights are a starting point for the hiring manager to adjust):

| Criterion | Type | Weight | Evidence the scorer looks for |
|---|---|---|---|
| Led large software org | Requirement | 20 | Org size stated or inferable (direct reports, "led 100+ engineers"), titles of Director and above |
| DoD software factory or enterprise DevSecOps platform leadership | Requirement | 20 | Signal dictionary `dod_software_factory` |
| Delivered through ATO/cATO | Requirement | 10 | Explicit mention; RMF roles |
| Product management across multiple programs or customers | Requirement | 15 | Multiple programs, services, or customers named |
| Defense autonomy product leadership (Anduril, Applied Intuition, Shield AI, or peers) | Desirement | 15 | Signal dictionary `defense_autonomy_product_leadership` |
| Commercial software product leadership | Desirement | 10 | Commercial SaaS or product company leadership |
| Active clearance (self-reported) | Desirement | 10 | ClearanceJobs or profile statement; always labeled unverified |

Scoring rules the Scoring Agent follows:

- Score each criterion 0 to 4 with a required quoted evidence span and source link. No quote, no score above 0; mark it **unknown** instead.
- Names, photos, schools, graduation years, and addresses are masked from the scoring prompt. Graduation year is a proxy for age, and school names can proxy for other protected characteristics.
- The model never infers protected characteristics, and the prompt and output schema contain no field for them.
- The final rank is computed in code from criterion scores and weights, not by asking the model to rank. That keeps rankings reproducible and auditable.
- Every run stores the rubric version, model version, prompt version, inputs, and outputs.

**Calibration.** Before the pilot, take three to five closed Astrion reqs where you know who was hired and who reached the final round. Run the tool on the candidate pool from those reqs and measure whether recruiters' top candidates appear in the tool's top 20. Tune weights and the signal dictionary until agreement is acceptable. Section 10 sets the targets.

## 8. Recruiter workflow

1. Recruiter and hiring manager fill the intake form. The Intake Agent turns it into a draft rubric; the hiring manager approves it. The approved rubric is locked to the search.
2. Recruiter runs LinkedIn Recruiter and ClearanceJobs searches using queries the Query Planner suggests, and pushes results into the ATS (or exports them to the tool) under the vendors' terms.
3. The tool pulls from the ATS, people-data API, and internal sources, then dedupes, enriches, filters, and scores.
4. Recruiter reviews the slate, reads evidence, and approves or rejects each candidate with a reason code. Overrides feed calibration.
5. Approved candidates go to the ATS with the evidence summary attached. The recruiter edits and sends the outreach draft.
6. Weekly: response and conversion data flows back to tune weights.

## 9. Compliance and governance

Get employment counsel to review this section in Phase 0. The summary below is my reading of current law, not legal advice, and state AI law has changed repeatedly in the last 18 months.

- **Title VII / ADEA / ADA disparate impact.** Applies to any selection procedure, AI or not. The EEOC withdrew its AI technical assistance documents in January 2025; the underlying statutes and the Uniform Guidelines did not change. Monitor selection rates by stage (the four-fifths rule is the usual first screen) once demographic data is available from voluntary self-ID at the applicant stage.
- **Illinois (HB 3773, effective 1/1/2026).** Prohibits AI use in employment decisions that has a discriminatory effect and requires notice to candidates when AI is used. Applies if Astrion recruits for Illinois-based roles or Illinois residents. Confirm scope with counsel.
- **California (Civil Rights Council ADS regulations, effective 10/1/2025).** Covers automated decision systems in hiring, including recordkeeping (four years) and anti-bias testing as a defense factor.
- **NYC Local Law 144.** Requires an independent bias audit and candidate notice for automated employment decision tools used for NYC roles.
- **Colorado.** The original AI Act (SB 24-205) was repealed and replaced by SB 26-189, signed May 14, 2026, effective January 1, 2027, and narrower than the original. A federal court order in the xAI litigation restrains enforcement. Watch this one; it is not settled.
- **FCRA.** If a people-data vendor's data is used to make or support eligibility decisions about a candidate, that vendor may be a consumer reporting agency, which triggers disclosure and adverse-action duties. Keep the tool in a sourcing role and get the vendor's FCRA position in writing.
- **Clearances.** The tool never states that a clearance is verified. Verification happens through DISS after a conditional offer under Astrion's FSO process.
- **Non-solicitation.** Contracts maintains the do-not-target list from teaming agreements, subcontracts, and NDAs. It is a hard filter, and each change is logged.
- **Federal contractor obligations.** EO 11246 affirmative action obligations were rescinded in January 2025. Section 503 and VEVRAA obligations remain. Keep sourcing records sufficient to show outreach practices.
- **Privacy.** California residents have CCPA rights over applicant and candidate data. Set retention (for example, delete unengaged sourced profiles after 12 months), honor deletion requests, and log data provenance per record.
- **Governance.** A short AI use policy for TA: who can run searches, who approves rubrics, quarterly bias review, annual third-party audit if NYC applies.

## 10. Success measures

Measured in the pilot against the same reqs worked the current way.

| Metric | Target for go decision |
|---|---|
| Recruiter agreement: share of recruiter's top 10 that appear in tool's top 25 | 70% or higher |
| Precision: share of tool's top 25 that recruiter approves for outreach | 50% or higher |
| Time from req open to first 15-candidate slate | 50% reduction |
| Outreach response rate | No worse than baseline |
| Interview conversion from sourced candidates | No worse than baseline |
| Evidence accuracy: spot-check of quoted evidence against source | 98% or higher (a fabricated quote is a stop-work issue) |
| Adverse impact by stage | No four-fifths flags without explanation |

Targets are my proposal. Adjust them with the TA lead before the pilot starts so the go decision is not argued after the fact.

## 11. Phased plan

| Phase | Duration | Work | Exit criteria |
|---|---|---|---|
| 0. Decisions and contracts | 2 to 3 weeks | Pick data vendors and sign terms; confirm ATS and API access; counsel review of Section 9; do-not-target list from Contracts; IT security approval for hosting; pick 3 calibration reqs and 3 live pilot reqs | Signed data agreements, legal sign-off on pilot scope, hosting approved |
| 1. MVP build | 5 to 6 weeks | Connectors (ATS, one people-data API, ClearanceJobs if access allows), normalization and dedupe, signal dictionary v1, Scoring Agent with evidence, ranker, minimal recruiter UI, audit log | End-to-end run on calibration reqs; evidence accuracy 98%+ |
| 2. Calibration | 2 weeks | Run on closed reqs, tune weights and dictionary, fix misses | Recruiter agreement target met on closed reqs |
| 3. Live pilot | 6 to 8 weeks | 3 live reqs (SW Factory Lead plus two others of different types), weekly tuning, measure Section 10 metrics | Go / adjust / stop decision against Section 10 |
| 4. Production | 4 to 6 weeks | ATS write-back, SSO, retention jobs, monitoring, bias reporting, runbooks, training for TA team | TA team using it without engineering support |

Roughly 4 to 5 months from kickoff to production if Phase 0 does not stall. Vendor contracting is the usual cause of slippage.

### Team

- Tech lead / backend engineer (1.0 FTE through Phase 4)
- Full-stack engineer (1.0 FTE Phases 1 to 4)
- Data engineer (0.5 FTE Phases 1 to 2)
- Senior recruiter as product owner (0.25 FTE throughout; this role decides whether the tool is any good)
- Technical SME for the signal dictionary, ideally a current software factory or program leader (a few hours per week)
- Employment counsel and HR compliance (Phase 0, then quarterly)

### Cost (rough order of magnitude, verify with vendors)

- People-data API license: low to mid five figures per year depending on volume.
- LinkedIn Recruiter Corporate seats and Hiring Assistant: priced by LinkedIn sales; many TA teams already hold seats.
- ClearanceJobs and SeekOut: subscription pricing, quote required.
- Model usage: small relative to data costs. A search that scores 500 profiles at a few thousand tokens each lands in the tens of dollars per search with the model mix above. Verify against current pricing before budgeting.
- Labor: the dominant cost. About 2.5 FTE for 4 to 5 months.

## 12. Buy versus build

Run this comparison in Phase 0, not after the build.

- **Buy only** (LinkedIn Hiring Assistant or SeekOut, plus a written rubric used by recruiters by hand): lowest cost and fastest. Gives you good search but generic ranking, no cross-source synthesis, and nothing Astrion owns.
- **Buy search, build ranking (recommended):** license the search and data, build the defense-specific signal dictionary, scoring, evidence trail, and internal data integration. This is where Astrion's domain knowledge has value.
- **Build everything:** not recommended. The data layer is where the legal risk and vendor dependence are, and building it yourself does not make it better.

The build case gets much stronger if the tool also serves capture: finding and ranking **key personnel for proposals** and contingent hires against an RFP's labor category requirements. That is the same rubric-and-evidence engine with a different intake form, and it lets BD and TA share the cost. If Astrion bids work with named key personnel, plan for this in the architecture from day one.

## 13. Open questions for Astrion

1. Which ATS does Astrion use, and does the contract include API access?
2. How many LinkedIn Recruiter seats exist today, and at what tier?
3. Does Astrion have teaming agreements, subcontracts, or NDAs with Anduril, Applied Intuition, or Shield AI?
4. How many hard-to-fill technical reqs per year? Under about 15 to 20, buy-only may be the better economics.
5. In which states are roles located or candidates recruited? This sets which AI hiring laws apply.
6. Cloud tenant and any IT policy on sending PII to an AI provider?
7. Is there appetite to extend this to proposal key personnel searches?

## 14. Proposed repository layout (for Phase 1)

```
talent-acquisition-agent/
  docs/                    plan, rubric guides, compliance notes, runbooks
  config/
    signals/               signal dictionary YAML (versioned)
    rubrics/               approved rubric templates by role family
    do_not_target.yaml     maintained by Contracts (not committed if sensitive)
  src/
    agents/                intake, query_planner, scoring
    connectors/            ats/, people_data/, clearancejobs/, public_web/
    pipeline/              normalize, resolve, enrich, filter, rank
    api/                   backend for recruiter UI
    audit/                 run logging, bias reports
  web/                     recruiter UI
  evals/                   calibration sets, evidence-accuracy checks, regression tests
  tests/
```

## Sources

- Proxycurl shutdown: [Nubela, "Goodbye Proxycurl"](https://nubela.co/blog/goodbye-proxycurl/amp); [Bloomberg Law on LinkedIn scraper litigation](https://news.bloomberglaw.com/ip-law/linkedin-battles-online-scrapers-in-perpetual-struggle-over-data)
- LinkedIn RSC access requirements: [Microsoft Learn, Recruiter System Connect](https://learn.microsoft.com/en-au/linkedin/talent/recruiter-system-connect)
- LinkedIn Hiring Assistant: [LinkedIn Business, Hiring Assistant](https://business.linkedin.com/hire/hiring-assistant)
- Colorado AI Act delay and replacement: [Greenberg Traurig on SB 25B-004 delay](https://www.gtlaw.com/en/insights/2025/9/colorado-delays-comprehensive-ai-law-with-further-changes-anticipated); [Proskauer on developments ahead of implementation](https://www.proskauer.com/blog/major-developments-put-colorados-ai-law-on-ice-ahead-of-implementation); [Davis+Gilbert on repeal and replacement](https://www.dglaw.com/what-to-know-about-colorados-repeal-and-replacement-of-its-landmark-ai-act/)
- Illinois HB 3773, California CRD ADS regulations, NYC LL144, EEOC guidance withdrawal, EO 11246 rescission, and FCRA points are from prior knowledge and should be confirmed by counsel.
