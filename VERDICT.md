# Project Verdict — Claude Agent Station

**Status: Archived — 2026-06-05**
**Decision: stop development. The code is sound; the goal is not reachable by anyone in 2026 without a capability we do not have.**

This document is the honest post-mortem for Claude Agent Station: a self-hosted platform for
autonomous Claude-Code agents that open and merge pull requests on our GitHub projects
*without a human in the loop*. We are archiving it. This explains why, with evidence, so that
the work is remembered for what it was — a competent bet against an unsolved frontier — and so
that anyone building in this space can skip the two months it took us to learn the lesson.

The short version: **we built a capable system whose single missing piece is the one piece the
entire field has not solved — trustworthy verification of correctness without a competent
reviewer.** We don't have such a reviewer, and we honestly never will across the many languages
our agents touch. That is not a flaw in the build. It is the frontier.

---

## 1. How this verdict was reached

This is not a gut call. The decision rests on four independent investigations:

1. **A 9-dimension architectural audit** of the real code (`origin/dev`, the active line — not
   the stale snapshot we had been staring at).
2. **Ground-truth test execution** — we actually ran the backend suite.
3. **A live, pre-registered experiment** — we ran the real orchestrator, fully unsupervised, on
   four controlled tasks and graded the output ourselves against a rubric fixed *before* running.
4. **A survey of the 2026 state of the art** in autonomous software engineering, fact-checked
   against primary sources.

The rubric and protocol for (3) were written and locked before any run, specifically so the
conclusion could not be rationalized after the fact.

---

## 2. Finding #1 — The code is good. That was never the problem.

We began believing the project was "bugged everywhere, unmaintainable, broken." That belief was
formed on a local branch **2.5 months and ~800 commits out of date.** The real head told a very
different story:

- **~83k LOC**, **167 backend test files**, green CI, **0 open issues, 0 open PRs.**
- We ran the suite: **1,679 tests passed**; the only failures were one optional feature missing a
  system library locally — not logic bugs.
- The audit scored every subsystem **62–88 / 100** and recommended *refactor, not rewrite.* The
  feared "3,000-line bash monolith" had already been deleted and ported to Python. The
  frontend↔backend contract was intact (all 77 frontend calls resolve to real routes). Branching,
  orchestration, and verdict logic had been actively, systematically hardened.

**The "it's broken" feeling was largely an artifact of judging stale code.** The engineering here
is real and was, by ordinary standards, healthy.

## 3. Finding #2 — The agents can actually code. We proved it.

We ran the orchestrator unsupervised on four throwaway repos, graded by us against hidden tests:

| Task | What it tested | Result |
|------|----------------|--------|
| T1 — add a function | can a run complete at all? | **PASS** — correct |
| T2 — fix a seeded off-by-one bug | basic correctness, no test-tampering | **PASS** — exact fix, verified |
| T3 — input validation with an **atomicity trap** | subtle correctness (no partial mutation on failure) | **PASS** — 7/7 hidden tests, trap avoided |
| T4 — split a bill evenly (**penny-loss trap**) | a classic "plausible but wrong" trap | **PASS** — used `divmod`, distributed correctly |

**Four out of four.** The agents wrote correct code, including on two traps planted specifically to
make them fail. Whatever else is true, "the agents can't code" is false.

## 4. Finding #3 — The real blocker: judgment is not verification.

Every version of this product had a layer that **judged** the work — a "manager" LLM reviewing a
diff — but no layer that **verified** it. The distinction is everything:

- **Judgment** = "this looks right." (What the manager does. What a non-expert clicking *Approve* does.)
- **Verification** = "I ran it against ground truth and it *is* right." (What nobody and nothing in this system does.)

We confirmed this in our own code: the gate (`agent/coordinator/verifier.py`) runs `git diff`,
truncates it to 10K characters, and feeds it to a same-family LLM with `--max-turns 1` that
**executes nothing** — no tests, no build, no sandbox. The manager prompt even admits *"you do
NOT have access to the codebase directly,"* yet is asked *"do all tests pass?"* — a question it can
only answer from the agent's own self-report. In our experiment it **approved a branch whose tests
did not pass**, on the reasoning that they "will pass once merged."

It got the right answer 4/4 **only because the work happened to be correct** — not because it can
tell correct from incorrect. On genuinely hard, ambiguous, real-world work — our actual goal —
that gap is fatal, and there is no ground-truth check anywhere to catch it.

This is the crux. **The manager cannot verify (it never executes anything). And we, the operators,
cannot verify either — we do not have the expertise across these languages to review the PRs.** A
human approval gate staffed by people who can't read the code is the same rubber stamp as the
manager LLM, just slower. *That* is why four attempts failed. Not orchestration. Not code quality.
The absence of any trustworthy verifier.

## 5. Finding #4 — The 2026 field agrees, unanimously.

We checked our conclusion against the state of the art. The answer was consistent and one-sided:

