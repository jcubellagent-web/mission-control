# Handoff: Requesting Josh 2.0: Fix Inbox work-card sender path

- Time: 2026-07-15T04:23:21Z
- From: JOSHeX
- To: agent
- Status: active
- Tool: agent task

## Detail
Created task task-josh-20260715-042321-fix-inbox-work-card-sender-path for Josh 2.0: Repair the verified Telegram delivery root cause. mission-control/scripts/josh_work_card.py imports send_josh_reply only from its own directory, where that module does not exist, so it catches ImportError and runs with API_BASE empty. The Telegram-capable synced helper is workspace/scripts/josh_work_card.py beside workspace/scripts/send_josh_reply.py. Change canonical inbox_coordinator WORK_CARD_SCRIPT and host...

## Privacy
Dashboard-safe only. Do not add secrets or raw private account contents here.

