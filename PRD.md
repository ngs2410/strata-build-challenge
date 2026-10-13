# Strata PRD

**One line:** A regulatory change-to-action workspace for in-house regulatory affairs teams at regulated enterprises. Ingests versioned regulatory documents alongside the company's own obligations, projects, and documents; detects material changes; cites the exact source passage behind every claim; maps each change to the specific internal state it affects; recommends an action; and routes it to the right internal reviewer, with a full audit trail. First user: Rachel Chen at Verdant Grid.

---

## 0. Industry context (for readers new to this domain)

A few terms used throughout. All are standard in the US energy-regulation industry.

- **Electric utility** — a company that generates, transmits, and delivers electricity. An **investor-owned utility** is a for-profit, shareholder-owned one (as opposed to a municipal utility or customer-owned cooperative).
- **FERC** — the Federal Energy Regulatory Commission, the US agency that regulates interstate transmission and wholesale sale of electricity.
- **Wholesale electricity market** — the bulk market where utilities and generators trade electricity before it reaches end customers. Operated by regional non-profits (ISOs / RTOs).
- **MISO / PJM** — two such regional operators. **MISO** (Midcontinent Independent System Operator) runs the Midwest wholesale market across ~15 US states; **PJM** runs the Mid-Atlantic. A utility that operates "in a MISO footprint" sits inside MISO's territory.
- **Rulemaking** — FERC's process for creating or amending rules. FERC publishes a draft (**Notice of Proposed Rulemaking**, or NOPR), takes public comment, then issues a **Final Rule** (sometimes called an "Order"). This NOPR → Final Rule cycle is what Strata diffs.
- **Docket** — FERC's identifier for a proceeding (e.g., Docket **RM18-9** is where Order 2222 was issued).
- **FERC eLibrary** — FERC's public online repository of filings and orders, searchable by docket.
- **Compliance filing / rate case** — the two main things utilities file with FERC. A compliance filing shows a utility's practices comply with a rule; a rate case asks the regulator to approve new electricity rates. Compliance filings typically land 6–18 months after a FERC rule changes.
- **Tariff** — a utility's or market operator's official schedule of rates, terms, and conditions, approved by its regulator.
- **FERC Order 2222** — a 2020 FERC rule requiring wholesale markets to let aggregators of **Distributed Energy Resources (DERs)** — rooftop solar, home batteries, EV chargers, smart thermostats — participate as a single pooled resource. The idea: pool a thousand home batteries so the market sees them as one small power plant.
- **Westlaw / Bloomberg Law / FERC listserv** — the newsletters and mailing lists Rachel scans each morning for new agency orders and rules.

---

## 1. The user

**Rachel Chen, Senior Manager of Regulatory Affairs at Verdant Grid, Inc.**

- Verdant Grid is a mid-size for-profit electric utility serving about 1.2 million customers in the US Midwest. Its service territory sits inside MISO's wholesale electricity market. Verdant has an early-stage program for pooling customers' rooftop solar panels, home batteries, and smart thermostats so MISO can treat them as a single grid resource (a "DER aggregation" program).
- Rachel has three analysts on her team. Her backlog is always larger than her team.
- Her job, in her own framing: *"When FERC moves, what has to change for us?"* She reads new FERC rules, decides what Verdant needs to do differently, breaks the work into tasks for the right internal team, and defends her reading when Verdant files with FERC or testifies in rate cases.