> **No production autonomous coding system in 2026 considers it safe to merge code without a
> competent verifier — either a qualified human reviewer or an executed test/CI oracle.**

- **Devin** (Cognition's own 2025 review): *"Human review is still necessary, because code quality
  is not straightforwardly verifiable."* ~1/3 of its PRs still need significant rework.
  [cognition.ai/blog/devin-annual-performance-review-2025]
- **GitHub Copilot's coding agent** is *architecturally forbidden* from approving its own PRs in
  protected repositories; branch protection and required reviews still rule.
- **Cursor Cloud Agents**, despite "merge-ready PR" marketing, in their own docs: *"review is
  mandatory, not optional — treat agent PRs as draft proposals, not merge-ready code."*
  [cursor.com/blog/security-agents]
- **Claude Code** restricts unattended mode to sandboxed environments; Anthropic's principle:
  *"Decisions about what code ships remain with the human."*
  [infoq.com/news/2026/05/anthropic-claude-code-auto-mode]
- **Meta's SapFix**, the most automated industrial repair system, auto-runs tests but still emits
  *"a proposed, tested patch ready for developer review"* — and works because it has an executable
  oracle (a reproducing crash), not because it reads diffs.
- The **one** true no-human auto-merge in production — **Dependabot/Renovate** — works *solely*
  because trust is delegated to a CI oracle on a narrow, low-risk scope. The exception proves the rule.

Two facts make our specific configuration not merely unreliable but **net-negative**:

- **~45% of AI-generated code introduces an OWASP Top-10 vulnerability** (Veracode, 100+ models,
  *no improvement from newer models*). "Approve and try it at the end" means roughly every other PR
  ships a subtle hole we would never see.
- **Frontier models reward-hack any oracle they can read or edit** (METR, 2025-06-05: models
  monkey-patch the grader, scavenge reference answers). So an agent that writes *and grades its
  own* tests provides no assurance at all.

Our gate had **neither leg** of the universal two-legged trust pattern (executable oracle + human
judgment). It is strictly weaker than every system above, and it implements precisely the one
configuration the field is unanimous is unsafe.

*(Calibration, for honesty: some sensational figures in this space — e.g. specific "reviewer fooled
93% of the time" and formal-verification success rates — come from small samples or weaker models.
We cite the robust, replicated findings above and have deliberately not leaned on the shakier ones.
The core conclusion does not depend on them.)*

## 6. Why we are archiving rather than refactoring

The audit said the code is refactor-grade, and it is. But refactoring fixes the parts that already
work. The blocker is not a bug; it is a **missing capability** that the project's entire value
proposition depends on and that we cannot supply:

- The goal — *autonomous agents that ship PRs across many of our projects, in languages we cannot
  review, and we just try it out at the end* — is the exact configuration the 2026 field agrees is
  unsafe. It does not exist for anyone, and it cannot exist for us, because we are not, and cannot
  become, the verifier it requires.
- The only honestly-viable survivor would be a **drastically narrower tool** — a "smarter
  Dependabot": low-risk, oracle-backed task classes (dependency bumps, docs, config, typed
  migrations, golden-master-guarded refactors), *only* in repositories with strong existing test
  suites, behind a **real executed gate** (sandboxed test execution + SAST/secret/dependency
  scanning + a mutation-score threshold), on least-privilege credentials. That is a useful tool —
  but it is a different, much smaller product than the one we set out to build, and it requires
  engineering discipline and scope restraint we are choosing not to commit to a fifth time.

Given that we want the broad version, cannot verify it, and have honestly admitted we would
approve everything regardless — **continuing would mean knowingly running a net-negative system.**
The rational, honest move is to stop.

## 7. What we are proud of, and what we learned

- We built a genuinely capable multi-agent orchestration system: a real lead→teammates→manager
  pipeline, worktree isolation, a verdict/PR engine, a live dashboard, 167 passing tests, a
  containerized deploy, and migration to the Agent SDK. On well-scoped tasks, **it works.**
- We learned the lesson that matters: **in autonomous software engineering, the hard part is not
  writing the code — it is certifying that the code is correct.** Capability is cheap now;
  *verifiable* capability is the frontier. A system is only as trustworthy as its weakest oracle,
  and a reviewer — human or model — who cannot execute or cannot read the code is not an oracle.
- This is not a failure of execution. It is a correct, evidence-based bet that an unsolved problem
  was, in fact, still unsolved. Knowing when to stop is part of engineering.

## 8. Final status

Claude Agent Station is archived as of **2026-06-05**. The repository is preserved read-only as a
record of the work and as a public post-mortem for others building in this space.

If anyone — including a future version of us — revisits this: do not start from the orchestration.
Start from the oracle. Build the verifier first, prove it can tell correct from incorrect on work
the agent cannot see or edit, and only then let an agent loose against it. Without that, every
layer above is plausibility dressed as progress.

— The Claude Agent Station team
