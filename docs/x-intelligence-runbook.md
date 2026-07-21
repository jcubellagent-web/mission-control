# Resilient X intelligence runbook

## Purpose

`@AgentJc11443` is the ecosystem-owned X operating and research identity. The agents may use its dedicated authenticated session for public research and bounded research curation. X provides leads; primary sources provide confirmation.

Standing authority covers public search and reading, bounded public-result collection, saved searches, bookmarks, research follows, and private research Lists. This authority does not remove the action gates below.

## Hard boundaries

- Authenticated UI collection is allowed only through the dedicated agent-auth browser on Josh 2.0 and an approved bounded workflow such as `x-trading-signal-search`. Never use Josh's personal browser profile.
- Each collection must verify the `@AgentJc11443` session canary, stay on public X surfaces, cap queries and results, use temporary tabs, and close them after collection.
- Require task-specific approval immediately before each post, reply, quote, repost, like, poll, Space, DM, or other public/external communication.
- Require task-specific approval immediately before identity, recovery, 2FA, security, privacy, billing, API-access, account-deletion, or similar high-impact changes.
- Never use account ownership as authorization for a trade, transfer, wallet signature, order, subscription, or purchase.
- Do not bypass X API pricing, authentication, quotas, or rate limits.
- Do not purchase xAI credits or enable auto-recharge.
- Keep the metered xAI API disabled unless separately verified and budgeted. SuperGrok subscription access is a distinct lane whose live CodexBar allowance determines whether Grok is attempted.
- Never export or publish cookies, tokens, credentials, private messages, protected content, or raw private account data.

## Collection

Canonical watchlist: `config/x-intelligence-watchlist.json`.

Allowed discovery:
1. Use Grok for X-native discovery while its exact live allowance is fresh and above zero.
2. If Grok is exhausted, limited, unavailable, or its allowance telemetry is stale, search and read public X results through the dedicated authenticated UI session.
3. If the X session canary or search fails, use forwarded X links and normal public-web search for indexed excerpts, account names, quoted phrases, or related reporting.
4. Verify against official documentation, GitHub releases, company blogs, filings, status pages, explorers, or direct announcements.
5. X-only claims remain incomplete and cannot receive high confidence.

Routine research curation may maintain saved searches, bookmarks, research follows, and private Lists. Do not use those permissions for engagement campaigns, bulk growth, or public messaging.

For contract-address or ticker research, use `x-trading-signal-search`. Prefer exact contract plus chain, disambiguate ticker-only matches, cap a normal run at eight searches and 200 unique public posts, deduplicate repeated promotion, and disclose partial coverage or rate limits.

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

- Grok subscription: first choice for genuinely X-native discovery and public social context while `data/modelUsage.json` reports a fresh, non-zero allowance. The remaining percentage is telemetry, not a promise about how many requests remain.
- Gemini Flash: extraction, classification, deduplication, concise summary.
- Gemini Pro: long threads, ambiguity, conflicts, judgment-heavy analysis.
- Codex/OpenAI subscription: implementation, code/config changes, verified execution, high-stakes integration.
- Ollama: background clustering, watchlist maintenance, repetitive comparisons, low-priority batches.
- Authenticated X UI: first fallback when Grok is exhausted or unavailable; use the dedicated agent browser and the bounded `x-trading-signal-search` collector.
- Public web and primary sources: final discovery/corroboration fallback when the X UI is rate-limited or needs reauthentication.

Subscription-backed Gemini/Codex usage is not incremental API spend.

## Confidence

- High: original source is primary/reputable and at least one independent primary corroboration exists.
- Medium: credible source with one corroboration, or multiple reputable secondary sources.
- Low: X-only, anonymous, screenshot-only, stale, conflicting, or unverifiable.

Source verification and confidence are separate from relevance and urgency.

## Delivery

Routine success and duplicate scans stay local/log-only. Telegram alerts are limited to time-sensitive developments, material ecosystem updates, credible security/provider incidents, actionable crypto/Sorare findings, blockers, and approvals. Control Tower receives dashboard-safe conclusions only.

## Grok allowance and recovery

Read the live SuperGrok window from `data/modelUsage.json` before routing. At zero remaining, a limited/error state, or telemetry older than 30 minutes, fail closed to authenticated X UI without repeatedly probing Grok. Restore Grok automatically only after fresh allowance telemetry reports it available again. Never infer a request count from the percentage, purchase credits, or enable auto-recharge.
