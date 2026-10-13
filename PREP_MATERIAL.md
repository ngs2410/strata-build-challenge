AI Fund · Engineer in Residence
Preparing for the Build Challenge
What we look for in your submission, what the AI interview covers, and what to expect if you advance to the partner panel. Worth reading before you start building.

Source required
Runnable product plus inspectable implementation
Two-part assessment
Build submission plus 45-minute AI interview
Panel preparation
Technical depth and implementation understanding
Evaluation
How we review.
The Build Challenge is a compressed simulation of the first weeks of the residency. Reviewers start by verifying that a substantive implementation exists: an accessible prototype plus source code or equivalent implementation evidence. Your PRD and TDD help us understand your reasoning. They do not replace implementation evidence.

We look closely at two things: the system you submitted, and whether you understand it.

Artifact quality
Is the submitted system good enough against the assignment? We run the assigned core workflow end to end, check every required behavior in the prototype or source, read the source for real depth, look for a concrete failure path plus tests or evaluations, and compare your PRD and TDD against what you actually built. We also look for at least two distinct technical spikes: mechanism depth, empirical validation, reliability discipline, performance work, or consequential architecture judgment.

Implementation understanding
Do you understand the system you submitted? Expect to trace your core loop end to end; open your repository, read your core code aloud, and make one small directed change live; explain a concrete failure and the evidence that your fix works; and show how the AI part actually works at the mechanism level — what it receives, what it emits, and one specific thing it gets wrong. The interview then goes deeper on your architecture and how the system holds up as it scales.

Submission
What to submit.
Reply to your challenge email using these exact labels. They map directly to how we review, so complete every line.

PRD:
TDD:
Prototype:
Source code:
Access notes / credentials:
Known limitations or broken paths:
Requirements
The prototype must be runnable or inspectable.
Source code or equivalent implementation evidence is required.
Private repositories must include reviewer access or a zip snapshot.
Include the exact command or URL that runs the core workflow.
Include the exact command that runs your tests.
Do not share personal credentials or account secrets. Use scoped or throwaway credentials in your access notes.
We do not ask for a demo video or recording, and you do not need to make one.

Rough is fine. Use any tools and AI assistants you want. We evaluate the submitted system and whether you understand it, not who typed each line. You do not need to preserve or explain your commit history.

Implementation evidence
A live product, screenshot, document, or recording does not replace inspectable implementation evidence. If we cannot access your source, the review cannot be completed until access is resolved.

Product
Building for a specific user.
We read your PRD, and a build aimed at one specific user is usually sharper and easier to defend. Treat this section as advice for making the work stronger.

Design for one specific user
If your answer to "who is this for" includes more than one type of person, you have not finished the work. A small business owner and a marketer at a large company have different workflows, different pain, and different products that serve them. Pick one and design for them.

Bring the strongest user evidence you can get
Direct feedback from real users in your target audience is the strongest evidence. If you can reach them during the challenge window, show them the prototype, note what they did and did not understand, note which features they ignored, and let it change the build. If you cannot reach target users in time, use credible proxy research instead: practitioner writing, comparable products, domain data, your own experience inside the workflow. Then be explicit about what remains unvalidated and bring a concrete plan for your first week of discovery.

Thin user evidence is not a scored gap. Strong user evidence does not substitute for inspectable implementation evidence.

Be able to explain why you built what you built
Every feature in your prototype should map to a specific user pain. If you added a feature that the user does not need but engineers would, like an internal eval dashboard for a non-technical user, expect to be asked why. The honest answer is fine. The unprepared answer is not.

Technical
Technical ownership.
Engineer in Residence is an engineering role, and both the AI interview and the panel test whether you own your build at the mechanism level. The preparation that works is knowing your own repository in detail.

