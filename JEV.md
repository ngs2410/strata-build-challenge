# Jev — TypeSafe AI's System One Decision Model

## Identity and purpose

**Jev** is a specialized artificial intelligence model developed by **TypeSafe AI**, founded by former OpenAI researcher Diogo Almeida. Released in September 2026, Jev is the first publicly available example of what TypeSafe calls a **System One Model**: an AI architecture optimized for extremely fast, inexpensive, structured, probabilistic decisions rather than natural-language generation.

Official overview: https://typesafe.ai/blog/introducing-system-one-models-and-jev

Documentation: https://docs.typesafe.ai/

API reference: https://docs.typesafe.ai/api

Jev should not be confused with a conventional generative LLM such as GPT, Claude, Qwen, or Gemini. It does not generate arbitrary text, reasoning traces, explanations, or code. Instead, it evaluates supplied information against explicitly defined questions and returns strongly typed answers, probability distributions, and measures of uncertainty.

Its intended role is a low-latency inference and decision-making component embedded inside conventional software, AI agents, classification pipelines, automated workflows, and production systems.

**Fundamental abstraction: unstructured or structured state + typed questions → bounded probabilistic decisions.**

## Conceptual architecture

Traditional autoregressive LLMs generate output tokens sequentially, conditioning each successive token on preceding tokens. Even when forced to produce JSON, these models are fundamentally generating strings whose contents must be interpreted and validated.

Jev uses a different approach: it is trained specifically for decisions over predefined answer spaces. It evaluates multiple independent questions against shared input state and produces their outputs in parallel, without autoregressive natural-language generation.

TypeSafe describes its approach as combining a specialized architecture, parallel sampling, and **Reinforcement Learning for Calibrated Decisions (RLCD)**. RLCD is intended to optimize both decision quality and the probability estimates associated with those decisions, rather than optimizing the generation of human-preferred text.

The name "System One" refers to Daniel Kahneman's distinction between fast, intuitive System 1 thinking and slower, deliberative System 2 reasoning. Jev is designed for the former: rapid judgments that can be decomposed into relatively atomic decisions.

The model's name references William Stanley Jevons and the Jevons paradox: greatly reducing the cost of a resource can dramatically increase its total consumption by making previously uneconomic applications practical.

## The three supported decision primitives

Every Jev request specifies one or more typed questions. There are three primary question types.

### 1. Choice — categorical classification

A `choice` selects one option from a predefined enumeration of possibilities. The developer supplies the candidate options and, preferably, semantic descriptions of what qualifies for each option.

It returns the highest-probability candidate, a probability distribution over all candidates, and an aggregate confidence score.

Examples include classifying customer support messages into billing, technical, and sales; selecting an appropriate agent tool; assigning a document category; choosing a workflow branch; or identifying the most relevant item from a shortlist.

Choice supports up to 255 defined alternatives per question.

For genuinely mutually exclusive classification, Choice is generally appropriate. Include an explicit "none", "other", or "insufficient evidence" option when the application requires that outcome.

### 2. Score — ordinal evaluation

A `score` evaluates the input against an ordered rubric consisting of 2–10 levels, with descriptive criteria for each level.

The response contains a probability distribution across the rubric levels, a probability-weighted numerical score, the rubric legend, and confidence.

Examples include measuring severity, urgency, sentiment, quality, relevance, or business risk against defined qualitative scales.

Unlike an ordinary integer category, Score's result may be fractional because it represents a probability-weighted expectation across ordered levels. It should not be treated as a reliable measurement of an arbitrary continuous real-world quantity.

### 3. Noul — binary probabilistic judgment

A `noul` evaluates whether a specific proposition is true and returns a floating-point probability between 0 and 1 representing the model's estimated probability that the proposition is true.

Examples include "Does this document indicate that the project deadline changed?", "Is this message requesting a refund?", "Does this passage support the supplied claim?", or "Does this record exhibit the specified failure pattern?"

Noul is particularly useful for multi-label classification because multiple independent yes/no questions can evaluate the same input simultaneously. Labels need not be mutually exclusive.

Unlike Choice and Score, Noul does not return a separate confidence field. Its uncertainty is represented directly by its probability, with values near 0.5 indicating greater ambiguity.

## API and invocation

Jev is accessed through TypeSafe's hosted API.