**The workflow she runs today:**
1. She scans alerts each morning (Westlaw, Bloomberg Law, FERC mailing lists), opens anything promising on FERC's eLibrary, and reads enough to decide if it affects Verdant.
2. If it does, she drafts an internal memo covering (a) what changed, (b) what Verdant does today, (c) what needs to change, (d) who owns the work.
3. She routes the memo via Slack and a Jira ticket to one of three internal teams — **Market Operations** (Verdant's MISO wholesale-market participation), **Legal** (FERC and state filings), or **IT & Metering** (smart-meter and data systems) — and tracks open work in a shared spreadsheet.

**Her hardest week in the last five years** was September 2020, when FERC issued Order 2222 — the DER-aggregation rule. The final rule was 660 pages, affected eight of Verdant's internal programs, and took Rachel's team six weeks to produce the first mapping memo. By the time MISO filed its compliance plan with FERC in April 2022 (showing how it would implement Order 2222 inside its market), the mapping was stale and Rachel had to redo most of it against a different source document.

---

## 2. Why alerts and search are insufficient

Rachel is not short on information. She is short on **trustworthy interpretation tied to Verdant's state.**

- **Alerts** tell her a document exists. They do not tell her what changed between versions, which Verdant obligations are affected, or what action to take.
- **Search** (FERC eLibrary, Westlaw, Google) returns documents, not interpretations. Every result still requires her to read the full order and do the mapping in her head.
- **Generic AI summarizers** (ChatGPT, Claude, etc.) produce prose that reads plausible but is not citation-grounded to source passages. Rachel cannot defend a summary to her General Counsel (Verdant's top in-house lawyer) if she can't show the exact source text behind each claim.
- **Internal spreadsheets** are Verdant's current system of record for its obligations and projects. They are not queryable, not versioned, and not connected to the external regulatory stream.

The missing capability is: *given this version of this regulatory proceeding, exactly which of Verdant's obligations, projects, and documents are affected, with the source passage behind every claim and a proposed action routed to the right reviewer.*

That capability is not a search tool and not an alert feed. It is a workspace that connects two worlds — external authority and internal state — with evidence under every connection.

---

## 3. The first change-to-action wedge workflow

**Scope for v1 (the build challenge submission):** FERC Order 2222, a single short subsection on how aggregators must register with wholesale-market operators. Two versions: the draft text (the NOPR, issued November 2019 in Docket RM18-9) and the final text (Order 2222, issued September 2020). We extract roughly 8 specific obligations from this one subsection — not the whole 660-page rule.

### The loop Rachel runs in Strata

1. **Ingest.** Strata loads the draft version (NOPR) and the final version (Order 2222) of the subsection, plus Verdant Grid's company state (about 8 obligations, 3 projects, and 5 documents, seeded as structured JSON for the build challenge). Rachel does nothing — this is a one-command operator step.
2. **Diff.** Strata detects material changes between the two versions: obligations that were added, removed, tightened, loosened, or merely reworded (cosmetic edits are flagged but not actioned). Every change links to the exact source passage in both versions.
3. **Cite.** Every extracted obligation and every detected change carries a verifiable citation to the source passage. A follow-up verification step confirms the cited passage actually contains the stated claim.
4. **Map.** For each material change, Strata identifies the specific Verdant obligations, projects, and documents it affects, with the mapping rationale cited to both the regulatory passage and the Verdant-side field it matched.
5. **Recommend & route.** For each affected item, Strata recommends an action (`review`, `amend`, or `no-op`) and routes it to a *primary* internal destination (one of Market Operations, Legal, or IT & Metering) with a rationale. v1 ships single-destination routing per change; multi-reviewer routing (primary + CC, or parallel destinations) is a known limitation — see §7.
6. **Escalate.** When the system's confidence in an interpretation is low, the item is routed to Rachel for triage instead of silently committed. The confidence threshold is a tunable setting per tenant.
7. **Review & audit.** Rachel opens the Strata workspace, sees a prioritized review queue, and approves, overrides, or escalates each item. Every approval, override, and status change is written to an append-only audit log. Rachel can roll back the project state to any prior point in time.

### What makes this the wedge, not a bigger first bite

- One rulemaking, one subsection, two versions — enough to demonstrate the full ingest → diff → cite → map → route → audit loop end-to-end.
- Produces a work product Rachel can defend to Verdant's General Counsel: every claim carries a source citation.
- The confidence-and-escalation path is the trust mechanism — Rachel is never asked to trust an unverified AI output. The system fails to a human reviewer, not to a guess.
- Everything else is explicitly out of scope for v1 (see §6).

### Interface surfaces (v1)

Strata ships with two surfaces, one per role:

- **CLI — the operator surface.** One command per pipeline stage (`main_ingest.py`, `main_detect_changes.py`, `main_eval.py`) plus one end-to-end command that chains them. Reviewers evaluating the submission run the CLI. Rachel does not — someone on Verdant's side (today Rachel's analyst, later a scheduled job) runs the pipeline to populate her review queue.
- **Minimal web view — Rachel's surface.** A single page showing her prioritized review queue, with three affordances per item: the detected change with its source citation inline, the affected Verdant obligation / project / document highlighted, and three buttons (**approve** / **override** / **escalate**). A second page shows the per-project audit log so Rachel can confirm or roll back prior decisions. Deliberately minimal — enough that Rachel could use it without a walkthrough, nothing more.

---

## 4. Trust and adoption metrics (for expert users)

Rachel is an expert, not a novice user being onboarded. She will reject a tool she cannot defend. The metrics reflect that.

### Trust metrics (what Verdant's General Counsel asks about)

- **Citation precision ≥ 98%.** Every extracted obligation and detected change cites a source passage; when audited, the cited passage must actually contain the stated claim. Measured on a held-out test set.
- **Mapping precision ≥ 90%** on suggested obligation / project / document matches, under expert review. False positives waste Rachel's time; precision matters more than recall at this stage.
- **Confidence calibration.** Claims the system labels "high confidence" must be correct ≥ 95% of the time. Everything below the confidence threshold must escalate to a human. Over-confidence is a worse failure than under-confidence.
- **Audit completeness.** Every state transition in the workspace is traceable to an actor, a timestamp, and a prior value. Zero gaps.

### Adoption metrics (whether Rachel actually uses it)

- **Time to first mapping memo** on a new proceeding: less than 2 days, versus 2–6 weeks today.
- **Share of changes auto-mapped without override:** target ≥ 60% in v1. Below that, the time Rachel spends overriding wrong answers cancels the time saved.
- **Rachel's hands-on time per proceeding:** target ≤ 20% of her current time; the rest is reviewing output and approving.
- **Escalation acceptance rate:** when Strata escalates an item to Rachel, Rachel finds the escalation justified ≥ 80% of the time. If this drops, the escalation threshold is miscalibrated — the system is being either too timid or too aggressive.

The two sets fail differently. Trust metrics are a floor — below them, the tool is unusable regardless of speed. Adoption metrics are a ceiling — above them, we are faster than Rachel's current process.

---

## 5. Expansion beyond utilities

The change-to-action pattern — *a versioned stream of external authority + a structured record of internal state + mapping + action + reviewer routing + audit trail* — is not utility-specific. The same shape fits other heavily-regulated industries:

- **SEC → brokerage and investment firms.** Internal state: compliance policies, trade-surveillance programs, training materials. User: Chief Compliance Officer.
- **HHS Office for Civil Rights → hospitals and health insurers.** Internal state: vendor contracts, patient-privacy notices, workforce training. User: Privacy Officer.
- **FDA → device makers and pharmaceutical companies.** Internal state: product design documentation, regulatory submissions, quality-system procedures. User: Director of Regulatory Affairs.
- **OCC / Federal Reserve / FDIC → regional banks.** Internal state: anti-money-laundering programs, lending policies, risk-model documentation. User: Compliance Risk Officer.

Three structural ingredients are consistent across all four: (1) the regulator publishes versioned, authoritative documents with clear draft-to-final cycles; (2) the regulated company maintains structured internal state it has to be able to defend; (3) the gap today is interpretation and mapping, not detection. The depth-area work we invest in citation grounding and audit architecture ports directly to each of these domains.

**What is utility-specific in v1 and must be factored out before expansion:**
- The obligation-type taxonomy (reporting / operational / procedural / financial). Each domain will have its own leaves; the shape is likely consistent across domains.
- The three internal reviewer destinations (Market Operations, Legal, IT & Metering). Pluggable per tenant via a facade in `src/workflow/reviewer_routing.py`.
- The FERC-specific document parser and docket numbering. Multi-jurisdiction support (state utility commissions, other federal regulators) is called out as a depth-area opportunity in CHALLENGE.md.

**The much larger adjacent opportunity — internal strategic change management.** The four domains above all share "external regulator → regulated enterprise" as the authority axis, but the same change-to-action pattern applies — with far broader reach — to *internal* change inside every enterprise where decisions move faster than they're communicated: strategic pivots, OKR revisions, product decisions, re-orgs, policy updates, roadmap shifts. The "regulator" becomes an executive, a product lead, or a strategy function; the "regulated entity" becomes a team or an IC whose ongoing work the decision may invalidate, redirect, or accelerate. The pain is near-universal and growing — as enterprises are asked to move faster, teams and ICs discover affecting decisions only when a deliverable misses its mark or someone surfaces the gap in a review, handled today by Slack threads, scattered memos, stale wiki pages, and "did you see the email" rituals that break under volume. The core problem is identical to the regulatory case: *given this version of this internal authority, which of our ongoing projects, commitments, and documents are affected, with the source passage behind every claim and a proposed action routed to the right internal owner.* Strata's architecture ports with minimal adaptation — versioned-document ingest handles exec memos, strategy docs, OKR files, and policy pages; draft vs. final becomes proposed vs. committed decision; obligations become internal commitments; the depth-area investments (citation grounding, audit/rollback) carry over verbatim. The regulated-utility wedge is a credible on-ramp; the TAM beyond it is every enterprise with more than one team.

---

## 6. Non-goals for v1

Explicitly out of scope for the build-challenge submission. Each is a defensible deferral, not an oversight.

- **Full-document ingest.** The one subsection only. Scaling to full 660-page rules is a depth-area problem for document-diffing-at-scale, not a wedge requirement.
- **Multi-tenant demo at scale.** One synthetic tenant (Verdant Grid). The `(tenant_id, environment)` plumbing is in place from day one, so adding a second tenant is a config change, not a refactor. The production shape also assumes shared-database logical isolation (row-level security + per-environment schemas); some regulated enterprises will require stronger **per-tenant physical isolation** — a distinct database or cluster rather than RLS — and the `Store[T]` facade reserves that seam, but delivering it per tenant is a post-wedge production option, not a v1 deliverable.
- **Multi-jurisdiction.** FERC only. State utility commissions and other federal regulators come later.
- **Push-based or scheduled ingestion.** The user triggers an ingest from the command line. Scheduled or event-driven ingestion is post-wedge.
- **Integrations with Jira and Slack.** Strata produces the mapping memo and routes items to in-system reviewer queues. External integrations are post-wedge.
- **Tenant-chosen models, providers, and API keys.** v1 uses a single commercial frontier model (OpenAI) with structured outputs, under our account and our API key, with no fine-tuning. A credible production path lets a tenant **supply their own API key**, **select an alternative provider** (Anthropic, Google, a self-hosted vLLM, or an on-prem endpoint), or **point at a tenant-owned fine-tuned model** — scenarios driven by existing procurement contracts, data-sharing constraints that limit which provider sees regulated content, or in-house model investments. The `ModelCaller` dispatch seam (TDD §4.1) reserves the hook; wiring per-tenant provider/key/model selection is a post-wedge production option.
- **Rich workspace UI.** The v1 web view is deliberately minimal: a review-queue page and an audit-log page (see §3). Dashboards, team-wide analytics, bulk operations, inline mapping edits, saved filters, and SSO authentication are all post-wedge.
- **Tenant-chosen deployment target.** v1 ships as a developer-run Python app on one host. A containerized (OCI-image) deployment that an enterprise tenant can run on **their chosen cloud provider** (AWS / GCP / Azure) or **on-premise / air-gapped** is a standard expectation for regulated buyers — driven by data-residency rules, procurement-approved provider lists, or security teams that insist the workload live inside their own perimeter. Supporting this is a post-wedge production option, not a v1 requirement.

---

## 7. Open questions and unvalidated assumptions

Per PREP_MATERIAL.md, calling these out explicitly is more valuable than overclaiming.

- **Rachel is a plausible proxy for the real user we have not yet interviewed.** The persona and workflow are drawn from public regulatory-affairs trade literature and FERC Order 2222 implementation case studies, not direct user research. First-week discovery plan: interview three in-house regulatory affairs managers at MISO or PJM utilities before any further scope commits.
- **The ≥ 90% mapping-precision target is plausible but unvalidated.** We need to run the full workflow against the Order 2222 excerpt and measure where we actually land before committing to this as a shippable threshold.
- **"Material change" is operator-defined, not objectively defined.** v1 ships with a five-category taxonomy (added / removed / tightened / loosened / cosmetic). Expect iteration with Rachel's input.
- **Confidence-threshold calibration is a per-tenant setting.** v1 ships with a single default; whether that default is the right starting point for a different regulator or company shape is unknown.
- **Synthetic company context fidelity.** The ~8 obligations, 3 projects, and 5 documents seeded for Verdant Grid are modeled on public MISO tariff provisions and industry-standard DER aggregation programs. They are plausible, not authentic. A real engagement would ingest the enterprise's own data.
- **Routing is single-destination in v1; real changes may need multi-destination.** Each change routes to one primary internal team (Market Operations, Legal, or IT & Metering). Real regulatory changes often touch multiple teams in parallel — e.g. the Order 2222 telemetry requirement belongs primarily to IT & Metering but also to Market Operations (which configures DER aggregation offers around telemetry data). TDD §8.6 captures the v2 direction (`routing_destinations: tuple[Literal[...], ...]` with a primary + CC model, or parallel destinations) and the workflow-engine questions that come with it (one-approval-unblocks-all vs. all-must-approve, cascading escalation). Expect this to force a v1.5 if Rachel's feedback shows systematic manual re-routing to a second team.
