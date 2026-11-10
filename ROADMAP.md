# AutoForge Roadmap

Phases are built **one at a time, in order**. Each slice must work end-to-end
before the next begins. Anything not in the current slice belongs here, not in
the code.

## Current: Phase 1 — Coding Agent (ASPICE SWE.3), production-grade
Labelled GitHub issue → LangGraph coder + reviewer agents (OpenAI) → verified
GitHub pull request → PR link commented back on the issue (human merges).
Deployed as a service. See `CLAUDE.md` for exact scope. Increments:
1. Core pipeline: issue number (CLI) → coder → GitHub PR → issue comment.
2. LangGraph orchestration + reviewer/critic loop + checkpoints / HITL interrupt.
3. Jira webhook service, idempotency, Docker deployment, CI.
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