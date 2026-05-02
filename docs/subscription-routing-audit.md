# Codex and Gemini Subscription Routing Audit

Updated: 2026-04-30T22:03:35Z

## Current posture

- Codex is the constrained execution subscription. CodexBar showed Codex Pro at 91% session left, 59% weekly left, 3% in deficit, projected runout in 3d 20h, and weekly reset in 4d 7h.
- Gemini is the high-headroom specialist subscription. CodexBar showed Gemini Paid at Pro 97% left, Flash 98% left, and Flash Lite 99% left.
- JOSHeX, Josh 2.0, and JAIMES can all invoke Gemini through the local Gemini CLI path. JAIMES was verified through `jaimes-via-josh` after OAuth and CLI upgrade.

## Operating rule

Use Gemini before Codex for dashboard-safe synthesis, review, compression, and evaluator passes. Use Codex for execution, trusted context, connectors, repo edits, terminal actions, approvals, and final accountability.

## Autopilot enforcement

`scripts/agent_route.py` now emits an enforced `modelRoute` for every routed task. Dashboard-safe synthesis/review/digest tasks get `modelRoute.firstStop=gemini`; execution, private, approval-gated, connector, repo-edit, destructive, or account-mutating tasks get `modelRoute.firstStop=codex`.

When `agent_route.py --create-task` queues work, it writes the model-route decision into the task note so Josh 2.0, JAIMES, J.A.I.N, and JOSHeX can see whether the task should start with Gemini or Codex.

Direct ad hoc prompts that bypass `agent_route.py` still depend on the agent following the documented policy. To make a task fully autopilot, route it through `agent_route.py` or a wrapper that calls it first.

## Route to Gemini first

- Large read-only sweeps across logs, docs, dashboard data, and sidecars.
- Drafting summaries, handoffs, runbooks, daily and weekly digests.
- Second-pass review of plans, diffs, pull request text, and deployment notes.
- Compression of stale Brain Feed, Today Jobs, adoption, and shared-ledger activity.
- JAIMES evaluator work over sanitized specialist reports and ML notes.
- J.A.I.N scheduled watchlist summaries, provided inputs are dashboard-safe.

## Keep on Codex

- Anything involving secrets, OAuth payloads, raw private email, raw connector data, private account contents, customer/account content, or browser-authenticated sessions.
- File edits, code changes, terminal commands, tests, builds, deployment, and git operations.
- Decisions that require approval, destructive maintenance, account mutation, or final operator accountability.
- Cross-agent delegation where the task packet contains sensitive context.

## Default model shape

- Gemini Flash Lite: cheap/high-volume quick summaries, smoke checks, classification, stale-task compression.
- Gemini Flash: normal long-context synthesis and first-pass report drafting.
- Gemini Pro: hard reasoning, architecture alternatives, post-Codex review, complex evaluator passes.
- Codex high-capability model: implementation, debugging, tool use, repo state, sensitive connectors, and final integration.
- Codex mini/fast model: narrow coding chores, low-risk explanations, local checks, and simple glue tasks when available.

## Guardrails

- Never store raw Gemini prompts or raw Gemini output in Mission Control sidecars.
- Send Gemini sanitized briefs or selected non-sensitive files by default.
- If Gemini needs private context, JOSHeX must explicitly approve the scoped packet first.
- If Codex weekly drops below 40%, Gemini becomes mandatory for all eligible synthesis/review work.
- If Codex weekly drops below 25%, Codex should only do execution, approvals, and final integration.
- If Gemini Flash rate-limits, fall back to Flash Lite for simple work or Pro for hard work; do not automatically burn Codex unless execution is needed.
