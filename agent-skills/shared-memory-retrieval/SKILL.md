---
name: shared-memory-retrieval
description: Use before repeating ecosystem work, asking Josh for context another agent may know, accepting a handoff, or proposing durable learning. Queries and updates the governed shared memory registry with provenance, confidence, validity, privacy, and review controls.
---

# Shared Memory Retrieval

The registry supplements checked-in `AGENTS.md`, `MEMORY.md`, and skills; those files remain authoritative.

## Retrieve

Before repeating work or asking for known context, run the host-appropriate command:

- Josh 2.0: `python3 mission-control/scripts/memory_registry.py retrieve --agent josh2 --query "<specific question>"`
- JAIMES: `~/scripts/ecosystem_memory.py retrieve --agent jaimes --query "<specific question>"`
- J.A.I.N: `~/scripts/ecosystem_memory.py retrieve --agent jain --query "<specific question>"`
- JOSHeX: `ssh josh2 'cd ~/.openclaw/workspace/mission-control && python3 scripts/memory_registry.py retrieve --agent joshex --query "<specific question>"'`

Use the returned source, confidence, validity, and status. If a record conflicts with current tool evidence, trust current evidence and propose a correction.

## Propose

Use `propose` for a durable fact, decision, preference, procedure, lesson, entity, relationship, or episode. Include a specific subject, predicate, value, source, evidence, owner, visibility, privacy, and confidence.

Never promote raw model inference directly. Preferences, policy, procedures, sensitive facts, and conflicts stay pending review. The nightly reviewer may auto-promote only verified low-risk facts, lessons, entities, and relationships.

## Maintain

- Status: `memory_registry.py status`
- Rebuild deterministic sources: `memory_registry.py build`
- Governed review: `memory_registry.py review --apply-safe`
- Refresh Control Tower sidecar: `memory_registry.py export`

Do not publish raw memory content to Control Tower. It receives health, counts, retrieval latency/hit rate, review state, and source coverage only.
