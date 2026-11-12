# AutoForge Roadmap

Phases are built **one at a time, in order**. Each slice must work end-to-end
before the next begins. Anything not in the current slice belongs here, not in
the code.

## Current: Phase 1 — Coding Agent (ASPICE SWE.3), production-grade
Labelled GitHub issue → LangGraph coder + reviewer agents (OpenAI) → verified
GitHub pull request → PR link commented back on the issue (human merges).
Deployed as a service. See `CLAUDE.md` for exact scope. Increments:
1. ✅ Core pipeline: issue number (CLI) → coder → GitHub PR → issue comment.
2. ✅ LangGraph orchestration + reviewer/critic loop + checkpoints / HITL interrupt.
3. GitHub webhook service, idempotency, Docker deployment, CI.
   - Carry over from 1.2: a ticket re-triggered while a run is paused for
     approval (`autoforge:awaiting-approval`) currently starts a new run; the
     webhook's dedupe must treat a paused run like an open PR.
   - Carry over from 1.2: checkpoints are a local SQLite file; a deployed
     service needs a persistent volume or a Postgres checkpointer.
4. Repo-context retrieval, C support (alongside Python), pre-PR verification
   with existing repo checks, evaluation harness (measure reviewer lift).

## Deferred: Jira integration
No Jira access yet. When available, add Jira Cloud as a second `TicketSource`
(read issue, comment, transition, webhook) alongside GitHub Issues.

## Step 2 — Unit Testing & Static Analysis
- Automated unit-test generation for the produced code.
- Static analysis + coding-standard checks, including MISRA compliance for
  embedded C.

## Step 3 — Requirements Engineering
- Automate requirements engineering activities.
- Integrate Polarion; establish traceability between requirements, design, and
  code (RAG over requirements fits here).

## Step 4 — HIL/SIL Test Automation
- Extend to Hardware-in-the-Loop and Software-in-the-Loop test automation.
- Progress toward system-level verification (ASPICE SYS.4).

---

## Parking lot
Ideas that come up mid-build but belong to a later phase — note them here instead
of implementing them now.