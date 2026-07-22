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
   Read Antigravity and Ollama allowance from the same CodexBar projection when
   available. A quota-cookie failure is not an inference failure: require a
   separate verified runtime health result and label exact allowance unknown.
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

## Exact Task Cases

- Gemini Flash Medium: routine dashboard-safe summaries, scheduled digests,
  compression, classification, broad synthesis, ordinary report drafts, and
  low-risk second passes.
- Gemini Flash High: UI/readability review, decision or handoff review, and a
  stronger dashboard-safe second pass where latency still matters.
- Gemini Pro High: nuanced multi-document judgment, deep research synthesis,
  model evaluation, escalation review, and long-context dashboard-safe analysis.
- GLM 5.2 Cloud: architecture, repository analysis, debugging hypotheses,
  multi-file planning, structured code review, long-context technical analysis,
  and parallel technical second opinions. It does not edit or execute.
- Local Ollama: private/offline bounded drafting and extraction only. On the
  current 16 GB hosts, keep Qwen 2.5 7B as the verified general fallback and use
  GLM-OCR only for local OCR after its dedicated canary passes.
- Codex: private data, tools, connectors, repo edits, terminal work, permissions,
  side effects, approval handling, high-stakes integration, and final proof.

## Runtime Resilience

- Automatic Gemini routes use: selected Gemini -> GLM 5.2 Cloud -> Codex Terra.
- Automatic GLM routes use: GLM 5.2 Cloud -> Gemini Pro High -> Codex Terra.
- Check the selected model before work. If execution then fails, announce the
  provider/model switch before trying the next fresh lane. Never silently reuse
  an output under the original model label.
- Explicit model requests remain fail-closed; do not substitute a different
  model merely to satisfy the request.
- Keep provider health and quota separate. Health controls whether a lane may
  execute; quota controls preference and conservation. Unknown quota with
  verified health is usable, while exhausted quota or failed health is not.

## Ollama Catalog Decisions

- Production: `glm-5.2:cloud` for the technical cases above.
- Specialized candidate: `glm-ocr` for local text, table, formula, and figure
  extraction; it is not a general reasoning model.
- Shadow-test on 16 GB hardware before promotion: `qwen3.5:4b` or `:9b` for
  lighter private multimodal work and `ornith:9b` for bounded coding review.
- Cloud shadow-test only: `minimax-m2.7:cloud` for skill adherence/document
  productivity and `nemotron-3-super:cloud` for high-volume multi-agent review.
- Hold: Qwen 3.6 27B/35B, Ornith 35B, Gemma 4 26B/31B, Qwen3-Coder-Next,
  GPT-OSS 20B local, Laguna S 2.1, and Nemotron 3 Super local exceed or crowd the
  current 16 GB production envelope.
- Hold Laguna XS 2.1 on macOS until its documented Metal chat-output issue is
  resolved. Keep GLM 5.1 and MiniMax M2.5 as older rollback comparators, not
  preferred routes. Gemma 4 small variants may enter a later private multimodal
  bake-off but do not duplicate Qwen/GLM lanes by default.

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
