# OpenCLAW and Hermes Release Management

This runbook keeps the ecosystem current without granting an updater permission to mutate production automatically.

## Policy

- Production follows signed stable releases. Alpha, beta, release-candidate, and untagged `main` builds are preview-only.
- Discovery is automatic; preparation is isolated; promotion is reviewed and manual.
- OpenCLAW and Hermes are never upgraded in the same observation window. This preserves fault isolation.
- A candidate must retain the existing Control Tower, Telegram, gateway, scheduled-work, model-routing, and browser contracts.
- Secrets, raw messages, account content, and credentials never enter release evidence. Evidence is metadata-only.

The daily JAIMES LaunchAgent `ai.jaimes.capability-upgrade-sweep` refreshes the host inventory, upstream stable release metadata, npm stable/beta tags, and Control Tower's Capability Watch. Beta information is a preview, not an Action Required item.

The Josh 2.0 LaunchAgent `ai.joshex.self-update-monitor` runs every five minutes. It verifies release-discovery freshness, both production runtimes, OpenCLAW gateway health, JAIMES Telegram health, the daily JAIMES sweep, and candidate-sandbox integrity. It records bounded metadata in `data/self-update-monitor.json` and publishes only status transitions. It cannot install or promote a release.

## Release flow

1. **Discover.** Compare exact installed versions with npm's `latest` tag and the latest signed GitHub release. Do not use “commits behind main” as the stable-version decision.
2. **Classify.** Security and reliability patches become immediate proposals. Stable patch/minor releases enter the weekly review. Major releases and preview builds remain separate, individually reviewed candidates.
3. **Prepare.** Stage an exact version in `/private/tmp`; record the production baseline and rollback version. Hermes also replays every locally carried commit onto the exact stable tag. Any conflict fails the gate.
4. **Verify synthetically.** Run compilation, diff checks, CLI probes, and the critical Telegram/gateway/cron test subset without external delivery or production mutation.
5. **Observe.** Run the isolated candidate for at least 24 hours with metadata-only checks for every critical surface. A missing check is a failure, not “pending success.” Do not send real account traffic merely to exercise a canary.
6. **Review and promote.** Review the exact diff, dependencies, carried patches, and rollback manifest. Promote one product at a time in a maintenance window.
7. **Prove and close.** Re-run health, Telegram receipt, gateway, cron, model-route, browser, and Control Tower checks immediately, after one hour, and after 24 hours. Roll back on contract drift or regression.

Neither candidate tool contains a production promotion command:

```bash
# Josh 2.0: exact stable npm package, isolated install only
python3 scripts/openclaw_update_pipeline.py prepare --target <exact-stable-version>
python3 scripts/openclaw_update_pipeline.py verify --manifest <manifest.json>

# JAIMES: exact stable Git tag plus all carried local commits
python3 scripts/hermes_update_pipeline.py prepare --target <exact-stable-tag>
python3 scripts/hermes_update_pipeline.py verify --manifest <manifest.json>
```

Observation evidence must name the exact candidate target, meet the configured duration, and mark every critical surface `true`. Record it, then re-run `verify`:

```bash
python3 scripts/<product>_update_pipeline.py record-observation \
  --manifest <manifest.json> \
  --observation-evidence <dashboard-safe-evidence.json>
```

`readyForPromotionReview` means the evidence is complete; it does not authorize promotion.

## Immediate operating decisions (2026-08-01)

- Keep OpenCLAW production on `2026.7.1-2`, the current npm stable. Track `2026.7.2-beta.5` only in an isolated preview lane.
- Prepare Hermes `v2026.7.30` (`v0.19.1`) now, but do not run `hermes update` against production. The first carried JAIMES patch currently conflicts during replay, so the 14-patch delivery/cron contract must be ported and tested deliberately.
- Do not run `openclaw doctor --fix` while its preview proposes switching agents to `google-gemini-cli`; live doctor output shows that credential missing for those agents. Preserve the canonical GPT/Codex and specialist routing policy.

## Feature adoption order

Adopt or exploit immediately on the already-installed OpenCLAW stable release:

- Gateway crash-loop stop and stable repair behavior.
- Stronger Telegram topic, progress, media, retry, and account-routing behavior, while retaining the ecosystem's gateway single-writer contract.
- Scheduled-work wake-on-change behavior for read-only/no-change jobs.
- Guarded remote browser downloads and workspace terminals only under the existing interaction lease and permission policy.
- GPT-5.6/Codex delegation and tracked-result improvements; keep the installed alpha Codex CLI pin required by the checked-in routing policy.

Adopt with the Hermes `v0.19.1` port:

- Telegram media timeout/retry fixes and gateway shutdown/message-flush hardening.
- Cross-process updater locking and downgrade prevention.
- Cron failure evidence, per-job model pins, and clean one-shot handling.
- Memory flush before session teardown and stronger session ownership/pinning.
- Current Computer Use permission-mode compatibility and model-catalog updates, after exact contract tests.

Preview, but do not enable in production yet:

- OpenCLAW `2026.7.2` durable ingress/dead-letter recovery, crash-safe state snapshots, session rewind/branching, MCP Apps, and structured approvals. These are high-value, but the available build is beta.
- Hermes Buzz/Nostr, Photon reactions/polls, Comfy Cloud MCP, FLUX3 video, and broad voice changes. They expand surface area and are not required for current JAIMES operations.

## Rollback minimum

Before promotion, preserve the exact package/tag, production commit, local carried-patch list, configuration checksum, service definitions, and last known-good evidence. Rollback must restore the previous code and restart only the affected product, then prove Telegram delivery, gateway health, scheduled work, model routing, browser capability, and Control Tower visibility.
