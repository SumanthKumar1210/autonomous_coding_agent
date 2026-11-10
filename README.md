# AutoForge

**An agentic AI platform for automating the automotive software V-model,
grounded in Automotive SPICE (ASPICE).**

Automotive embedded software is built under strict process frameworks.
ASPICE requires every step, from requirements through design, code and test,
to be carried out, reviewed and traceable. AutoForge automates these
engineering activities with LLM-based agents, one V-model stage at a time. It
connects to the tools teams already use: ticketing, GitHub, requirements
management and test benches. A human engineer stays in control at every
decision point.

It starts with **Software Construction (ASPICE SWE.3)**: a coding agent that
turns a ticket into a reviewed pull request.

```
  Requirements ──────────────────────────────────────────▶ System verification
   (Phase 3)  ╲                                         ╱   (Phase 4, SYS.4)
               ╲  Architecture / design          Integration & HIL/SIL ╱
                ╲                                  (Phase 4)          ╱
                 ╲  Detailed design + code  ◀──▶  Unit verification  ╱
                  ╲  (Phase 1, SWE.3)              (Phase 2, SWE.4) ╱
                   ╲──────────────────────────────────────────────╱
```

## Roadmap

| Phase | V-model stage (ASPICE) | What AutoForge automates | Status |
|---|---|---|---|
| **1 · Coding Agent** | Software construction (SWE.3) | Ticket → multi-agent code generation (coder + reviewer) → pre-PR verification → pull request → status reported back on the ticket. Python and C. | 🟡 In progress |
| **2 · Unit Testing & Static Analysis** | Unit verification (SWE.4) | Automated unit-test generation for produced code. Static analysis and coding-standard enforcement, including **MISRA C** compliance checks. | ⚪ Planned |
| **3 · Requirements Engineering** | Requirements analysis (SYS.2 / SWE.1) | Requirements authoring and quality checks. **Polarion** integration. End-to-end traceability from requirement to design, code and test. | ⚪ Planned |
| **4 · HIL/SIL Test Automation** | Integration & system verification (SWE.5–6, SYS.4) | Hardware-in-the-Loop and Software-in-the-Loop test generation and execution, moving toward system-level verification. | ⚪ Planned |

### Phase 1 in detail

| Increment | Scope | Status |
|---|---|---|
| 1. Core pipeline | GitHub issue → OpenAI coder agent (structured outputs) → validated change → branch + commit → PR → issue comment/labels | ✅ Built, 51 automated tests |
| 2. Multi-agent orchestration | **LangGraph** coder ↔ reviewer/critic loop, persistent checkpoints, human-in-the-loop interrupt | ⚪ Next |
| 3. Service & delivery | Webhook-driven service (HMAC-verified), idempotent runs, Docker, CI | ⚪ Planned |
| 4. Quality | Repo-context retrieval, C code generation, sandboxed pre-PR verification (lint/tests), evaluation harness measuring reviewer lift | ⚪ Planned |
| Later | Jira as a second ticket source (pluggable `TicketSource` port) | ⚪ Planned |

## Architecture

```mermaid
flowchart LR
    %% ---------- Inputs ----------
    subgraph IN["Work intake"]
        direction TB
        GHI["GitHub Issues"]
        JIRA["Jira"]
        POL["Polarion<br/>requirements"]
    end

    %% ---------- Core ----------
    subgraph CORE["AutoForge core"]
        direction TB
        ENTRY["Entry points<br/>CLI · Webhook service"]
        ORCH["Workflow orchestrator<br/>LangGraph · checkpoints · HITL interrupt"]

        subgraph AGENTS["Agents"]
            direction TB
            CTX["Repo context<br/>retrieval"]
            CODER["Coder agent<br/>SWE.3"]
            REVIEW["Reviewer / critic<br/>agent"]
            TESTGEN["Unit-test generator<br/>SWE.4"]
            REQ["Requirements agent<br/>SYS.2 / SWE.1"]
            HIL["HIL/SIL test agent<br/>SWE.5-6 · SYS.4"]
        end

        POLICY["Policy and safety layer<br/>path rules · PR-only · injection guard"]
        VERIFY["Verification sandbox<br/>lint · tests · MISRA C"]
        OBS["Observability<br/>run IDs · JSON logs · token cost"]
    end

    %% ---------- External ----------
    LLM[("OpenAI<br/>structured outputs")]

    subgraph OUT["Delivery and review"]
        direction TB
        PR["GitHub pull request"]
        HUMAN(["Engineer reviews and merges"])
        BENCH["HIL/SIL test benches"]
    end

    GHI --> ENTRY
    JIRA -.-> ENTRY
    POL -.-> REQ
    ENTRY --> ORCH
    ORCH --> CTX --> CODER
    CODER <-.->|feedback loop| REVIEW
    CODER --> POLICY
    REVIEW -.-> POLICY
    TESTGEN -.-> VERIFY
    POLICY --> PR
    POLICY -.-> VERIFY -.-> PR
    HIL -.-> BENCH
    CODER <--> LLM
    REVIEW -.-> LLM
    PR --> HUMAN
    PR -->|status + PR link| GHI
    ORCH --- OBS

    classDef built fill:#d4f7dc,stroke:#1a7f37,color:#0b3d1a
    classDef next fill:#fff4cc,stroke:#bf8700,color:#4d3800
    classDef planned fill:#f0f0f0,stroke:#8c959f,color:#57606a,stroke-dasharray:4 3
    class GHI,ENTRY,CTX,CODER,POLICY,OBS,PR,HUMAN,LLM built
    class ORCH,REVIEW next
    class JIRA,POL,TESTGEN,REQ,HIL,VERIFY,BENCH planned
```

