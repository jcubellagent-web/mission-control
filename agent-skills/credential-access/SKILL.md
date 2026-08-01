---
name: credential-access
description: Use when any ecosystem agent needs a password, API key, token, or routine login capability from 1Password, macOS Keychain, or another approved host-local secret store on Josh 2.0 or JAIMES/J.A.I.N.
---

# Credential Access

Route credentialed work to the Mac mini that owns the account or service. Use
the credential there; never retrieve a secret value into chat, model context,
Brain Feed, shared memory, a task payload, the clipboard, a shell argument, or
logs.

## Standard route

1. Identify the intended service and owning host from
   `config/agent-credential-access.json`. JOSHeX coordinates; Josh 2.0 executes
   Josh-owned capabilities; JAIMES executes JAIMES/J.A.I.N-owned capabilities.
2. For checked-in service profiles, launch the intended child through
   `scripts/op_agent_env.sh <profile> [--only NAME[,NAME...]] -- <command>`.
   The profile contains only `op://` references. The service-account token
   remains in that host's login Keychain and the resolved value exists only in
   broker memory and the intended child environment.
3. For an app-specific protected store, use its checked-in launcher or broker.
   Do not inspect another process environment, read a secret store into the
   model, or improvise a reveal command.
4. Report only capability, host, purpose, authorization outcome, and success or
   failure. Never report an item value, OTP, recovery material, or raw account
   content.

## 1Password authorization

For ordinary, expected CLI access, apply
`agent-skills/authorize-1password-prompts/SKILL.md`. Launch with a PTY and a
one-second first yield; if the verified 1Password access sheet remains visible,
authorize it immediately. The expected wait is under two seconds. Match the
requesting process, account, host, and current purpose before clicking.

Do not automatically approve password changes, account recovery, passkey or MFA
enrollment, wallet/payment prompts, purchases, permission expansion, or a sheet
that does not match the command just launched.

## MFA and browser sessions

- Use `agent-skills/agent-mfa-broker/SKILL.md` only for allowlisted routine TOTP
  challenges. The broker injects the code into the exact approved origin and
  never reveals it.
- Existing authenticated browser sessions remain host-local capabilities. Use
  the applicable browser skill; do not export cookies, OAuth material, or
  session storage.
- Personal-account access stays owner/JOSHeX scoped even when execution is
  delegated to a dedicated host.

## Fail closed

Stop when the route, host, account owner, or requested scope is ambiguous; when
the approved wrapper/profile is absent; or when an action crosses an identity,
financial, public, irreversible, recovery, or permission boundary. Ask for the
smallest explicit approval needed. Never work around a failed broker by printing
or copying the secret.

#JAIMES: credential use is autonomous only through approved host-local profiles and brokers; secret disclosure and security-factor changes are never autonomous.
