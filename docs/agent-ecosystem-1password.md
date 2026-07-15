# Agent Ecosystem 1Password Hook

The shared secret source is a dedicated 1Password vault named `Agent Ecosystem`.
Josh 2.0 and JAIMES/J.A.I.N use separate host-scoped read-only service accounts.

## Production state

As of 2026-07-14, the production launch paths are fully migrated:

- Josh 2.0 OpenCLAW resolves the shared template from 1Password before the
  gateway starts.
- JAIMES OpenCLAW and Telegram fast-ack resolve the shared template from
  1Password before their child processes start.
- JAIMES Hermes resolves `config/agent-ecosystem-hermes.op.env`. Its OpenRouter
  and Telegram credentials are dedicated items named `OpenRouter JAIMES Hermes`
  and `Telegram JAIMES Hermes Bot`; the remaining integrations use shared vault
  items.
- Active host configuration contains no plaintext assignments for the migrated
  shared credential set. Pre-migration copies are isolated under host-local
  backup directories, not active service/config directories.
- JOSHeX uses its signed-in 1Password desktop account for intentional vault
  administration. It does not receive a background service-account token.

## Manual Provisioning

1. Create the `Agent Ecosystem` vault.
2. Move shared automation secrets into named items with fields matching
   `config/agent-ecosystem.op.env` and
   `config/agent-ecosystem-hermes.op.env`.
3. Create read-only 1Password service accounts:
   - Josh 2.0 host: read-only access to the `Agent Ecosystem` vault.
   - JAIMES/J.A.I.N host: read-only access to the same vault.
4. Store each service-account token only in that host's macOS Keychain service:
   - Josh 2.0: `com.josh.agent-ecosystem.op-service-account.josh2`
   - JAIMES/J.A.I.N: `com.josh.agent-ecosystem.op-service-account.JC-Agents-Mac-mini`
5. Launch shared secreted commands through:
   `scripts/op_agent_env.sh config/agent-ecosystem.op.env -- <command>`.
   Launch Hermes through the same wrapper with the Hermes-specific template.

Service-account creation can be done with an already authenticated 1Password
CLI. Create two distinct credentials; never create one and copy it between
hosts:

```bash
op service-account create "Josh 2.0 Agent Ecosystem" \
  --vault "Agent Ecosystem:read_items" --raw
op service-account create "JAIMES JAIN Agent Ecosystem" \
  --vault "Agent Ecosystem:read_items" --raw
```

The `--raw` value is shown once. On each host, pipe or paste only that host's
value into a hidden local Keychain prompt. Do not put it in a command argument,
clipboard history, shell history, SSH session, file, chat, or dashboard.

Set `AGENT_ECOSYSTEM_OP_TOKEN_SERVICE` only if a host must use a different
Keychain service name.

## Safety Rules

- Keep only `op://` references in Git.
- Do not put service-account tokens in files, launchd plists, Telegram, Control
  Tower, Brain Feed, queues, or logs.
- The wrapper reads the token from Keychain into process environment only and
  does not print the token or resolved secret values.
- Josh 2.0 may use the standard `op run` path. JAIMES uses the canonical
  `scripts/op_agent_env.sh` direct-read path because 1Password CLI 2.34.x can
  deadlock launchd capture pipes. Each JAIMES read writes only to a private
  kernel FIFO, and a separate shell reader imports the value into memory.
- The JAIMES wrapper stops the CLI daemon and removes its FIFO and lock before
  launching the service. It also removes the service-account token from the
  child environment.
- Shared telemetry records route facts and outcomes only; raw prompts, model
  outputs, OAuth payloads, cookies, passwords, tokens, raw emails, and private
  account contents are not allowed in shared JSONL, queues, or dashboard data.

Secret-bearing Inbox prompts are transferred to the local worker over a pipe
and are never persisted or hashed. They intentionally do not receive restart
retries because replay would require retaining the secret-bearing prompt.

## Josh 2.0 launcher wiring

After the Josh 2.0 Keychain entry passes presence-only validation:

- Keep generated OpenCLAW service environment files for non-secret runtime
  settings only. Remove migrated plaintext assignments only after confirming
  their 1Password references and a healthy wrapped restart.
- Chain the existing OpenCLAW environment wrapper into `scripts/op_agent_env.sh`
  before the gateway Node command.
- Launch `com.josh20.telegram-fast-ack` directly through
  `scripts/op_agent_env.sh`; do not use a shell command containing redirects or
  secret values.
- Link and enable `plugins/inbox-coordinator`, allow-list its plugin id, and
  restart only `ai.openclaw.gateway` and
  `com.josh20.telegram-fast-ack`.

The Inbox plugin is scoped to Telegram group `-1003589561528`, topic `1`.
It claims untagged messages, silently yields visible ownership to direct
`@JAIMES`/`@JAIN` mentions, and sends all owned prompts to one asynchronous
coordinator worker. `#jaimes` and plain-language delegation remain Josh 2.0
front-door requests and route to the JAIMES workhorse without a duplicate Josh
model run.

## JAIMES launcher wiring

- Chain `ai.openclaw.gateway` through the generated service environment and
  then `scripts/op_agent_env.sh` before the Node gateway command.
- Launch `ai.jaimes.telegram-fast-ack` directly through
  `scripts/op_agent_env.sh`; use launchd stdout/stderr paths instead of shell
  redirection.
- Remove the six shared assignments from the generated OpenCLAW service file
  only after both services restart and pass presence/equality checks.
- Hermes has distinct OpenRouter and Telegram credentials. Store them as
  dedicated vault items and use `config/agent-ecosystem-hermes.op.env`; never
  replace those two values with the Josh/OpenCLAW credentials.
- Launch `ai.hermes.gateway` directly through `scripts/op_agent_env.sh` before
  the Hermes Python command. Keep Hermes's `.env` for non-migrated settings
  only.

## Presence-only validation

Validation may report item names, field names, model ids, worker ids, host ids,
latency, fallback, and outcome. It must never print resolved field values.

1. Confirm each Keychain service exists without `security ... -w`.
2. Run the wrapper with `op vault get "Agent Ecosystem"` redirected to
   `/dev/null`; expect exit zero.
3. Resolve each required `op://` reference to `/dev/null`.
4. Confirm `launchctl getenv OP_SERVICE_ACCOUNT_TOKEN` is empty.
5. Run plugin runtime inspection, coordinator tests, topic-ownership checks,
   work-card tests, and one synthetic Inbox acceptance task.
6. Verify one reaction, one live card, one final summary, one execution record,
   and no JAIMES duplicate for the untagged task.