- Endpoint: `POST https://api.typesafe.ai/v1/systemone`
- Authentication: Bearer token using `TYPESAFE_API_KEY`
- Stable model ID as of October 2026: `jev-1.13.0`
- Rolling stable alias: `jev-latest`
- Rolling preview alias: `jev-preview`
- Official Python package: `typesafe-sdk`
- Official JavaScript/TypeScript package: `@typesafe-ai/sdk`

Requests include a `state` field, a `model` identifier, and a dictionary of named `questions`.

The state can contain plain text, structured JSON objects, or arrays of text values, allowing it to represent documents, messages, business records, application state, or agent context.

Each question specifies its `type`, natural-language `instructions`, and any required `criteria` defining acceptable answers or scoring levels.

For example, an enterprise decision record describing a product release delay could be evaluated with three questions in a single request:

- `issue_type`: Choice among "rug_pull", "exception_creep", "re_litigation", "double_build", "time_bomb", or "none", with explicit definitions for each.
- `severity`: Score across four levels such as negligible, moderate, serious, and critical.
- `requires_escalation`: Noul evaluating whether the available evidence warrants escalation.

The response contains an `answers` object keyed by the question identifiers. Choice results include `choice`, `probabilities`, and `confidence`; Score results include `score`, `legend`, `probabilities`, and `confidence`; Noul results contain `noul`. The response also identifies the model version and token usage.

These are native decision outputs rather than free-form text constrained after generation.

## Parallel evaluation

A particularly important feature of Jev is that multiple questions sharing the same state can be evaluated concurrently within one request.

Unlike an autoregressive LLM producing a sequential response, Jev computes the answers to independent questions in parallel. Adding questions typically has much less incremental latency than issuing additional LLM requests.

This architecture favors **decomposition into atomic judgments**.

Rather than asking one complicated question requiring several reasoning steps, an application can define numerous simpler questions, retrieve their probability distributions, and combine them through deterministic business logic.

For multi-label tasks, separate Nouls allow labels to overlap naturally. For exclusive classification, a Choice represents competing alternatives. For multidimensional judgments, multiple Score questions can evaluate independent characteristics.

The application, rather than the model, should implement aggregation, weighting, thresholds, validation, and consequential decisions.

## Performance, economics, and deployment

TypeSafe advertises typical API response times of approximately **70–500 milliseconds**, depending on workload, infrastructure, and network conditions.

Published pricing for Jev 1.13 is **$0.042 per million input tokens**, or $42 per billion input tokens, with output tokens currently free.

At that price, one million requests averaging 1,000 billable input tokens each would cost approximately $42 in model inference charges, excluding other infrastructure and pipeline costs.

TypeSafe reports performance and economic advantages reaching approximately two orders of magnitude over conventional frontier LLMs on selected structured-decision workflows. Its launch evaluation reported approximately 193.6× faster and 444.6× cheaper performance on selected benchmarks. These are provider-reported, workload-dependent comparisons, not universal performance guarantees.

The official context limits are 64,000 tokens per request in aggregate, with a separate 32,000-token limit covering the state and longest individual question. Published rate limits are subject to change.

Jev is delivered primarily as a hosted API. The proprietary model weights are not provided for ordinary local deployment, and the official service does not currently support per-customer LoRA or fine-tuning. Domain-specific behavior is controlled through state, question instructions, criteria, and surrounding application logic.

## Probability, calibration, and confidence

Jev's primary distinguishing capability is not merely structured output but **probabilistic decision-making**.

Choice and Score return full distributions, allowing downstream software to examine competing hypotheses rather than only the selected answer.

The `confidence` field is a derived measure of how concentrated the distribution is. It is not identical to the top candidate's probability, and it must not be interpreted directly as guaranteed classification accuracy.

Noul returns a direct probability for its binary proposition and no separate confidence.

TypeSafe trains Jev with an emphasis on calibration, meaning predicted probabilities are intended to correspond to observed outcome frequencies. However, calibration and accuracy must be tested independently for the application's actual data distribution.

A robust production system should measure classification quality, precision, recall, calibration, threshold behavior, and failure modes on held-out labeled data.

An effective architectural pattern is confidence-gated or probability-gated routing: automate sufficiently clear low-risk decisions, route ambiguous examples to a stronger reasoning model or human reviewer, and apply higher thresholds to expensive or irreversible actions.

Thresholds should be empirically selected rather than universally fixed.

## Key strengths and practical applications

