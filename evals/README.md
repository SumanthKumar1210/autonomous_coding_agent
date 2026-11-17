# AutoForge evaluation suite

A fixed set of sample tickets used to measure AutoForge's output quality, for
example when prompts or models change. Each run reports how well the agents did
and what that cost. It also shows how much each part of the agent loop adds,
most importantly the reviewer.

The harness runs the **production agent graph** (`workflow/graph.py`: context
→ coder → policy → sandbox checks → reviewer) against an in-memory copy of a
fixture repository. Nothing reaches GitHub. The only difference from a real
run is the repository host (`evals/local_repo.py`), which records the change
instead of opening a PR.

## What is in here

```
evals/
├── repos/
│   ├── bms_c/      C11 BMS firmware (4S pack): cell monitor, SOC, contactor, faults; CMake + CTest, HAL stub
│   └── bms_py/     Python BMS log tools: parser, statistics, report; pytest + ruff
└── cases/<id>/
    ├── case.toml   ticket (title, body with acceptance criteria) + expectations
    ├── hidden/     acceptance tests added only when grading; the agents never see them
    └── solution/   reference change, which proves the case is solvable and the tests are right
```

| Case | Repo | Kind | What makes it hard |
|---|---|---|---|
| `c-cell-imbalance` | C | feature across 4 files | New fault code + API + threshold; an **existing** unit test breaks unless updated |
| `c-soc-clamp` | C | bug fix | Clamping the output alone is wrong; the stored charge must saturate (recovery test) |
| `c-pyro-fuse` | C | new module | New header/source, HAL function, HAL stub, CMake registration, unit test; never-retry safety rule |
| `py-parser-robust` | Python | bug fix | Header/comment/blank handling, 1-based line numbers, consistent cell count |
| `py-energy` | Python | feature across 2 modules | Trapezoidal integration with units, report line; existing report test must be updated |

The fixtures are frozen. Change them only together with the cases, and re-run
`validate` afterwards.

## Metrics

All graders are deterministic. No LLM judges another LLM.

| Metric | Meaning |
|---|---|
| **Resolved** (headline) | The final change passes the policy and every check: the repository's own checks (CMake + CTest; pytest + ruff) and the case's hidden acceptance tests, run in the sandbox |
| Scope ok | The change creates/modifies every expected file, touches no protected file (`expect.unchanged`), and matches every `expect.contains` pattern |
| Hidden checks | Share of hidden acceptance checks passed |
| Context recall | Share of `expect.context` files the context retrieval showed the agents |
| Attempts, tokens, time | Cost per run (mean) |
| False accepts | Runs that ended approved (published) with an unresolved change |
| False rejects | Runs the reviewer rejected although the change was resolved |
| Unverified | Runs whose checks could not run (no sandbox); they never count as resolved |

## Variants

| Variant | Loop | Measures |
|---|---|---|
| `single` | One coder attempt. No checks or reviewer in the loop | The coder alone (the baseline) |
| `no-review` | Coder with policy and sandbox-check feedback, up to `MAX_ATTEMPTS` | What verification feedback adds |
| `full` | Production loop: policy, checks and reviewer feedback | **Reviewer lift** = `full` − `no-review` |

Every variant is graded the same way afterwards, including `single`, whose
change is still built and tested.

## Running

| Command | Needs | Cost |
|---|---|---|
| `autoforge eval context [--verbose]` | nothing | free; also runs in CI (`tests/test_evals.py`) |
| `autoforge eval validate` | `SANDBOX_URL` for the build/test part | free (no LLM) |
| `autoforge eval run [--variant V ...] [--case ID ...] [--repeat N]` | `OPENAI_*`, `SANDBOX_URL` (no GitHub) | **spends OpenAI tokens**: about 1-3 calls per case and variant |
| `autoforge eval compare OLD.json NEW.json` | two reports | free |

`run` writes `eval-<time>.json` (one record per case run) and `.md` to `--out`
(default `.autoforge/evals`) and prints the Markdown summary. LLM output
varies between runs, so use `--repeat 3` or more before trusting small
differences.

The sandbox is only reachable from the service container. The suite ships in
the image at `/app/evals`, so run the evals there:

```sh
docker compose build autoforge
docker compose run --rm --no-deps -v "$PWD/.autoforge/evals:/out" autoforge autoforge eval validate
docker compose run --rm --no-deps -v "$PWD/.autoforge/evals:/out" autoforge \
    autoforge eval run --out /out --repeat 3
autoforge eval compare .autoforge/evals/eval-<before>.json .autoforge/evals/eval-<after>.json
```

The report records the coder and reviewer models and a fingerprint of the
prompts (`prompt_fingerprint`), so `compare` shows what changed between two
runs.

## Adding a case

1. Create `cases/<id>/case.toml` (format: `src/autoforge/evals/suite.py`). Write
   the body like a real ticket, with acceptance criteria. Mention names the
   tests rely on (function signatures, stub helpers); don't mention file paths
   the agent should work out for itself.
2. Put the acceptance tests in `cases/<id>/hidden/`, at the paths where they go
   in the repository (by convention under `.autoforge-eval/`), and add
   `[[hidden_checks]]` that run them.
3. Put the reference change in `cases/<id>/solution/`: the full new content of
   every file it creates or modifies.
4. Run `autoforge eval validate`. The hidden checks must **fail** on the
   untouched fixture and **pass** with the solution, and the repository's own
   checks must pass on both.
5. Run `autoforge eval context --verbose`, then `pytest tests/test_evals.py`.
   CI requires full context recall on every case, so a case whose expected
   context the retrieval misses is a retrieval bug to fix (or a wrong
   expectation).
