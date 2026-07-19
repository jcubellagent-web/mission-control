---
name: linear-work-tracking
description: Use when durable agent-ecosystem work should be created, found, updated, handed off, blocked, verified, or completed in the shared Linear workspace.
---

# Linear Work Tracking

Use this skill for durable planning and issue history across JOSHeX, Josh 2.0, JAIMES, and J.A.I.N. Control Tower remains authoritative for live execution, heartbeats, queues, current jobs, runtime health, and edit-lease ownership.

## Read First

1. Read `config/linear-integration.json` from the canonical mission-control checkout.
2. Confirm the connected Linear workspace and team match the non-secret identifiers in that file.
3. Search before creating. Use the Control Tower `workId` first, then proposal ID, then the durable objective.
4. Read the existing issue before updating it.

## Issue Gate

Create or reuse an issue only for:

- a user-approved enhancement;
- a confirmed bug or regression;
- an approved ecosystem proposal;
- work expected to span sessions or agents; or
- an unresolved follow-up or human blocker that needs a durable owner.

Do not create issues for instant answers, short checks, routine health checks, heartbeats, normal cron success, individual Telegram replies, live-card edits, transient telemetry, self-healed alerts, or same-session microtasks unless they reveal a durable defect.

## Opt-In Task Path

For a qualifying Control Tower task, opt in explicitly rather than projecting the whole queue:

```bash
python3 scripts/agent_task.py create \
  --owner <agent> --title "<dashboard-safe title>" --objective "<dashboard-safe objective>" \
  --durable --area "<one configured Area>" \
  --acceptance-criterion "<dashboard-safe criterion>"
```

`agent_delegate.py` accepts the same `--durable`, `--area`, and repeatable `--acceptance-criterion` arguments. The task lifecycle writes a sanitized idempotent intent to `data/linear-work-intents.json`; heartbeats never write intents, and a newer boundary supersedes older pending state for the same `workId`.

For a verified existing task that predates the bridge, use `agent_task.py track --id <task> --agent <agent> --area <Area> --acceptance-criterion <criterion>` once. Do not backfill raw queue history until latest state is reconciled.

After a durable task command returns `linear.syncState: pending`, the connected Codex lane must:

1. from a noncanonical host, run `python3 scripts/linear_work_intent.py flush-local` so any fail-open transport spool is replayed, then run `python3 scripts/linear_work_intent.py pending --route-to <agent>` (the latter proxies to the Josh 2.0 canonical outbox);
2. atomically claim it with `linear_work_intent.py claim --intent-id <intent> --consumer <agent>` and retain the short-lived claim token;
3. search Linear using the intent's stable `workId`, then create or update one issue with the supplied state, Agent label, Area label, priority, objective, and acceptance criteria;
4. run `linear_work_intent.py ack --intent-id <intent> --claim-token <claim> --issue-id <JCU-n> --verified-work-id <workId>`;
5. on connector failure, run `linear_work_intent.py fail` with the same intent, claim token, verified work ID, and `--error-code connector_unavailable`, then retry later.

Never infer durability for tasks created without `--durable`. Reconcile latest terminal task/proposal state before any historical backfill.

## Create And Update

- Use project `Agent Ecosystem` and team `Jcubellagent`.
- Assign the shared workspace owner and select exactly one child label from `Agent` plus one child label from `Area`. Linear permits only one label from each group.
- Include the dashboard-safe objective, acceptance criteria, priority, approval state, Control Tower `workId` or proposal ID, and safe artifact links.
- Reuse the same issue across handoffs. Change the Agent label when ownership changes; do not create a second issue.
- Update at accepted, started, blocked, verifying, completed, or cancelled boundaries only. Do not post heartbeat comments.
- Map accepted/planned/routed to `Todo`, active to `In Progress`, verifying to `In Review`, done to `Done`, and cancelled to `Canceled`.
- A durable blocker stays `In Progress` with a concise blocker note. Waiting for the shared edit lease is coordination state and does not grant or bypass that lease.
- Mark `Done` only after the underlying work has verification evidence and Control Tower has a terminal receipt.

## Connector Routing

JOSHeX, Josh 2.0, and JAIMES use their connected Linear Codex tools. Standalone J.A.I.N, Hermes, or OpenCLAW processes without a Linear tool delegate the sanitized issue operation to the JAIMES or Josh 2.0 Codex lane. Connector failure is fail-open for execution: preserve the stable work reference in Control Tower and retry the same issue operation later.

Create J.A.I.N durable work through `agent_delegate.py`; every meaningful lifecycle boundary refreshes one stable, non-durable JAIMES connector task for the source `workId`, so the delegated operation cannot remain silent or point at a superseded intent. The outbox is canonical on Josh 2.0 and rejects stale generation/revision writes. If canonical transport is temporarily unavailable, `flush-local` replays only the newest retryable boundary without blocking the underlying task.

Do not export or reuse the Codex connector OAuth token. Add separate headless authentication only for a concrete unattended workflow and keep it in the approved 1Password/Keychain path.

## Privacy

Never place raw prompts, raw Telegram messages, raw emails, private account content, OAuth payloads, tokens, cookies, passwords, credentials, wallet secrets, or private customer data in Linear. Summarize in dashboard-safe language.

#JAIMES: this workflow deliberately records durable boundaries only so Linear improves coordination without duplicating Control Tower telemetry.
