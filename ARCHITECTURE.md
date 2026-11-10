# AutoForge Architecture

This document describes the target architecture for Phase 1 (Coding Agent,
ASPICE SWE.3) and shows which parts already exist. Scope is defined in
`CLAUDE.md`, sequencing in `ROADMAP.md`.

## 1. System overview

![AutoForge system overview](docs/system-overview.svg)

How a ticket becomes a pull request:

1. **Engineer** writes a GitHub issue and adds the `autoforge` label.
2. **Understand:** AutoForge reads the ticket and the relevant repository code.
3. **Write code:** the coder agent sends a prompt to the OpenAI LLM and gets
   back a structured code change (JSON).
4. **Review** *(next increment)*: a reviewer agent checks the change against the
   ticket and sends fixes back to the coder.
5. **Safety checks:** policy rules block unsafe or out-of-scope edits (path
   traversal, CI/CD files, files the agent never saw).
6. **Verify** *(planned)*: lint and tests run in a sandbox before anything is
   published.
7. **Pull request:** AutoForge creates a branch and commit, opens a PR
   (`Closes #N`), and comments the link and status back on the ticket.
8. **Engineer** reviews and merges. AutoForge can never merge, so a human
   always makes the final call.

GitHub is where requests come in and results go out. AutoForge has no UI. The
roadmap strip shows how the same platform extends across the ASPICE V-model in
Phases 2–4.

## 2. System design (component view)

![AutoForge system design](docs/system-design.svg)

Every block in the Phase 1 system appears here. Solid arrows are runtime calls
or data flow. Dashed arrows are planned flows. Dotted arrows with hollow heads
mean "implements this port". Colour shows status: green is built, yellow is
next (1.2, LangGraph + reviewer), grey is planned, blue is external, purple is
the human actor. Today `pipeline.py` is the orchestrator. `graph.py` replaces
it behind the same `run_ticket()` entry point.

### Block reference

| Block | Layer | Responsibility | Status |
|---|---|---|---|
| **Engineer** | actor | Writes the ticket, labels it `autoforge`, reviews the PR and is the only one who merges | — |
| **GitHub REST API** | external | Issues, Git Data (trees, commits, refs) and Pulls endpoints | — |
| **GitHub Webhooks** | external | Pushes `issues.labeled` events to the service | — |
| **OpenAI Responses API** | external | LLM inference with schema-constrained (structured) output | — |
| **Jira Cloud API** | external | Alternative ticket source once access exists | — |
| **Container runtime** | external | Isolated workspace where repo checks run | — |
| `app/cli.py` | app | Developer entry point: run one issue, print a JSON result, exit code 0/1/2 | ✅ |
| `app/webhook.py` | app | HTTP endpoint. Verifies the HMAC signature and checks the labeller has write access before queueing | ⬜ 1.3 |
| Run queue / worker | app | Decouples webhook response from the long agent run. Dedupes redelivered events | ⬜ 1.3 |
| `app/config.py` | app | Typed settings from env / `.env`. Secrets held as `SecretStr` | ✅ |
| `app/wiring.py` | app | Composition root. The only place concrete adapters are created and injected | ✅ |
| `workflow/pipeline.py` | workflow | Linear run: idempotency check → context → code → policy → publish → PR → feedback. Turns every failure into a ticket comment | ✅ |
| `workflow/graph.py` | workflow | LangGraph state machine with a coder ↔ reviewer loop (bounded), verify step and HITL interrupt | 🟨 1.2 |
| Checkpoint store | workflow | Persists graph state so runs can pause for a human and resume | 🟨 1.2 |
| `agents/context.py` | agents | Builds the `RepoSnapshot`: file tree plus selected files within a byte budget | ✅ (retrieval upgrade 1.4) |
| `agents/prompts.py` | agents | Versioned system prompts and input templates. Untrusted content goes inside `<ticket>`/`<repository>` tags | ✅ |
| `agents/coder.py` | agents | `OpenAICoder`: ticket + snapshot → `CodeChange`. Handles truncated or refused output | ✅ |
| `agents/reviewer.py` | agents | Checks a change against acceptance criteria and conventions, returns a verdict and feedback | 🟨 1.2 |
| `agents/languages/` | agents | Per-language guidance and checks (Python, C) behind one small interface | ⬜ 1.4 |
| `TicketSource` | port | `get_ticket`, `comment`, `set_status` | ✅ |
| `RepoHost` | port | `get_base`, `list_files`, `read_file`, `find_open_pull`, `publish`, `open_pull`. No merge | ✅ |
| `Coder` | port | `generate(ticket, snapshot) → CoderResult` | ✅ |
| `Reviewer` | port | `review(ticket, snapshot, change) → ReviewVerdict` | 🟨 1.2 |
| `Verifier` | port | `verify(snapshot, change) → CheckReport` | ⬜ 1.4 |
| `domain/models.py` | domain | Pure data types shared by every layer. `CodeChange` doubles as the LLM output schema | ✅ |
| `domain/policy.py` | domain | Hard constraints in code: path rules, forbidden dirs, modify-only-what-was-seen, agent-branch-only writes | ✅ |
| `domain/errors.py` | domain | Typed, reportable failure classes | ✅ |
| `github/client.py` | integrations | REST transport: auth header, timeouts, retries that never create anything twice. Has no merge or approve method | ✅ |
| `github/issues.py` | integrations | GitHub Issues as `TicketSource`. Status shown as `autoforge:*` labels | ✅ |
| `github/repo.py` | integrations | GitHub repo as `RepoHost`. Atomic multi-file commit via the Git Data API, then opens the PR | ✅ |
| `jira/` | integrations | Jira as `TicketSource`, using workflow transitions instead of labels | ⬜ later |
| `sandbox/` | integrations | `ContainerVerifier`: runs formatter, linter and tests in a container before publishing | ⬜ 1.4 |
| `observability.py` | cross-cutting | JSON-lines logs. A `run_id` ties together the logs, PR footer and issue comment. Token usage recorded per run | ✅ |
| `evals/` | cross-cutting | Fixed sample-ticket suite that scores output quality across prompt or model changes | ⬜ 1.4 |

