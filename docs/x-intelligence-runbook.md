# No-cost X intelligence runbook

## Purpose

`@AgentJc11443` is the ecosystem-owned X research identity. It is a curated intake identity, not an automation account. X provides leads; primary sources provide confirmation.

## Hard boundaries

- Do not automate logged-in X through Chrome, CDP, Playwright, Computer Use, or browser tools.
- Do not follow, unfollow, like, repost, bookmark, post, DM, or modify Lists automatically.
- Do not bypass X API pricing, authentication, quotas, or rate limits.
- Do not purchase xAI credits or enable auto-recharge.
- xAI API stays disabled while exhausted. Grok stays unavailable until a sparse health check confirms usable credits.
- Never store cookies, tokens, credentials, private messages, or raw private account data.

## Collection

Canonical watchlist: `config/x_intelligence_watchlist.json`.

Allowed discovery:
1. Josh forwards an X URL, screenshot, or quoted post.
2. Normal public web search finds indexed excerpts, account names, quoted phrases, or related reporting.
3. Verify against official documentation, GitHub releases, company blogs, filings, status pages, or direct announcements.
4. X-only claims remain incomplete and cannot receive high confidence.

Account and List changes are proposals for Josh to perform manually in X.

## Intake contract

Run `python3 scripts/x_intelligence_intake.py URL --claim "..." --corroboration URL --source-tier primary` after public discovery. The script never opens X or makes network requests. It parses, scores, routes, and deduplicates supplied evidence.

Consequential output includes:
- Claim
- Original X source and supplied timestamp
- Corroborating sources
- Confidence: high, medium, or low
- Why it matters
- Recommended action
- Model used and why
- Explicit coverage limitation

Topic routing:
- News / breaking intelligence: topic 56
- Implementation or deeper research: topic 17
- Actionable crypto: topic 20
- Sorare: topic 19
- Unclear: Inbox topic 1

## Model routing

- Gemini Flash: extraction, classification, deduplication, concise summary.
- Gemini Pro: long threads, ambiguity, conflicts, judgment-heavy analysis.
- Codex/OpenAI subscription: implementation, code/config changes, verified execution, high-stakes integration.
- Ollama: background clustering, watchlist maintenance, repetitive comparisons, low-priority batches.
- Grok/xAI: disabled while exhausted. Do not repeatedly probe it.

Subscription-backed Gemini/Codex usage is not incremental API spend.

## Confidence

- High: original source is primary/reputable and at least one independent primary corroboration exists.
- Medium: credible source with one corroboration, or multiple reputable secondary sources.
- Low: X-only, anonymous, screenshot-only, stale, conflicting, or unverifiable.

Source verification and confidence are separate from relevance and urgency.

## Delivery

Routine success and duplicate scans stay local/log-only. Telegram alerts are limited to time-sensitive developments, material ecosystem updates, credible security/provider incidents, actionable crypto/Sorare findings, blockers, and approvals. Control Tower receives dashboard-safe conclusions only.

## Grok recovery

A deterministic cooldown file may permit one low-cost health check per reset window. A failed/exhausted check remains silent and extends the cooldown. Restore Grok only after a usable-credit response, then reserve it for genuinely X-native specialist work. Never auto-recharge.
