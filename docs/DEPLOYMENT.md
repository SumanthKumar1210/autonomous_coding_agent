# Deploying the AutoForge webhook service

The service listens for GitHub webhooks: labelling an issue `autoforge`
starts a run, and an `/autoforge approve` or `/autoforge reject` comment
decides a run that is waiting for approval. It runs as one container. The job
queue and the LangGraph checkpoints live in SQLite files on a volume, so
queued jobs and paused runs survive restarts.

```
GitHub ──webhook──► tunnel (smee.io / ngrok) ──► localhost:8080/webhook ──► job queue ──► worker ──► PR
```

## 1. Configure `.env`

Copy `.env.example` to `.env` and fill in `OPENAI_API_KEY`, `OPENAI_MODEL`,
`GITHUB_TOKEN`, `GITHUB_REPO` and `GITHUB_WEBHOOK_SECRET`. Generate the secret
with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

The GitHub token must be fine-grained and limited to the target repo, with
read/write on **Contents**, **Pull requests** and **Issues**, and read on
**Metadata**. The service uses Metadata to check that whoever labels an issue
or approves a run has write access to the repo. Labels and comments from
anyone else are ignored.

## 2. Start the service

```bash
docker compose up -d --build
curl http://127.0.0.1:8080/healthz        # {"status":"ok"}
docker compose logs -f autoforge          # JSON logs, one line per event
```

Without Docker: `pip install -e .` and then `autoforge serve --port 8080`.

The container runs as a non-root user with a read-only filesystem. The port is
bound to `127.0.0.1` only.

## 3. Expose it to GitHub through a tunnel

GitHub has to reach the service. For a local deployment, use one of these:

- **smee.io** (no account needed): open https://smee.io/new, copy the channel
  URL, then run
  `npx smee-client --url https://smee.io/<channel> --target http://127.0.0.1:8080/webhook`.
  smee forwards GitHub's headers unchanged, so signature checking keeps working.
- **ngrok**: `ngrok http 8080`, then use `https://<id>.ngrok-free.app/webhook`.

## 4. Add the webhook on the target repository

In the target repo (for example `bms_foundation`), go to **Settings →
Webhooks → Add webhook** and set:

| Field | Value |
|---|---|
| Payload URL | the smee channel URL, or `https://<ngrok-host>/webhook` |
| Content type | `application/json` |
| Secret | the same value as `GITHUB_WEBHOOK_SECRET` |
| Events | "Let me select individual events": **Issues** and **Issue comments** |

GitHub sends a `ping` straight away. Under **Recent Deliveries** it should
show `200` with `{"status":"pong"}`.

## 5. Use it

- **Run:** add the `autoforge` label to an open issue. The service replies
  `202 queued`, and the issue moves `autoforge:in-progress` →
  `autoforge:pr-opened`, or to `autoforge:failed` with a comment explaining why.
- **Approve or reject** (only when `REQUIRE_APPROVAL=true`): comment
  `/autoforge approve` or `/autoforge reject` on the issue.
- **Re-trigger:** remove the label and add it again. While an AutoForge PR is
  open, or a run is waiting for approval, no second change is made.

A delivery GitHub sends twice (same `X-GitHub-Delivery`) is processed once.
Use **Redeliver** in GitHub's UI to safely retry a delivery that failed.

## Operations

| Topic | Notes |
|---|---|
| State | Docker volume `autoforge-data`: `/data/jobs.sqlite` (queue and run history) and `/data/checkpoints.sqlite` (paused runs). Back up both together. |
| Restarts | A job interrupted mid-run is queued again on startup. Idempotency (one open PR per issue) prevents duplicates. |
| Throughput | One worker runs one job at a time, so jobs are serialised per container. Run a single replica: SQLite is not shared between hosts. |
| Logs | JSON on stdout. Filter by `run_id` to follow one run from the webhook to the PR. |
| Secrets | Only from the environment (`.env` locally; a secret store in the cloud). Never baked into the image. |

Deploying to a cloud platform reuses the same image. The platform needs a
persistent volume mounted at `/data`, or a Postgres checkpointer and queue
(see `ROADMAP.md`).
