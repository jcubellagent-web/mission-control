# OpenCLAW and Hermes Release Management

This runbook keeps the ecosystem current without granting an updater permission to mutate production automatically.

## Policy

- Production follows signed stable releases. Alpha, beta, release-candidate, and untagged `main` builds are preview-only.
- Discovery is automatic; preparation is isolated; promotion is reviewed and manual.
- OpenCLAW and Hermes are never upgraded in the same observation window. This preserves fault isolation.
- A candidate must retain the existing Control Tower, Telegram, gateway, scheduled-work, model-routing, and browser contracts.
- Secrets, raw messages, account content, and credentials never enter release evidence. Evidence is metadata-only.

The JAIMES LaunchAgent `ai.jaimes.capability-upgrade-sweep` runs every two hours. It refreshes the host inventory, upstream stable and beta release metadata, npm tags, the fast capability lane, and Control Tower's Capability Watch. Beta information remains preview-only and never becomes production-eligible.

## Fast capability lane

`scripts/capability_release_lane.py` maps bounded public release notes against the weighted ecosystem priorities in `config/capability-release-lane.json`. Reliability, security, agent coordination, approvals, memory/context, observability, workflow integration, and performance signals determine one of three tracks:

- `fast-track`: candidate due within 12 hours;
- `test`: candidate due within 24 hours;
- `routine`: reviewed within 72 hours.

Exact signed-release metadata must match the exact npm/tag target. Stable high-value releases and high-value previews may be prepared automatically in isolated sandboxes. Preview candidates are always `preview-only`; no candidate tool can promote production. Runtime prerequisites fail closed before preparation—for example, a release that declares Node 26 is blocked while the host remains on Node 24.

The lane writes current dashboard-safe state to `data/capability-release-lane.json`, projects its summary into Capability Watch, and appends immutable assessment/preparation/supersession events to `data/capability-release-history.jsonl`. A retry never prepares the same extant manifest twice. A newer release appends a supersession event; it never deletes older evidence.

JAIMES executes the watch and lane from the immutable versioned release at `~/.local/share/mission-control-release-lane/current`; `MISSION_CONTROL_RUNTIME_ROOT` keeps live sidecars in the existing workspace without reading mutable source code. Activation changes only the atomic `current` link after the exact commit bundle is verified.

The Josh 2.0 LaunchAgent `ai.joshex.self-update-monitor` runs every five minutes. It verifies release-discovery freshness, both production runtimes, OpenCLAW gateway health, JAIMES Telegram health, the daily JAIMES sweep, and candidate-sandbox integrity. It records bounded metadata in `data/self-update-monitor.json` and publishes only status transitions. It cannot install or promote a release.

The monitor copies only JAIMES's bounded `dashboard-safe metadata only` Capability Watch payload into Josh 2.0 when the owning-host timestamp is newer. Freshness is calculated from the payload timestamp, not the local copy time. Missing OpenCLAW or Hermes probes are attention states and must never produce a zero-recommendation `ok` result.

## Release flow

1. **Discover.** Compare exact installed versions with npm's `latest` tag and the latest signed GitHub release. Do not use “commits behind main” as the stable-version decision.
2. **Classify.** The fast lane scores every exact release within three hours of publication. High-value stable releases and previews enter isolated qualification immediately; routine releases remain grouped for review. Major releases remain individually reviewed even when their candidate preparation is accelerated.
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

Hermes verification re-applies the current `replayLocalPatches` policy. A manifest with missing, disabled, or conflicted carried-patch replay cannot pass the required replay gate, even if an older manifest recorded its own `ok` flag.

## Immediate operating decisions (2026-08-05)

- Keep OpenCLAW production on `2026.7.1-2`, the current npm stable. Fast-track `2026.7.2-beta.7` only in the isolated preview lane.
- Fast-track Hermes `v2026.8.3` (`v0.20.0`) qualification, but do not run `hermes update` against production. Node 26 is a declared prerequisite while JAIMES currently runs Node 24, and all 14 carried delivery/cron commits must replay and pass the contract suite deliberately.
- Do not run `openclaw doctor --fix` while its preview proposes switching agents to `google-gemini-cli`; live doctor output shows that credential missing for those agents. Preserve the canonical GPT/Codex and specialist routing policy.

## Feature adoption order

Adopt or exploit immediately on the already-installed OpenCLAW stable release:

- Gateway crash-loop stop and stable repair behavior.
- Stronger Telegram topic, progress, media, retry, and account-routing behavior, while retaining the ecosystem's gateway single-writer contract.
- Scheduled-work wake-on-change behavior for read-only/no-change jobs.
- Guarded remote browser downloads and workspace terminals only under the existing interaction lease and permission policy.
- GPT-5.6/Codex delegation and tracked-result improvements; keep the installed alpha Codex CLI pin required by the checked-in routing policy.

Adopt with the Hermes `v0.20.0` port:

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
