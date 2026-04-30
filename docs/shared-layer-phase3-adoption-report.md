# Shared Layer Phase 3 Adoption Report

Generated: 2026-04-30T08:44:26Z

## Summary

- nodesChecked: 3
- helpersReady: 3
- wrappersReady: 3
- topLevelHelpersReady: 2
- topLevelWrappersReady: 2
- wrappedCronLines: 0
- totalCronLines: 54
- overall: ready_unwrapped

## Node Status

### MacBook/JOSHeX

- Status: ready
- Helper ready: True
- Wrapper ready: True
- Top-level helper ready: False
- Top-level wrapper ready: False
- Cron lines: 0
- Wrapped cron lines: 0

### JOSH 2.0

- Status: ready_unwrapped
- Helper ready: True
- Wrapper ready: True
- Top-level helper ready: True
- Top-level wrapper ready: True
- Cron lines: 6
- Wrapped cron lines: 0

First wrap candidates:

- `*/5 * * * * HOME=/Users/josh2.0 PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /bin/zsh /Users/josh2.0/.openclaw/workspace/mission-control/scripts/update_and_push.sh >> /Users/josh2.0/.openclaw/workspace/logs/mission-control-cron.log 2>&1`
- `*/2 * * * * pgrep -f brain_feed_server.py > /dev/null || /opt/homebrew/bin/python3 /Users/josh2.0/.openclaw/workspace/mission-control/scripts/brain_feed_server.py >> /Users/josh2.0/.openclaw/workspace/logs/brain_feed_server.log 2>&1 &`
- `0 * * * * HOME=/Users/josh2.0 PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /opt/homebrew/bin/python3 /Users/josh2.0/.openclaw/workspace/scripts/jain_silence_detector.py >> /Users/josh2.0/.openclaw/workspace/logs/jain_silence_detector.log 2>&1`
- `0 13 * * * HOME=/Users/josh2.0 PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /opt/homebrew/bin/python3 /Users/josh2.0/.openclaw/workspace/scripts/sorare_cookie_freshness.py >> /Users/josh2.0/.openclaw/workspace/logs/sorare_cookie_freshness.log 2>&1`
- `0 * * * * HOME=/Users/josh2.0 PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /bin/bash /Users/josh2.0/.openclaw/workspace/scripts/jain_medic.sh >> /Users/josh2.0/.openclaw/workspace/logs/jain_medic.log 2>&1`

### JAIMES/J.A.I.N

- Status: ready_unwrapped
- Helper ready: True
- Wrapper ready: True
- Top-level helper ready: True
- Top-level wrapper ready: True
- Cron lines: 48
- Wrapped cron lines: 0

First wrap candidates:

- `15 7 * * 1-5 HOME=/Users/jc_agent PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /opt/homebrew/bin/python3 /Users/jc_agent/.openclaw/workspace/scripts/intelligence_feed.py full >> /Users/jc_agent/.openclaw/workspace/logs/intelligence_feed.log 2>&1`
- `0 10 * * 1-5 HOME=/Users/jc_agent PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /opt/homebrew/bin/python3 /Users/jc_agent/.openclaw/workspace/scripts/intelligence_feed.py full >> /Users/jc_agent/.openclaw/workspace/logs/intelligence_feed.log 2>&1`
- `0 12 * * 1-5 HOME=/Users/jc_agent PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /opt/homebrew/bin/python3 /Users/jc_agent/.openclaw/workspace/scripts/intelligence_feed.py full >> /Users/jc_agent/.openclaw/workspace/logs/intelligence_feed.log 2>&1`
- `0 14 * * 1-5 HOME=/Users/jc_agent PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /opt/homebrew/bin/python3 /Users/jc_agent/.openclaw/workspace/scripts/intelligence_feed.py full >> /Users/jc_agent/.openclaw/workspace/logs/intelligence_feed.log 2>&1`
- `15 16 * * 1-5 HOME=/Users/jc_agent PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /opt/homebrew/bin/python3 /Users/jc_agent/.openclaw/workspace/scripts/intelligence_feed.py full >> /Users/jc_agent/.openclaw/workspace/logs/intelligence_feed.log 2>&1`

## Recommendation

Do not bulk-rewrite crontabs. Wrap one low-risk job at a time, confirm logs and exit code behavior, then proceed to the next job. Start with Mission Control refresh and health/check jobs before sensitive connector workflows.
