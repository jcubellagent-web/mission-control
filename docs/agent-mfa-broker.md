# Agent-Owned MFA Broker

## Purpose

Josh 2.0 may complete routine TOTP challenges for explicitly allowlisted
agent-owned service accounts. The broker retrieves the seed from the local
login Keychain, validates the active Chrome origin and path, generates the
short-lived code, fills and submits the browser challenge, and returns only a
metadata receipt.

The broker never provides a seed or TOTP value to an agent, model prompt,
terminal, clipboard, Control Tower, Brain Feed, queue, handoff, or log.

## Interface

```bash
python3 scripts/agent_mfa_broker.py self-test
python3 scripts/agent_mfa_broker.py status --account alpaca-paper
python3 scripts/agent_mfa_broker.py enroll --account alpaca-paper --approval-ref <bounded-reference>
python3 scripts/agent_mfa_broker.py complete --account alpaca-paper
```

There is deliberately no command that displays a seed, recovery code, or TOTP.

## Authorization boundary

Routine `complete` calls are standing-authorized only for enabled entries in
`config/agent-mfa-broker.json`. Each entry binds one opaque account identifier
to exact HTTPS origins, path prefixes, a purpose, and one Keychain service.

Enrollment and replacement of a factor require Josh's explicit approval at the
time of action. Personal accounts, passkeys, recovery codes, account recovery,
factor removal, factor replacement, billing, funding, live brokerage, orders,
transfers, and other money movement remain outside this capability.

The initial Alpaca scope is paper trading and market-data access only. The
broker does not authorize activating a live brokerage account or submitting an
order.

## Secret custody and receipts

- Seeds are stored as generic-password data in the Josh 2.0 login Keychain.
- Seed data is passed to Security.framework in process memory, never as a
  command-line argument or temporary file.
- Codes are injected directly into the verified browser page and are never
  returned by the CLI.
- A time window can be consumed only once per account. Near-expiry windows are
  skipped so the browser receives a viable code. An exclusive local lock
  serializes enrollment and completion across agent processes.
- The Keychain removes seeds from model, shell, clipboard, and shared-state
  exposure. As with other same-user Keychain integrations, a hostile process
  already running as the logged-in Josh 2.0 user remains a residual risk; a
  signed helper with a restricted Keychain ACL is the next hardening tier.
- Local receipts contain account alias, action, origin, path, outcome, and
  timestamps only. Private broker files are mode `0600` under a mode `0700`
  directory. Private files reject symbolic links and unexpected ownership.
- Broker operations take a bounded exclusive lock, normalize URL paths, scope
  selectors to one visible dialog when present, and fail closed on ambiguous or
  persistent challenge UI.

## Enrollment

1. Josh explicitly approves the named agent account and exact purpose.
2. Open the service's authenticator enrollment page in the persistent Josh 2.0
   `agent-auth` Chrome profile.
3. Run `enroll`. The broker validates the page, selects the manual setup path,
   extracts the TOTP material in memory, stores it in Keychain, fills the
   generated code, and submits the challenge.
4. If the service does not accept enrollment, the newly created Keychain item is
   removed and the operation fails closed.
5. Recovery codes stay human-only and must not be captured by the broker.

## Validation and rollback

- `self-test` verifies the RFC 6238 SHA-1 vectors without accessing Keychain.
- `status` reports presence only.
- Tests cover origin/path denial, disabled accounts, seed parsing, redaction,
  and the absence of any reveal operation.
- Disable an account by setting `enabled` to `false`. Removing an enrolled seed
  is a separately approved security-factor change; the broker intentionally has
  no deletion command.

#JAIMES: the broker owns the MFA code-to-browser boundary; shared agents receive only an allowlisted outcome receipt.