## 3. Layers and the dependency rule

```mermaid
flowchart TB
    app["app/<br/>config · wiring · cli · (webhook)"]
    workflow["workflow/<br/>pipeline → (LangGraph graph)"]
    agents["agents/<br/>coder · prompts · context · (reviewer)"]
    integrations["integrations/<br/>github/ · (jira/)"]
    ports["ports.py<br/>TicketSource · RepoHost · Coder"]
    domain["domain/<br/>models · policy · errors"]

    app --> workflow
    app --> integrations
    app --> agents
    workflow --> ports
    workflow --> agents
    agents --> ports
    integrations --> ports
    ports --> domain
    workflow --> domain
    agents --> domain
    integrations --> domain
```

Dependencies only point downward, and `domain/` imports nothing else in the
package. `tests/test_architecture.py` enforces these rules, so a violating
import fails CI.

| Layer | Responsibility | May import | Must not import |
|---|---|---|---|
| `domain/` | Pure models (`Ticket`, `CodeChange`, `RepoSnapshot`, …), change policy and safety rules, error types | stdlib, pydantic | everything else |
| `ports.py` | `Protocol` interfaces the core depends on | `domain` | agents, workflow, integrations, app, SDKs |
| `agents/` | LLM roles: prompts, context building, and calls to the OpenAI model | `domain`, `ports`, `openai` | workflow, integrations, app |
| `workflow/` | Orchestrates one ticket run, owns run IDs and idempotency, reports outcomes | `domain`, `ports`, `agents` contracts | integrations, app, `agents.coder`, SDKs |
| `integrations/` | Adapters to external systems that implement the ports | `domain`, `ports`, `httpx` | workflow, app, agents, `openai` |
| `app/` | Composition root: settings, wiring, entry points | everything | — |
| `observability.py` | JSON logging and the run-ID context, used by every layer | stdlib | — |

**Why ports?** The workflow never sees `GitHubClient` or `OpenAI`. Adding Jira
means adding one `TicketSource` implementation. Supporting another git host
means adding one `RepoHost`. Tests run the real workflow against fakes, with no
network access.

## 4. Package layout

