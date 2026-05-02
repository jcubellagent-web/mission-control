# Gemini Agent Ecosystem Wiring

Gemini is available to the agent ecosystem through the local Gemini CLI and CodexBar visibility. Treat it as a specialist model layer, not as a replacement for the agent owner.

## Routing

- JOSHeX owns Gemini brokering for `gemini-review`, `gemini-long-context`, `gemini-research`, and `model-fallback`.
- Josh 2.0 owns host health checks such as `gemini-health-check` when running on the Mission Control host.
- JAIMES can use Gemini only as an evaluator for dashboard-safe reports, ML notes, and specialist summaries.
- J.A.I.N can use Gemini for scheduled summaries and stale-task review.

## Default Operating Policy

Use Gemini first for dashboard-safe synthesis work when it can reduce Codex subscription pressure without losing execution control.

Send to Gemini by default:

- second-opinion reviews of plans, diffs, docs, runbooks, and architecture;
- long-context summarization of selected, non-sensitive files or sanitized excerpts;
- report drafting, handoff drafting, checklist generation, and stale-task compression;
- JAIMES evaluator passes on non-sensitive Hermes/report/model notes;
- J.A.I.N scheduled digests, monitoring summaries, and watchlist synthesis.

Keep in Codex by default:

- repo edits, terminal execution, tests, deployment, and final verification;
- Gmail, calendar, account, OAuth, token, or raw private connector work;
- destructive operations, account mutation, posting side effects, and approvals;
- final decisions that affect agent ownership, Mission Control data, or external systems.

Codex may ask Gemini for critique or synthesis, but Codex owns the final action and must verify results before applying them.

## Guardrails

- Do not send secrets, OAuth payloads, tokens, raw emails, private account contents, or private customer/account data to Gemini automatically.
- Use sanitized briefs, selected files, or summaries by default.
- Any Gemini task that needs raw private context requires JOSHeX approval and should not be written into Mission Control sidecars.
- Store only status, role, model, route, and test metadata in dashboard data.

## Local Commands

Check CLI installation and update the Gemini sidecar:

```sh
python3 scripts/gemini_agent.py status --write-status
```

Run a dashboard-safe smoke prompt and update the sidecar:

```sh
python3 scripts/gemini_agent.py smoke --model gemini-2.5-flash --write-status
```

Route a Gemini review task:

```sh
python3 scripts/agent_route.py --task-type gemini-review --title "Gemini review" --objective "Review a sanitized plan" --capability gemini-review --privacy dashboard-safe
```

## Operator Notes

JAIMES should not treat Gemini output as Hermes live-agent work or direct approval to mutate accounts. J.A.I.N should use Gemini for summaries and monitoring synthesis only, not posting side effects or raw private data processing.

## Handoff Text For Agents

Use Gemini as a dashboard-safe specialist model layer. If a task is review, summary, long-context synthesis, report drafting, checklist generation, stale-task compression, or non-sensitive evaluator work, prefer the Gemini route before spending more Codex budget. Do not send Gemini raw private connector data, secrets, OAuth payloads, raw emails, private account contents, or customer/account data. Codex/JOSHeX remains the owner for repo edits, execution, approvals, sensitive connectors, and final decisions.
