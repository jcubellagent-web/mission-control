---
name: agent-mfa-broker
description: Use when an allowlisted agent-owned account presents a routine TOTP challenge on Josh 2.0.
---

# Agent MFA Broker

Use `scripts/agent_mfa_broker.py` only on Josh 2.0 for an enabled account in
`config/agent-mfa-broker.json`.

## Routine login

1. Confirm the persistent `agent-auth` Chrome route is alive.
2. Run `status --account <alias>` and require `enrolled: true`.
3. Navigate the existing browser session to the legitimate service challenge.
4. Run `complete --account <alias>` in the logged-in Josh 2.0 GUI audit
   session. For SSH orchestration, enter the console-user bootstrap with
   `sudo -n launchctl asuser <console-uid>`, immediately drop back to the
   `josh2.0` user, and invoke the broker. Never unlock the Keychain or pass a
   login password in a command.
5. Trust only `ok`, `browserInjected`, `submitted`, and the metadata receipt.
   Never inspect the DOM for the filled code.

## Enrollment

`enroll` changes an authentication factor and requires Josh's explicit approval
for the exact account and purpose at the moment of action. Pass only a bounded
approval reference; never put Josh's raw message, a seed, QR content, recovery
code, or account data in the argument.

## Hard boundaries

- Do not use the broker for personal accounts or an account not checked into the
  allowlist.
- Do not use it for passkeys, recovery, factor removal or replacement, live
  finance, funding, orders, transfers, billing, purchases, or public actions.
- Do not add a seed/code reveal command, clipboard path, debug dump, screenshot,
  or shared telemetry field.
- Fail closed on multiple matching browser pages, origin/path mismatch, repeated
  use of one TOTP window, missing Keychain material, or an unrecognized UI.

#JAIMES: routine allowlisted TOTP may be standing-authorized, but enrollment and security-factor changes remain explicit-approval actions.