Jev is particularly well suited to high-volume, low-latency decision workloads where the input may be unstructured but the possible outputs can be specified in advance.

Representative applications include semantic classification, document and event tagging, multi-label detection, moderation, sentiment and risk scoring, intent routing, agent tool selection, escalation detection, RAG passage relevance assessment, citation-support verification, workflow branching, exception identification, and LLM output evaluation.

It can act as a semantic decision layer between ordinary application code and more expensive generative models.

For example, a system might use deterministic code for parsing and retrieval, Jev for identifying relevant or risky records, Claude or another reasoning model for ambiguous cases requiring detailed analysis, and conventional code for enforcing policies and executing actions.

This arrangement makes it possible to use sophisticated semantic judgment at far higher volumes than would be economical with frontier generative LLMs alone.

## Limitations and failure modes

**Jev's output type safety is not a guarantee of semantic correctness.** Its architecture prevents arbitrary out-of-schema textual generation, but it can still make incorrect decisions, misunderstand context, misinterpret evidence, or assign misleading probabilities.

TypeSafe documents several weaknesses in Jev 1.13:

- Literal interpretation of instructions, including sensitivity to negation and boundary conditions.
- Poor suitability for exact arithmetic, counting, numerical comparisons, and precise date reasoning.
- Reduced reliability on tasks involving multiple levels of indirection or lengthy reasoning chains.
- Accuracy degradation when state contains substantial irrelevant information.
- Vulnerability to adversarially crafted input or prompt-injection-like content.
- Sensitivity to ambiguities or contradictions between instructions and criteria.
- Potential sensitivity to the ordering of Choice alternatives.
- Inability to generate arbitrary explanations, text, code, or open-ended extracted values.
- Uneven multilingual performance, with English currently the strongest supported language.

The model should not be asked to execute complex algorithms, perform calculations, generate summaries, reason over long chains of interdependent implications, or invent answer categories dynamically.

When evaluating extracted values, applications should preferably generate candidate values through ordinary parsing or an LLM and ask Jev to select among those candidates.

Similarly, it should not be assumed that Jev's confidence will detect every incorrect or adversarially influenced prediction.

## Engineering guidance for applications and coding agents

When a project refers to "Jev", interpret it as TypeSafe AI's System One decision model, not a replacement for Claude or another generative model.

When designing integrations:

1. Identify bounded semantic decisions that are currently handled by generative LLMs or fragile heuristic code.
2. Preserve deterministic code for tasks that can be computed exactly.
3. Represent each semantic decision as a narrowly scoped Choice, Score, or Noul question.
4. Make answer-space criteria explicit, including meaningful boundary cases and abstention possibilities.
5. Batch independent questions over the same state whenever practical.
6. Keep the supplied state compact, relevant, and clearly structured.
7. Treat probabilities and confidence as numeric inference outputs, not guarantees of correctness.
8. Keep question definitions, thresholds, and versioned evaluation configurations under source control.
9. Evaluate predictions against representative labeled examples and compare with existing LLM-based or classical ML baselines.
10. Measure the complete pipeline's cost, latency, precision, recall, calibration, and fallback rate rather than relying only on provider benchmarks.
11. Log the actual model version because rolling aliases can change behavior.
12. Use conservative fallbacks for uncertain, high-risk, or out-of-distribution inputs.

Jev is a potential alternative to traditional discriminative classifiers, zero-shot LLM classification, and LLM-as-judge pipelines, but should be compared empirically rather than presumed superior.

**The essential design principle is: use Jev for fast, typed, probabilistic semantic judgments; use generative models for language and complex reasoning; use ordinary code for exact computation, orchestration, and enforcement.**

## Authoritative references

- Official introduction: https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Documentation: https://docs.typesafe.ai/introduction
- API reference: https://docs.typesafe.ai/api
- Models and pricing: https://docs.typesafe.ai/models
- Confidence semantics: https://docs.typesafe.ai/confidence
- Documented weaknesses: https://docs.typesafe.ai/model-jaggedness/jev-1.13
- Using Jev with coding agents: https://docs.typesafe.ai/introduction/coding-agents
- Official coding-agent skill: https://docs.typesafe.ai/agent-skill

Treat these official references as authoritative when implementation details, limits, model versions, or API schemas change.


TypeSafe publishes an official Claude Code skill that provides the API knowledge, integration patterns, and implementation conventions directly to Claude Code. Its installation commands are:

claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai
