# No-cost X intelligence runbook

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
- xAI API stays disabled while exhausted. Grok stays unavailable until a sparse health check confirms usable credits.
- Never export or publish cookies, tokens, credentials, private messages, protected content, or raw private account data.

## Collection

Canonical watchlist: `config/x-intelligence-watchlist.json`.

Allowed discovery:
1. Search and read public X results through the dedicated authenticated UI session.
2. Josh forwards an X URL, screenshot, or quoted post.
3. Normal public web search finds indexed excerpts, account names, quoted phrases, or related reporting.
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