```
src/autoforge/
├── domain/
│   ├── models.py          Ticket, TicketStatus, BaseRef, RepoFile, RepoSnapshot, PullRequest,
│   │                      FileChange, CodeChange (LLM schema), TokenUsage, CoderResult
│   ├── policy.py          validate_change, assert_writable_branch   ← hard constraints in code
│   └── errors.py          TicketError, ChangeValidationError, SafetyError, AgentError
├── ports.py               TicketSource, RepoHost, Coder
├── agents/
│   ├── context.py         build_snapshot / select_context_files (repo context within a budget)
│   ├── prompts.py         system prompt + input template (versioned, evaluable)
│   └── coder.py           OpenAICoder → Responses API structured outputs
├── workflow/
│   └── pipeline.py        run_ticket(key, Dependencies, WorkflowConfig) → RunResult
├── integrations/
│   └── github/
│       ├── client.py      REST client: auth, retries, timeouts (no merge capability)
│       ├── issues.py      GitHubIssuesSource  (TicketSource)
│       └── repo.py        GitHubRepoHost      (RepoHost: Git Data API commits, PRs)
├── app/
│   ├── config.py          Settings (pydantic-settings, env / .env)
│   ├── wiring.py          build_dependencies(settings): the only place concrete classes meet
│   └── cli.py             `autoforge run <issue>`
└── observability.py       JSON logs + run_id context
```

## 5. Runtime flow (one ticket run)

```mermaid
sequenceDiagram
    participant E as Entry point (CLI / webhook)
    participant W as workflow.run_ticket
    participant T as TicketSource
    participant R as RepoHost
    participant C as Coder (OpenAI)
    participant P as domain.policy

    E->>W: run_ticket(key)
    W->>R: find_open_pull(autoforge/issue-N)
    alt PR already open
        W-->>E: ALREADY_OPEN (idempotent)
    end
    W->>T: get_ticket(key)
    W->>T: set_status(IN_PROGRESS)
    W->>R: get_base, list_files, read_file (context)
    W->>C: generate(ticket, snapshot)
    C-->>W: CodeChange (structured)
    W->>P: validate_change (paths, actions, forbidden dirs)
    W->>R: publish(branch) → assert_writable_branch → tree, commit, ref
    W->>R: open_pull(head=agent branch, base=default)
    W->>T: comment(PR link), set_status(PR_OPENED)
    W-->>E: PR_OPENED
    Note over W,T: Any exception → comment the error + set_status(FAILED)
```

## 6. Cross-cutting concerns

| Concern | How it's handled |
|---|---|
| **Human in the loop** | `RepoHost` has no merge or approve method. `assert_writable_branch` allows writes only to `autoforge/*` branches. `tests/test_github_client.py` asserts the client has no merge method. |
| **Untrusted input** | Issue text and repo files are wrapped in `<ticket>`/`<repository>` tags and treated as data. The prompt tells the model to flag injected instructions, not follow them. `domain.policy` rejects path traversal and writes under `.git/` or `.github/workflows/` whatever the model says. |
| **Idempotency** | Each ticket gets a fixed branch, `autoforge/issue-<N>`. If a PR is already open for it, the run does nothing. A leftover branch with no open PR is reset. |
| **Reliability** | GitHub calls have timeouts and retry with backoff: reads and updates on 429/5xx, creates only on 429 or when the connection never opened, so nothing is created twice. The OpenAI SDK has its own retries. Truncated or refused model output becomes an `AgentError`. |
| **Observability** | JSON logs on stderr. A `run_id` ties together the logs, the PR footer and the issue comment. Every run logs token usage. |
| **Secrets** | `SecretStr` settings, loaded from env or `.env`, never logged. HTTP client logging is turned down so request URLs and headers don't appear in logs. |
| **Testability** | `FakeGitHub` (an in-memory REST fake on `httpx.MockTransport`) and `FakeCoder` let the real workflow and adapters run in tests with no network. |

## 7. Where Phase 1 increments plug in

| Increment | Change | Location |
|---|---|---|
| 2. LangGraph + reviewer | `workflow/graph.py` replaces the linear pipeline behind `run_ticket`. Adds `agents/reviewer.py`, a `Reviewer` port and a `ReviewVerdict` model. Checkpointer and HITL interrupt live in the graph. | `workflow/`, `agents/`, `ports.py`, `domain/models.py` |
| 3. Webhook service | `app/webhook.py` (HTTP app) verifies the HMAC signature and checks the labeller has write access. It queues the run and reuses `app/wiring.py`. Adds Dockerfile and CI. | `app/`, repo root |
| 4. Context, C, verification, evals | Better `select_context_files`. Python and C prompts and checks behind a small `Language` interface. A `Verifier` port that runs the repo's checks in a sandbox container before publishing. `evals/` holds a sample-ticket suite. | `agents/`, `ports.py`, `integrations/sandbox/`, `evals/` |
| Later: Jira | `integrations/jira/` implementing `TicketSource`. | `integrations/` |

Steps 2–4 of the product roadmap (test generation, MISRA, Polarion, HIL/SIL) are
not part of this architecture yet and get no stubs.
