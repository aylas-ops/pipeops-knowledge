# The thirty-day pilot

The goal is not documentation coverage. It is a demonstrable answer to one
question: **do AI answers get measurably better when grounded in reviewed,
cited organisational knowledge?**

Everything below is in service of being able to answer that in four weeks with
evidence rather than enthusiasm.

## Pick one domain

Not the whole enterprise. One domain, with known owners, recurring questions,
and enough existing metadata to bootstrap. The best first domain is the one
where somebody is currently answering the same question every week.

| Candidate | Why it works | What you'd extract |
|-----------|--------------|--------------------|
| Platform / infra | On-call context is high-value and goes stale fast | Services, runbooks, escalation paths, ADRs |
| Product telemetry | Event naming drifts; late arrival trips everyone up | Datasets, events, funnel metrics, instrumentation notes |
| Billing / revenue | Highest business value; best showcase for Attested Computations | Metrics, pricing definitions, source tables, caveats |
| Support operations | Combines process knowledge with dashboards | Runbooks, case taxonomies, queues, SLA metrics |

Write the choice down along with the three questions you expect the agent to
answer at the end. Those three questions are your evaluation set.

## Week 1 — repository and first drafts

**Objective:** 20–50 concepts on disk, generated, unreviewed, honest about it.

```bash
okf extract-github --org your-org --bundle bundles/<domain>
okf reindex bundles
okf validate bundles       # expect owner errors; that is the design
```

- Create the bundle directory and let the extractor fill it.
- Every generated concept lands `status: draft`. Do not promote them yet.
- Add the real teams to `owners:` in `okf-profile.yaml`.
- Add a `/bundles/<domain>/ @team` line to `.github/CODEOWNERS`.

**Deliverables:** bundle on a branch, source inventory, the list of repos or
tables that had no owner.

**Trap:** treating the extractor output as done. It is scaffolding. The number
of files is not the metric.

## Week 2 — owner review

**Objective:** the drafts become knowledge.

Open one PR per owning team, not one giant PR. The PR template asks the five
questions that matter:

1. What is this asset used for?
2. What should analysts or agents avoid?
3. What depends on it?
4. What do people commonly get wrong about it?
5. When should this be reviewed again?

Question 4 is the one that produces the value. Push back on reviews that only
tidy the prose.

As each concept is reviewed: set `owner`, set `status: stable`, add a
`verified: { by: human:<id>, at: ... }` entry, set `stale_after`.

**Deliverables:** reviewed concepts, populated ownership registry, a
contribution habit.

**Trap:** review-by-committee. One accountable owner per concept, approving in
their own name.

## Week 3 — retrieval and an agent

**Objective:** cited answers to the three questions you wrote down in week 0.

```bash
okf index bundles
okf query "<your evaluation question>" --format context
okf serve
```

Point an agent at `http://127.0.0.1:8787/search?q=...&format=context` as a
tool. The context block already instructs it to cite; the citations are
`concept#heading`, so you can check each one by opening the file.

Run each evaluation question three ways and keep the transcripts:

- the agent with no knowledge base,
- the agent with the raw source systems,
- the agent with OKF retrieval.

**Deliverables:** search index, an agent that cites, three transcripts.

**Trap:** letting the agent answer without citations. An uncited correct answer
is indistinguishable from a lucky one, and proves nothing.

## Week 4 — CI, measurement, and the case for expansion

**Objective:** the thing keeps working after you stop paying attention to it.

- CI is already in `.github/workflows/validate-okf.yml`; make it a required
  check on `main`.
- Enable the weekly freshness run so decay surfaces on a schedule.
- Score the transcripts. For each answer: correct? cited? citation actually
  supports the claim?

```bash
okf stats     # coverage by type, status, trust tier; broken links
```

**Deliverables:** required CI check, a quality report, a rollout backlog
ordered by which domain has the next-worst repeated-question problem.

## What to measure

Report these, not file counts:

| Metric | Why |
|--------|-----|
| Citation rate | Share of agent answers carrying an OKF citation. |
| Citation correctness | Share where the cited passage actually supports the claim. This is the number that matters. |
| Coverage | Share of the domain's key assets with an owner, caveats, and a source link. |
| Repeated questions | Volume of the recurring questions that started this, before vs after. |
| Time to onboard | How long a new engineer takes to get productive in the domain. |
| Decay | Broken links and stale documents caught by CI per week. |

`okf stats` gives you coverage and decay directly. The first two need a human
reading transcripts — budget for it, because they are the pilot's actual result.

## The pitfalls, in the order teams hit them

1. **Starting too broadly.** One domain. Prove it. Then expand.
2. **Treating generated metadata as finished documentation.** Human context is
   the entire differentiator; without it you have built a slower catalog.
3. **Designing a large schema before real consumers exist.** The house profile
   is deliberately small. Add a field when something breaks without it.
4. **Indexing drafts and stale documents without status filters.** Retrieval
   excludes them by default here — do not turn that off to improve recall.
5. **Letting AI answer without citations.** Then you cannot tell whether the
   knowledge base did anything.
6. **Ignoring access control.** OKF concentrates business context. Repository
   permissions must match the permissions on the systems it describes, and the
   serving layer must not become a way around them.

## After the pilot

Expand one domain at a time, in the order the repeated-question data suggests.
Add OKF updates to the existing launch and change workflows so new certified
metrics, production datasets, and services arrive with a concept rather than
needing a backfill. That is the point at which it stops being a project.
