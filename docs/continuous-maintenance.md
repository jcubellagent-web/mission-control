# Continuous Ecosystem Maintenance

Control Tower turns maintenance discovery into a proposal-first operating loop:

`discover -> accept -> lease -> implement -> verify -> complete or defer`

## Source Of Truth

- `data/ecosystem-proposals.json` preserves proposal lifecycle history.
- `data/adaptive-refactor-candidates.json` contains bounded, risk-ranked discoveries.
- `data/reliability-reuse-eval.json` contains the six independent reliability gates.
- `data/maintenance-portfolio.json` is the dashboard-safe current projection.
- `data/maintenance-readiness-history.json` records bounded promotion-readiness evidence.
- `config/continuous-maintenance.json` owns WIP, aging, risk, dependency, and promotion policy.

The hourly `maintenance-portfolio-snapshot` job regenerates the projection. Control Tower remains the operational source of truth; Linear tracks the durable approved work.

## Promotion Rules

- Automatic source mutation is disabled.
- Low-risk work may be classified, prepared in a sandbox, tested, and packaged with rollback evidence automatically.
- Medium-risk work additionally requires design approval and an independent exact-diff review.
- High-risk work additionally requires explicit human approval.
- Every source promotion remains reviewed and must use the exclusive Control Tower change lease.
- Reviewed promotion readiness requires all six reliability gates to pass for seven consecutive distinct evaluations.
- A failed required gate freezes elective changes. Only security fixes, reliability repairs, and rollbacks may advance until the gates recover.
- Active maintenance WIP is capped at three items. Open proposals aging past 30 days are surfaced for a decision; history is never deleted.

## Proposal Lifecycle

Create an accepted proposal with explicit risk and area:

```bash
python3 scripts/ecosystem_proposal_ledger.py \
  --id <stable-proposal-id> \
  --title "<dashboard-safe title>" \
  --summary "<dashboard-safe objective and acceptance boundary>" \
  --owner <agent> --status approved --risk <low|medium|high> --area Reliability \
  --publish
```

Advance the same proposal by appending a transition event:

```bash
python3 scripts/ecosystem_proposal_ledger.py \
  --id <stable-proposal-id> --status <leased|implementing|verifying|implemented|deferred> \
  --publish
```

The ledger retains every event. The maintenance projection selects one latest current row per stable proposal ID and reports its history-event count.

## Dependency Hygiene

- Security updates are proposed immediately.
- Patch and minor updates are grouped weekly.
- Major updates remain individual reviewed proposals.
- Dependency manifest changes run a dedicated review and locked-install validation workflow.

## Verification

For a maintenance-control change, run at minimum:

```bash
python3 -m pytest -q \
  tests/test_continuous_maintenance.py \
  tests/test_ecosystem_proposal_ledger.py \
  tests/test_control_tower_live_projection.py \
  scripts/test_ecosystem_qa_scheduler.py
python3 scripts/continuous_maintenance.py --no-write
npm run build
```

Finish through `scripts/control_tower_change_guard.py finish`; do not manually edit generated `dist/` assets.
