# Shared Managed-Wallet Signer

## Purpose

JOSHeX, Josh 2.0, JAIMES, and J.A.I.N use one request path for the managed EVM
wallet. The wallet secret remains in JAIMES's login Keychain and never enters
shared source, agent prompts, Control Tower, logs, or another agent host.

The shared client is `scripts/agent_wallet_signer.py`. It invokes JAIMES's
private gateway locally or over the existing SSH trust path. JAIMES starts a
one-shot GUI-session worker so macOS Keychain access stays inside the dedicated
signer host.

## Operations

- `status`: verify signer and network readiness without signing.
- `canary`: sign and immediately discard a deliberately non-broadcastable test
  transaction; only a recovery match is returned.
- `validate`: normalize a proposed transaction and return its private request
  identifier and digest. It does not sign.
- `sign`: consume a matching, short-lived Josh approval artifact and keep the
  signed transaction encrypted in JAIMES's private receipt store. It returns no
  raw signed transaction and never broadcasts.

Examples:

```bash
python3 scripts/agent_wallet_signer.py status --agent josh2
python3 scripts/agent_wallet_signer.py canary --agent jaimes
python3 scripts/agent_wallet_signer.py validate --agent joshex --request /private/path/request.json
python3 scripts/agent_wallet_signer.py sign --agent jain --request /private/path/request.json
```

## Approval and custody rules

- Validation and simulation may proceed without wallet approval.
- Every real transaction signature requires a transaction-specific, short-lived
  approval artifact created only after Josh explicitly approves that exact
  transaction.
- The shared interface intentionally has no broadcast operation. Broadcasting,
  swapping, transferring, approving, bridging, minting, staking, claiming, or
  revoking remains a separate explicit-approval action.
- Approval artifacts are one-time. The signer moves each used artifact into its
  private consumed ledger, preventing replay.
- Signed receipts are encrypted with a key derived only inside the Keychain
  signer context, have a short expiry, and remain mode `0600` on JAIMES.
- This capability does not reactivate any retired crypto research or execution
  schedules. The only recurring crypto jobs remain the three wallet reports.

#JAIMES: all agents share the broker, not the key; live transaction authority remains transaction-specific and human-approved.
