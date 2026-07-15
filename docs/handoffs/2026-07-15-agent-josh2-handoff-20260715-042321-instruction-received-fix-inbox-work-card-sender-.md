# Handoff: Instruction received: Fix Inbox work-card sender path

- Time: 2026-07-15T04:23:21Z
- From: JOSH 2.0
- To: agent
- Status: active
- Tool: agent delegate

## Detail
Received JOSHeX request. Task id: task-josh-20260715-042321-fix-inbox-work-card-sender-path. Objective: Repair the verified Telegram delivery root cause. mission-control/scripts/josh_work_card.py imports send_josh_reply only from its own directory, where that module does not exist, so it catches ImportError and runs with API_BASE empty. The Telegram-capable synced helper is workspace/scripts/josh_work_card.py beside workspace/scripts/send_josh_reply.py. Change canonical inbox_coordinator WORK_C...

## Privacy
Dashboard-safe only. Do not add secrets or raw private account contents here.

