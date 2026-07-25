---
name: remote-interaction-ops
description: Use when Josh 2.0 or JAIMES performs browser automation, Computer Use, visible desktop work, screenshots, or vision-based UI verification.
---

# Remote Interaction Ops

Use the strongest semantic surface that fits the task while keeping visible
work understandable and shared telemetry free of private screen content.

## Route

1. Prefer a connector, API, or CLI for semantic operations.
2. Prefer Browser DOM/Playwright for ordinary web interaction.
3. Prefer accessibility-element actions for native desktop interaction.
4. Use screenshot vision when semantic state is incomplete.
5. Use raw coordinates only as a bounded fallback, then inspect fresh state.

Host ownership:

- Josh 2.0 owns visible browser, Computer Use, OpenCLAW UI, Telegram UI, and
  Control Tower-local work. Begin a `control_tower_foreground.py` lease before
  taking the display and end it immediately after visible work.
- JAIMES owns headless CDP/Playwright, durable browser workflows, and background
  web checks. It remains headless by default. CuaDriver is a desktop fallback,
  not the default browser route.
- JOSHeX owns private-account, keychain, OAuth, cookie, and personal-Mac-only
  sessions.

Before any browser or desktop surface is assigned, run the fail-closed route
guard:

```bash
python3 scripts/interaction_route_guard.py \
  --target-host <josh2|jaimes|joshex> \
  --surface <browser-dom|browser-visual|desktop-ui|computer-use|semantic-operation>
```

Visible work targeting JOSHeX is rerouted to Josh 2.0 unless a canonical
private-context reason is supplied. Even a valid exception exits for an
explicit personal-device acknowledgement before use. Never silently use the
personal Mac as a fallback.

## Preflight

Before a meaningful interaction task, run the metadata-only host probe:

```bash
python3 scripts/interaction_capability_probe.py \
  --host <josh2|jaimes> --role <visible|headless> --active-display-canary
```

The active display canary captures one temporary frame, records only status,
dimensions, and latency, and deletes the frame. Do not use it on a private page
unless the task requires visible control on that host.

Fail closed when the selected semantic surface is down. Do not silently fall
back from browser DOM control to desktop coordinates.

## Visibility

- On Josh 2.0, let the Codex Computer Use surface render its normal cursor and
  state transitions while the valid display lease is active.
- On JAIMES desktop fallback, keep CuaDriver's synthetic cursor and on-host
  post-action preview enabled. Do not enable the preview for headless CDP jobs.
- Describe phases as `looking`, `clicking`, `typing`, `waiting`, `verifying`, or
  `recovering` in the owning agent's existing work update. Do not create a
  second lifecycle or per-click Brain Feed spam.

## Privacy

Control Tower may receive host role, surface, status, version, permission
booleans, dimensions, latency, and whether on-host observability is enabled.
Never publish screenshots, URLs, page text, selectors, accessibility trees,
cookies, credentials, OTPs, account content, or raw tool output.

## After Work

- Fetch fresh app/browser state after each material action.
- Release the Josh 2.0 display lease and verify Control Tower was restored.
- Refresh interaction capability metadata when a driver, plugin, permission,
  or browser service changed.

#JAIMES: visible work is Josh 2.0-owned and lease-guarded; JAIMES stays
# headless-first while its optional desktop path keeps cursor/PiP feedback on-host.