<sub>🟩 built (Phase 1.1) · 🟨 next (Phase 1.2) · ⬜ planned (Phase 1.3+ and Phases 2–4) · solid
arrows = working today, dashed = planned. In Phase 1.1 the orchestrator is a
linear pipeline; LangGraph replaces it in 1.2.</sub>

Layers, ports and the per-run sequence diagram are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Design principles

- **Human-in-the-loop by construction.** Agents only open pull requests. The
  code has no way to merge or approve, and writes are allowed only on
  agent-owned branches. Tests enforce both.
- **Process-grounded.** Each phase maps to an ASPICE process, and every run is
  traceable from ticket to agent run to commit to PR through a shared run ID.
- **Secure with untrusted input.** Ticket and repo content are treated as data,
  never instructions, to guard against prompt injection. A policy layer rejects
  unsafe paths and any change to CI/CD or `.git/`, whatever the model outputs.
- **Pluggable integrations.** A hexagonal (ports and adapters) architecture
  keeps agents and workflow independent of GitHub, Jira, Polarion or the LLM
  provider. Layer rules are enforced by tests.
- **Deterministic tool execution.** The LLM only produces a structured change.
  Plain code performs every GitHub operation after policy checks. There is no
  autonomous tool calling, so the model cannot invoke merge or delete.
- **Production-grade.** Typed Python, structured JSON logging, retries that
  never create a PR or comment twice, idempotent ticket runs, per-run token and
  cost tracking, and fully offline tests.

## Tech stack

Python 3.11 · OpenAI Responses API (structured outputs) · LangGraph (Phase 1.2)
· GitHub REST API · pydantic / pydantic-settings · httpx · pytest · Docker and
CI (Phase 1.3)

## How it works today (Phase 1, increment 1)

`autoforge run <issue>` does the following:

1. Reads the GitHub issue (title, body, comments, labels) and labels it
   `autoforge:in-progress`.
2. Snapshots the repo's default branch: the file tree plus convention and
   source files, up to a size budget.
3. Asks the OpenAI coder agent for a structured change (files, commit message,
   PR title and body).
4. Validates the change against the policy layer: paths must be safe, nothing
   may be written under `.git/` or `.github/workflows/`, and a file can only be
   modified if the agent saw its current content.
5. Commits to `autoforge/issue-<N>` and opens a PR against the default branch
   (`Closes #N`).
6. Comments the PR link on the issue and labels it `autoforge:pr-opened`. If
   the run fails, it labels the issue `autoforge:failed` and comments the error.

Re-running a ticket that already has an open AutoForge PR does nothing, so no
duplicate PR is opened. Every run has a run ID, which appears in the JSON logs,
the PR and the issue comment.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env          # then fill in the values
```

The GitHub token must be a fine-grained token limited to the target repo, with
read/write on Contents, Pull requests and Issues, plus read on Metadata. The
repo needs at least one commit on its default branch.

## Usage

```bash
autoforge run 12              # or: python -m autoforge run 12
```

The command prints a JSON result line (`run_id`, `outcome`, `pr_url`, `error`)
on stdout and writes structured logs to stderr. Exit codes: 0 means a PR was
opened or one already exists, 1 means the run failed, 2 means a configuration
error.

## Tests

```bash
pytest
```

The tests run against an in-memory fake GitHub API and a fake coder. They make
no network calls. They also enforce the architecture's layer rules and the
PR-only safety guarantees.
