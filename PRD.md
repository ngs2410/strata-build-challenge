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

## 1.5 How this PRD was built without interviewing Rachel

The persona and workflow in [§1](#1-the-user), the pain articulation in [§2](#2-why-alerts-and-search-are-insufficient), the Order 2222 wedge choice in [§3](#3-the-first-change-to-action-wedge-workflow), and the trust metrics in [§4](#4-trust-and-adoption-metrics-for-expert-users) are all drawn from secondary sources: FERC Order 2222's docket record (RM18-9), post-order compliance filings on FERC eLibrary, MISO tariff amendments, trade publications (Utility Dive, Energy Choice Matters), and public reg-affairs practitioner literature. **No primary user research was conducted inside the build-challenge window.** Two deliberate choices make this a defensible methodology under the constraints — primary interviews are deferred, not forgotten.

**Claude Code as the domain-literate proxy.** The project was built end-to-end with Claude Code in the loop (Opus 4.7 at the time of writing) — from PRD and TDD authoring to code generation and prompt engineering. Frontier-model training corpora contain substantial coverage of the US energy-regulation domain (FERC orders, state PUC rulemakings, DER-aggregation trade coverage, utility compliance filings, in-house regulatory-affairs practitioner writing), so Claude's baseline framing of what a reg-affairs manager at an investor-owned utility cares about is materially closer to a real Rachel than anything an author with no domain background could produce from a blank page. Every design decision in this PRD was pressure-tested against Claude's domain-grounded intuition — not by asking the model to role-play Rachel-the-persona, but by using its public-evidence-grounded framing as the starting point and iterating from there. The result is a persona + pain articulation that converges with the secondary evidence rather than being invented in isolation. In a 48-hour build-challenge window whose evaluation criteria weigh **technical demonstration + system reasoning** as heavily as product scoping, this is a credible trade-off: spending any of that window on cold-outreach to validate hypotheses the frontier-model proxy can answer well enough would trade one scarce resource for another at low marginal return — assuming cold-outreach would even return a response in that time, which it would not.

**Primary research is deferred to the corporate-sponsor engagement, where it is a no-op.** In the real-world shape of this engagement (post-wedge, with a corporate sponsor), access to Rachel-equivalent in-house regulatory affairs managers is table stakes — the sponsor opens the door, the two parties have a commercial reason to interact, interview budget is a line item, and the first-week discovery plan already named in [§7.5](#75-validation-plan) consumes maybe three person-days to execute. In that engagement model, primary research is not a build-cost but a setup-cost. The specific hypotheses primary research would prioritise falsifying are called out in §7.5; the one that would most meaningfully revise v1 scope is explicitly top-ranked.

**What this does not claim.** This PRD does NOT claim the model-as-proxy methodology produces as high-signal a result as talking to an actual Rachel. It claims the methodology produces a defensible wedge scope under the actual constraints of the build challenge, and that the proxy's limits are known and specific (per §7.5). The submission is honest about which hypotheses are secondary-evidence-grounded and which are speculative.

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

**Scope for v1 (the build challenge submission):** FERC Order 2222, a single short subsection on how aggregators must register with wholesale-market operators. Two versions: the draft text (the NOPR, issued November 2019 in Docket RM18-9) and the final text (Order 2222, issued September 2020). We extract roughly 6 specific obligations from this one subsection — not the whole 660-page rule.

### The loop Rachel runs in Strata

1. **Ingest.** Strata loads the draft version (NOPR) and the final version (Order 2222) of the subsection, plus Verdant Grid's company state (about 8 obligations, 3 projects, and 5 documents, seeded as structured JSON for the build challenge). Rachel does nothing — this is a one-command operator step. **Replaces (from [§1](#1-the-user))**: her morning alert-triage read — *"she scans alerts each morning (Westlaw, Bloomberg Law, FERC mailing lists), opens anything promising on FERC's eLibrary, and reads enough to decide if it affects Verdant."* Rachel keeps the alert feed; Strata removes the "read enough to decide" cost. [TDD §3.1 Ingest](TDD.md#31-ingest).
2. **Diff.** Strata detects material changes between the two versions: obligations that were added, removed, tightened, loosened, or merely reworded (cosmetic edits are flagged but not actioned). Every change links to the exact source passage in both versions. **Replaces (from [§1](#1-the-user))**: the "what changed between NOPR and Final" compare-the-two-versions work that cost Rachel's team the first several weeks of her 2020 Order 2222 effort — *"took Rachel's team six weeks to produce the first mapping memo."* [TDD §3.2 Version Diff](TDD.md#32-version-diff).
3. **Cite.** Every extracted obligation and every detected change carries a verifiable citation to the source passage. A follow-up verification step confirms the cited passage actually contains the stated claim. **Replaces (from [§2](#2-why-alerts-and-search-are-insufficient))**: the "generic AI summarizer" problem — *"Rachel cannot defend a summary to her General Counsel if she can't show the exact source text behind each claim."* Every claim ships with its citing span inlined via `<mark>`-highlight in the UI — the audit receipt is one click, not a reconstruction. [TDD §3.3 Citation Grounding](TDD.md#33-citation-grounding-depth-area) + [§3.4 Citation Verification](TDD.md#34-citation-verification-depth-area).
4. **Map.** For each material change, Strata identifies the specific Verdant obligations, projects, and documents it affects, with the mapping rationale cited to both the regulatory passage and the Verdant-side field it matched. **Replaces (from [§1](#1-the-user) hardest week)**: the "mapping was stale and Rachel had to redo most of it against a different source document" problem when MISO's compliance filing landed. The mapping is versioned and re-runnable against a new source document in minutes, not weeks. [TDD §3.6 Mapping](TDD.md#36-mapping).
5. **Recommend & route per-pair.** For each affected (change × asset) pair, Strata proposes an action (`review`, `amend`, or `no-op`) and a route (Market Operations, Legal, or IT & Metering). One Change touching N diverse assets produces N proposals — a tariff edit routes to Legal, a telemetry data-element change routes to IT & Metering, a market-eligibility shift routes to Market Ops. Routing + action are produced per-item by the same LLM call as the mapping. Multi-reviewer routing is **closed in v1** via the per-item model — see [TDD §8.6](TDD.md#86-multi-reviewer-routing). **Replaces (from [§1](#1-the-user))**: the "routes the memo via Slack and a Jira ticket to one of three internal teams, and tracks open work in a shared spreadsheet" plumbing — the per-pair proposal is Rachel's first-draft version of that routing decision, pre-populated so she edits rather than composes. [TDD §3.6](TDD.md#36-mapping).
6. **Escalate.** When the system's confidence in an interpretation is low, the item is routed to Rachel for triage instead of silently committed. The confidence threshold is a tunable setting per tenant. **Addresses (from [§1](#1-the-user))**: "backlog is always larger than team" — the escalation gate ensures Rachel's bandwidth is spent on genuinely-uncertain items, not reviewing high-confidence straight-throughs the system should have handled. [TDD §3.7 Review & Audit](TDD.md#37-review--audit) (escalation gate) + [§4.3 Configuration](TDD.md#43-configuration) (`EscalationConfig`).
7. **Review & audit per-pair.** Rachel opens the Strata workspace, sees a prioritized review queue with a per-pair table under each change. She approves, overrides, dismisses, or escalates each (change × asset) pair — multi-selecting rows for bulk decisions sharing one audit `batch_id`. Every per-pair decision is written to an append-only audit log, viewable as a read-only chronological page per tenant. Rolling the project state back to a prior point in time is deferred to future work along with the pure-fold-over-log architecture that supports it (see [TDD §8.8](TDD.md#88-pure-fold-over-log-state--rollback)). **Replaces (from [§1](#1-the-user))**: the "shared spreadsheet" that is Verdant's current system of record — *"not queryable, not versioned, and not connected to the external regulatory stream"* ([§2](#2-why-alerts-and-search-are-insufficient)). Rachel's decision pattern (approve the AI's call, override with her own, escalate for GC input, dismiss false positives) matches the memo-approval pattern she runs today. [TDD §3.7](TDD.md#37-review--audit).

### What makes this the wedge, not a bigger first bite

- One rulemaking, one subsection, two versions — enough to demonstrate the full ingest → diff → cite → map → route → audit loop end-to-end.
- Produces a work product Rachel can defend to Verdant's General Counsel: every claim carries a source citation.
- The confidence-and-escalation path is the trust mechanism — Rachel is never asked to trust an unverified AI output. The system fails to a human reviewer, not to a guess.
- Everything else is explicitly out of scope for v1 (see [§6](#6-non-goals-for-v1)).

### Interface surfaces (v1)

Strata ships with two surfaces, one per role:

- **CLI — the operator surface.** `main_detect_changes.py` runs the end-to-end pipeline (ingest + diff + verify + extract + map + enqueue); `main_eval.py` runs the eval harness; `main_review.py` launches the web view. Reviewers evaluating the submission run the CLI. Rachel does not — someone on Verdant's side (today Rachel's analyst, later a scheduled job) runs the pipeline to populate her review queue. [TDD §6.1 CLI](TDD.md#61-cli).
- **Minimal web view — Rachel's surface.** A review-queue page with a card per detected change, each card carrying the source citation inline (with the citing span `<mark>`-highlighted) and a per-pair table of affected items (change × asset rows). Rachel multi-selects rows and decides each pair via four actions (**approve** / **override** / **dismiss** / **escalate**) + per-item action/route edit — bulk-decisions share a `batch_id` in the audit log. A second page shows the per-tenant+environment audit log so Rachel can confirm prior decisions (rollback is deferred — see [TDD §8.8](TDD.md#88-pure-fold-over-log-state--rollback)). Deliberately minimal — enough that Rachel could use it without a walkthrough, nothing more. [TDD §6.2 Web View](TDD.md#62-web-view).

---

## 4. Trust and adoption metrics (for expert users)

Rachel is an expert, not a novice user being onboarded. She will reject a tool she cannot defend. The metrics reflect that.

### Trust metrics (what Verdant's General Counsel asks about)

- **Citation precision ≥ 98%.** Every extracted obligation and detected change cites a source passage; when audited, the cited passage must actually contain the stated claim. Measured on a held-out test set. Measured by [TDD §5.2 Metrics](TDD.md#52-metrics); failure modes catalogued at [TDD §5.3](TDD.md#53-named-failure-modes); mechanism at [TDD §3.3](TDD.md#33-citation-grounding-depth-area) + [§3.4](TDD.md#34-citation-verification-depth-area).
- **Mapping precision ≥ 90%** on suggested obligation / project / document matches, under expert review. False positives waste Rachel's time; precision matters more than recall at this stage. Measured by [TDD §5.2](TDD.md#52-metrics); mechanism at [TDD §3.6](TDD.md#36-mapping).
- **Confidence calibration.** Claims the system labels "high confidence" must be correct ≥ 95% of the time. Everything below the confidence threshold must escalate to a human. Over-confidence is a worse failure than under-confidence. Reliability-diagram measurement at [TDD §5.2](TDD.md#52-metrics); open question tracked at [TDD §8.1](TDD.md#81-confidence-calibration-beyond-self-report).
- **Audit completeness.** Every state transition in the workspace is traceable to an actor, a timestamp, and a prior value. Zero gaps. Mechanism at [TDD §3.7](TDD.md#37-review--audit) + [§2.7 AuditEvent](TDD.md#27-auditevent).

### Adoption metrics (whether Rachel actually uses it)

- **Time to first mapping memo** on a new proceeding: less than 2 days, versus 2–6 weeks today.
- **Share of changes auto-mapped without override:** target ≥ 60% in v1. Below that, the time Rachel spends overriding wrong answers cancels the time saved.
- **Rachel's hands-on time per proceeding:** target ≤ 20% of her current time; the rest is reviewing output and approving.
- **Escalation acceptance rate:** when Strata escalates an item to Rachel, Rachel finds the escalation justified ≥ 80% of the time. If this drops, the escalation threshold is miscalibrated — the system is being either too timid or too aggressive. Threshold tuning open question at [TDD §8.3](TDD.md#83-mapping-confidence-threshold).

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
- The three internal reviewer destinations (Market Operations, Legal, IT & Metering). Pluggable per tenant via the routing Literal on `AffectedItem.proposed_route` ([TDD §2.5](TDD.md#25-mapping)) + the mapping prompt in [`src/mapping.py`](https://github.com/ngs2410/strata-build-challenge/blob/main/src/mapping.py); per-tenant override of the destination set is a post-wedge add through [`DomainConfig`](TDD.md#43-configuration).
- The FERC-specific document parser and docket numbering. Multi-jurisdiction support (state utility commissions, other federal regulators) is called out as a depth-area opportunity in CHALLENGE.md.

**The much larger adjacent opportunity — internal strategic change management.** The four domains above all share "external regulator → regulated enterprise" as the authority axis, but the same change-to-action pattern applies — with far broader reach — to *internal* change inside every enterprise where decisions move faster than they're communicated: strategic pivots, OKR revisions, product decisions, re-orgs, policy updates, roadmap shifts. The "regulator" becomes an executive, a product lead, a strategy function or just an engineer who changes some code; the "regulated entity" becomes a team or an IC whose ongoing work the decision may invalidate, redirect, or accelerate. The pain is near-universal and growing — as enterprises are asked to move faster, teams and ICs discover affecting decisions only when a deliverable misses its mark or someone surfaces the gap in a review, handled today by Slack threads, scattered memos, stale wiki pages, and "did you see the email" rituals that break under volume. The core problem is identical to the regulatory case: *given this version of this internal authority, which of our ongoing projects, commitments, and documents are affected, with the source passage behind every claim and a proposed action routed to the right internal owner.* Strata's architecture ports with minimal adaptation — versioned-document ingest handles exec memos, strategy docs, OKR files, and policy pages; draft vs. final becomes proposed vs. committed decision; obligations become internal commitments; the depth-area investments (citation grounding, audit/rollback) carry over verbatim. The regulated-utility wedge is a credible on-ramp; the TAM beyond it is every enterprise with more than one team.

---

## 6. Non-goals for v1

Explicitly out of scope for the build-challenge submission. Each is a defensible deferral, not an oversight.

- **Full-document ingest.** The one subsection only. Scaling to full 660-page rules is a depth-area problem for document-diffing-at-scale, not a wedge requirement.
- **Multi-tenant demo at scale.** One synthetic tenant (Verdant Grid). The `(tenant_id, environment)` plumbing is in place from day one, so adding a second tenant is a config change, not a refactor. The production shape also assumes shared-database logical isolation (row-level security + per-environment schemas); some regulated enterprises will require stronger **per-tenant physical isolation** — a distinct database or cluster rather than RLS — and the [`Store[T]` facade](TDD.md#44-persistent-storage) reserves that seam, but delivering it per tenant is a post-wedge production option, not a v1 deliverable.
- **Multi-jurisdiction.** FERC only. State utility commissions and other federal regulators come later.
- **Push-based or scheduled ingestion.** The user triggers an ingest from the command line. Scheduled or event-driven ingestion is post-wedge.
- **Integrations with Jira and Slack.** Strata produces the mapping memo and routes items to in-system reviewer queues. External integrations are post-wedge.
- **Tenant-chosen models, providers, and API keys.** v1 uses a single commercial frontier model (OpenAI) with structured outputs, under our account and our API key, with no fine-tuning. A credible production path lets a tenant **supply their own API key**, **select an alternative provider** (Anthropic, Google, a self-hosted vLLM, or an on-prem endpoint), or **point at a tenant-owned fine-tuned model** — scenarios driven by existing procurement contracts, data-sharing constraints that limit which provider sees regulated content, or in-house model investments. The [`ModelCaller` dispatch seam (TDD §4.1)](TDD.md#41-llm-dispatch-modelcaller) reserves the hook; wiring per-tenant provider/key/model selection is a post-wedge production option.
- **Rich workspace UI.** The v1 web view is deliberately minimal: a review-queue page and an audit-log page (see [§3](#3-the-first-change-to-action-wedge-workflow)). Dashboards, team-wide analytics, bulk operations, inline mapping edits, saved filters, and SSO authentication are all post-wedge.
- **Multi-user web UI.** The v1 web view is single-user — one reviewer, no authentication, no concurrency control. Rachel's actual team at Verdant has three analysts under her ([§1](#1-the-user)) who would plausibly all operate against the same review queue in a production deployment, which requires concurrency primitives Strata does not implement in v1: per-pair optimistic or pessimistic locking so two analysts can't silently last-write-wins the same (change × asset) decision; presence indication on in-flight rows; and isolation of the `_fold_status` read from concurrent writes. The audit log already records an `actor` per event, so the data-model foundation is in place ([TDD §2.7](TDD.md#27-auditevent)); the UI + auth + locking layer that would make it safe for a team is a post-wedge production option. See [TDD §8.10](TDD.md#810-multi-user-concurrency-control).
- **Tenant-chosen deployment target.** v1 ships as a developer-run Python app on one host. A containerized (OCI-image) deployment that an enterprise tenant can run on **their chosen cloud provider** (AWS / GCP / Azure) or **on-premise / air-gapped** is a standard expectation for regulated buyers — driven by data-residency rules, procurement-approved provider lists, or security teams that insist the workload live inside their own perimeter. Supporting this is a post-wedge production option, not a v1 requirement.

---

## 7. Open questions and unvalidated assumptions

Per PREP_MATERIAL.md, calling these out explicitly is more valuable than overclaiming.

- **Rachel is a plausible proxy for the real user we have not yet interviewed.** The persona and workflow are drawn from public regulatory-affairs trade literature and FERC Order 2222 implementation case studies, not direct user research. First-week discovery plan: interview three in-house regulatory affairs managers at MISO or PJM utilities before any further scope commits.
- **The ≥ 90% mapping-precision target is plausible but unvalidated.** We need to run the full workflow against the Order 2222 excerpt and measure where we actually land before committing to this as a shippable threshold. [TDD §8.3](TDD.md#83-mapping-confidence-threshold) tracks the empirical set-point; harness at [TDD §5.4](TDD.md#54-harness).
- **"Material change" is operator-defined, not objectively defined.** v1 ships with a five-category taxonomy (added / removed / tightened / loosened / cosmetic). Expect iteration with Rachel's input. [TDD §8.5](TDD.md#85-material-change-taxonomy-stability).
- **Confidence-threshold calibration is a per-tenant setting.** v1 ships with a single default; whether that default is the right starting point for a different regulator or company shape is unknown. [TDD §8.1](TDD.md#81-confidence-calibration-beyond-self-report) (calibration beyond self-report) + [TDD §4.3 `EscalationConfig`](TDD.md#43-configuration).
- **Synthetic company context fidelity.** The ~8 obligations, 3 projects, and 5 documents seeded for Verdant Grid are modeled on public MISO tariff provisions and industry-standard DER aggregation programs. They are plausible, not authentic. A real engagement would ingest the enterprise's own data. Gold-set growth tracked at [TDD §8.4](TDD.md#84-gold-set-drift-and-growth).
- **Multi-reviewer routing — closed in v1 via per-item (action, route).** Each `AffectedItem` carries its own `proposed_action` + `proposed_route`; one Change touching N diverse assets produces N proposals that can route to N different teams. Rachel's per-pair `AssetDecision` records her approval/override/dismiss/escalate per (change × asset) pair. The mapping-level single-destination model this bullet originally called out is gone — replaced by per-item fields. [TDD §8.6](TDD.md#86-multi-reviewer-routing) tracks what's still deferred (notification fan-out layer: who gets emailed first vs. CC vs. silent — a workflow-engine problem, not a schema problem).

---

## 7.5 Validation plan — what primary research falsifies first

The hypotheses below are ranked by what primary research in a corporate-sponsor engagement would most urgently falsify. The ranking reflects where v1 design is weakest-tested against secondary evidence, not the amount of literature the current design is grounded in (which varies). The top-ranked hypothesis is the one whose falsification would most materially revise v1 scope; the others are important but either config-pluggable (secondary) or numeric-tuning (lower priority).

### Top priority — the per-pair review UI is the right workload shape for Rachel

The current UI (per-pair table of (change × asset) rows under each ReviewItem card, with multi-select bulk toolbar + inline per-row edit, four verbs: approve / override / dismiss / escalate) assumes Rachel can triage N pairs per Change efficiently via a mix of bulk and per-row operations. The hypothesis to falsify: *at realistic company-context size (1000+ obligations, as opposed to the 16-item demo), the per-pair surface becomes a scrolling nightmare and the bulk-select workflow doesn't actually accelerate — real reg-affairs managers either (a) collapse to per-change-only decisions giving up per-item fidelity, or (b) adopt a filter-first workflow we didn't design for.*

**Why this is first.** The whole product's value proposition rests on Rachel's throughput. If the per-pair UI is wrong-shaped, no amount of upstream accuracy saves it — Rachel will go back to her Jira/Slack/spreadsheet workflow and treat Strata as a research tool rather than a workspace. Every other v1 decision is reversible with a config change; this is architectural.

**Needed evidence (first-week-of-sponsor):**
- Watch 3–5 reg-affairs managers work through a 50+ affected-item mapping (synthetic or redacted-real; MISO Order 2222 compliance filings are a plausible real source).
- Measure time-to-decision per pair; identify where they bulk-select vs per-row edit vs defer.
- Count "defer this one" moments — the current UI has no explicit defer verb; is that a gap?
- Count re-visit moments — does Rachel return to pairs she previously decided?

**Likely scope revisions if falsified:**
- Collapse the bulk toolbar into a dedicated bulk-mode UI with a separate per-pair edit mode (modal switch rather than always-both).
- Add a `deferred` verb alongside approve/override/dismiss/escalate — orthogonal to disposition; a per-pair status that doesn't commit a decision but hides the pair from the active queue until a date trigger.
- Group affected items by `proposed_route` so decisions-by-team become the first-class view (Rachel acts in a "do all the Legal items in one sitting" mode).
- Add a filter-first entry — "show me only the items at `match_confidence=5`" or "only `proposed_action=amend`" — before the bulk toolbar is useful at scale.

The product impact of this validation is the difference between shipping an opinionated workflow (if the current UI holds) and shipping a flexible one (if it doesn't). The engineering work either way is bounded — the data model already supports both shapes.

### Secondary — the three routing destinations match a real utility's team shape

**Hypothesis to falsify:** *real utilities split responsibility differently than the Verdant seed assumes. For example: a dedicated Compliance team separate from Legal, a Risk function that owns some filings, Operations split across Market Ops and Grid Ops, a Rates team distinct from Market Ops, or an outsourced-to-counsel model for the Legal items.*

**Needed evidence:** interview 2–3 reg-affairs managers about current internal routing destinations (who gets what for an Order 2222-shaped rule).

**Likely scope revisions:** mechanical, not architectural. `AffectedItem.proposed_route` becomes per-tenant pluggable via `DomainConfig` (the seam is already reserved per [TDD §4.3 Decisions](TDD.md#43-configuration)); the mapping prompt's `ROUTING_DESTINATION_GLOSSARY` becomes tenant-rendered rather than hard-coded. Same shape as changing the `obligation_type` taxonomy per tenant.

### Secondary — the action rubric (review / amend / no_op) is the right verb set

**Hypothesis to falsify:** *Rachel wants more granularity in the action enum — e.g., `assign_to_analyst`, `defer_to_next_quarter`, `escalate_to_gc`, `watch_for_updates`, `respond_in_comments`.*

**Needed evidence:** interview + review Rachel's current Jira/Slack workflow to see what verbs she actually uses when routing today.

**Likely scope revisions:** same shape as routing — the `proposed_action` Literal becomes per-tenant pluggable via config. Not architectural.

### Lower priority — numeric thresholds and precision targets

The specific numbers in [§4 Trust metrics](#trust-metrics-what-verdants-general-counsel-asks-about) and the mapping emission-floor at `match_confidence=3`:
- citation precision ≥ 98%
- mapping precision ≥ 90%
- auto-mapping share ≥ 60%
- escalation acceptance ≥ 80%
- `match_confidence` emission floor
- model choice (`gpt-5.4-nano` default)

These are all tuning questions, not architectural ones. They calibrate against observed Rachel accept/dismiss outcomes over the first N review sessions; the eval harness ([TDD §5](TDD.md#5-evals-depth-area)) measures each per-run against the gold set; the right starting point for a different regulator/company shape is unknown but easily re-tuned (edit config, re-run the eval). Falsification = different numbers, not different system shape.

### Lower priority — the obligation-type taxonomy

Open question in [§7 bullet 3](#7-open-questions-and-unvalidated-assumptions) tracks this. Falsification would surface categories we're missing (e.g., `recordkeeping` as distinct from `reporting`), not restructure the model.

### What this plan is NOT

It is not a research gantt chart. The ranking is by scope-revision cost if the hypothesis is falsified; the first-two-weeks sequence will be shaped by the sponsor's own priorities + Rachel's availability. The plan commits to the order of investigation; it does not commit to a specific calendar.
