---
name: multi-model-routing
description: Use before substantial work in the Codex app, Josh 2.0, or JAIMES when choosing between GPT/Codex, Antigravity Gemini, Ollama GLM 5.2, local Ollama, or Grok, especially when provider usage is imbalanced or Codex allowance is constrained.
---

# Multi-Model Routing

Use the best healthy provider for the task while keeping the receiving agent as
owner. The current Codex conversation coordinates, checks privacy, dispatches a
fresh specialist lane when useful, integrates the returned result, and owns the
final answer.

## Preflight

1. Classify privacy before routing. Cloud specialists receive only
   dashboard-safe or explicitly sanitized context.
2. Read the current Codex allowance from `data/modelUsage.json`. Weekly remaining
   at or below 20% means `conserve`; zero means `exhausted`.
3. Verify the selected provider and exact model. Never infer success from a
   configured catalog entry and never describe a requested route as an executed
   route.

## Selection

- Antigravity `gemini-3.6-flash-medium` (or `-high` for review): summaries, digests, compression, broad
  synthesis, routine document review, and low-risk second passes.
- Antigravity `gemini-3.1-pro-high`: nuanced multi-document judgment and deep
  dashboard-safe review when Flash is insufficient.
- Ollama Cloud `glm-5.2:cloud`: large-context technical analysis, architecture
  analysis, multi-file planning, structured code review, and parallel technical
  reasoning. GLM plans and reviews; Codex retains execution and verification.
- Grok `grok-4.5`: X-native research, current events, public social signals, and
  outside critique that depends on fresh public context.
- Local Qwen/Llama: bounded private/offline drafts that do not require frontier
  reasoning.
- GPT/Codex: private context, repo edits, terminal/tool execution, permissions,
  authenticated connectors, approvals, high-stakes integration, and the final
  verified change.

Do not call an outside model for trivial conversation or when its output would
not materially improve the answer. In conservation mode, send eligible bulk
reasoning out first and reserve GPT for orchestration, execution, and synthesis.

## Codex-App Dispatch

For a dashboard-safe specialist pass from the Codex app, invoke the canonical
fresh-lane launcher on Josh 2.0; it forwards authenticated specialist execution
to JAIMES:

```bash
ssh josh2 'cd ~/.openclaw/workspace/mission-control && python3 scripts/model_lane.py --task-type <type> --title <safe-title> --objective <safe-objective> --prompt <sanitized-prompt> --privacy dashboard-safe --requester joshex --execute'
```

Use explicit `--requested-provider` and `--requested-model` only when Josh asks
for one or when the automatic task classification is ambiguous. Capture the
returned output and integrate it; do not hand the conversation to the worker.

## Guardrails

- Never send secrets, credentials, OAuth payloads, cookies, raw emails, raw
  connector/account contents, wallet data, or customer data to Gemini, GLM
  Cloud, or Grok.
- Refuse silent provider fallback. If authentication or verification fails,
  disclose the failure and use the policy-safe fallback.
- Preserve Josh 2.0 or JAIMES ownership when they initiated the task. JOSHeX
  remains owner for Codex-app coordination.
- Publish only dashboard-safe model, route, latency, and outcome facts. Raw
  prompts and outputs stay out of shared telemetry.

#JAIMES: specialist credits are consumed by verified task-matched passes, while
# GPT remains the trusted executor and integrator instead of the default bulk-reasoning lane.
