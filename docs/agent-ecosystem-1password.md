# Agent Ecosystem 1Password Hook

The shared secret source is a dedicated 1Password vault named `Agent Ecosystem`.
Josh 2.0 and JAIMES/J.A.I.N use separate host-scoped read-only service accounts.

## Manual Provisioning

1. Create the `Agent Ecosystem` vault.
2. Move shared automation secrets into named items with fields matching
   `config/agent-ecosystem.op.env`.
3. Create read-only 1Password service accounts:
   - Josh 2.0 host: read-only access to the `Agent Ecosystem` vault.
   - JAIMES/J.A.I.N host: read-only access to the same vault.
4. Store each service-account token only in that host's macOS Keychain service:
   - Josh 2.0: `com.josh.agent-ecosystem.op-service-account.josh2`
   - JAIMES/J.A.I.N: `com.josh.agent-ecosystem.op-service-account.JC-Agents-Mac-mini`
5. Launch secreted commands through:
   `scripts/op_agent_env.sh config/agent-ecosystem.op.env -- <command>`.

Set `AGENT_ECOSYSTEM_OP_TOKEN_SERVICE` only if a host must use a different
Keychain service name.

## Safety Rules

- Keep only `op://` references in Git.
- Do not put service-account tokens in files, launchd plists, Telegram, Control
  Tower, Brain Feed, queues, or logs.
- The wrapper reads the token from Keychain into process environment only, then
  executes `op run`. It does not print the token or resolved secret values.
- Shared telemetry records route facts and outcomes only; raw prompts, model
  outputs, OAuth payloads, cookies, passwords, tokens, raw emails, and private
  account contents are not allowed in shared JSONL, queues, or dashboard data.