Prepare with your repository open
Have the repository open when you interview.
Know the exact file and function that implement your core loop.
Be able to narrate that loop in order, step by step, as if you were running it live.
Be ready to open a file, read your own code aloud, and make one small directed change on the spot.
Explain where state lives, which validations and guardrails fire, what happens on failure, and where latency and cost go.
Know how the AI part works at the mechanism level: what your model or agent receives, what it emits, and one specific thing it gets wrong. If your submission has no AI component, be ready to do this for a prior AI system you built yourself.
Describe one real bug from the build: the wrong turns and the eventual fix, and the evidence that the fix works.
Explain what your tests and evals prove, and what they cannot prove.
Be ready to reason through a small unfamiliar technical problem out loud.
Be intentional about what you used AI to build
Using Claude Code, Cursor, or another AI coding tool to build is expected, and AI tools stay allowed throughout the interview. We evaluate the submitted system and whether you understand it, not who typed each line. You should be able to say "I chose this approach because of X, and I asked the AI to implement it." Needing a tool to locate or read your own code is the gap we are testing for.

Know your build — "I don't know" is only fine on the unfamiliar
There are two very different "I don't know" answers. On a topic or technology you have never worked with, "I have not used that, and here is how I would approach learning it" is a strong, honest signal, and confident bluffing on it is a red flag. But "I don't know, that's just what the AI decided" is not an acceptable answer about your own build. You chose to ship it, so you own every decision in it. Be ready to explain why your AI tools built it the way they did — you are ultimately responsible for the output they produce.

Role fit
Engineer in Residence is an engineering role. If your primary background is product or business rather than engineering, talk to your recruiter before submitting.

Prototype
Building the prototype.
Build for one specific user and one specific problem. Keep the interface simple enough that your target user could use it without a walkthrough. Make your hardest technical decision visible in the product rather than buried in the stack. If you can reach real users before you submit, show it to them and let what you learn change what you build. You should be able to tell the story of how the prototype evolved and why.

AI interview
The AI interview.
The Build Challenge is a two-part assessment: the build submission, then a required 45-minute voice interview with our AI interviewer. Start it within 30 minutes of sending your submission, so plan the submission and interview as one sitting.

What it covers
Your core loop, traced end to end: what comes in, what transforms it, where state lives, and what comes out.
A live code walk: you open your repository, read your core code aloud, and make one small directed change live. Have the repo open.
How the AI works at the mechanism level: what your model or agent receives, what it emits, and one specific thing it gets wrong — for this build, or for a prior AI system you built yourself.
A deeper look at your architecture: the strongest case for the option you rejected, your riskiest assumption, which AI building blocks you used and why, and how the system holds up as it scales.
Your read on the user, the market, and why this problem fits you.
Logistics
Your invitation arrives by email from DeepInterview.AI (noreply@deepinterview.org) alongside the challenge email. Start the interview only after you have submitted Part 1. No additional preparation is needed beyond knowing your own build, and none helps.

Documentation
Writing the TDD and PRD.
Both documents are read closely, and both the AI interview and the panel dig into them. They clarify your reasoning. They do not replace implementation evidence.

The TDD
The Technical Design Document (TDD) is where we see how you think in systems. For every building block and technology you chose, explain what went into that decision: the alternatives you considered, the trade-offs you accepted, and the constraints that shaped the choice. A diagram without the reasoning behind it tells us very little. The panel will walk through this document in depth, so be ready to explain why each piece of your architecture was picked and what you weighed in making each decision.

The PRD
The Product Requirements Document (PRD) is where we see your picture of the problem. It should make clear who the user is, what pain you are solving, how user feedback factored into what you built, and why this product is the right answer. The PRD and the prototype should tell the same story. The panel will read it closely and dig into how the user and their feedback shaped the build, and whether you have a complete picture of the problem you are solving and why it matters.

Panel
The panel interview.
The panel is a conversation with AI Fund partners, usually held onsite. It builds on your submission and your AI interview, and it centers on technical depth and your understanding of what you built.

AI expertise and founder market fit (FMF)
How you build with agentic AI tools, how you build AI products and applications, how current your AI practice is, and why you are drawn to this specific problem.

How to prepare
Be ready to walk through your prototype and code in person.
Have a 90-second product pitch ready: who, what, why, what is hard about it.
Practice explaining your hardest technical choice to a non-expert.
Reread your TDD and PRD the morning of the panel.
Communication matters
Clear communication is part of how we evaluate you. If English is not your first language, that is fine. Speak clearly, take your time, and ask the panelist to rephrase if you are unsure what they are asking.
