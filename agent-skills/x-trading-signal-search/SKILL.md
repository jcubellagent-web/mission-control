---
name: x-trading-signal-search
description: Research current X narratives for a token contract address, ticker/cashtag, project, protocol, or trading catalyst. Use for X sentiment checks, contract-address searches, ticker disambiguation, catalyst/risk discovery, source-linked trading intelligence, or comparisons between crypto assets. Perform bounded read-only collection through the agent-owned @AgentJc11443 account, then corroborate material claims with primary and onchain sources.
---

# X Trading Signal Search

Use X as a lead and sentiment surface, never as sole proof for a trade.

## Route

- Prefer a fresh verified Grok specialist pass for X-native discovery while the live SuperGrok allowance is above zero. Treat the reported percentage as pressure telemetry, not a guaranteed request count.
- If Grok is exhausted, limited, unavailable, or its allowance telemetry is stale, continue through the dedicated authenticated X UI collector below. Then fall back to forwarded X links and public-web primary sources if the UI is rate-limited or needs reauthentication.
- Run authenticated X UI collection on the dedicated agent-auth browser, never Josh's personal browser.
- Prefer Josh 2.0 for the signed-in UI session. Send longer analysis and corroboration to JAIMES/J.A.I.N without copying cookies or tokens.
- Keep routine results local or in the user response. Publish only dashboard-safe status and conclusions to Control Tower.

## Workflow

1. Check the X-insight route.
   - Read the live Grok window from `data/modelUsage.json`; use Grok only when the signal is fresh, available, and above zero.
   - A failed Grok check must not end the research request. Record the truthful fallback and continue with authenticated X UI.
2. Normalize the asset.
   - Prefer the exact contract address plus chain.
   - For a ticker, request or infer the chain, project name, official handle, or contract before assigning high confidence.
   - Treat ticker-only matches as ambiguous until identifiers converge.
   - Resolve an official or primary-source handle from the project's official site or documentation when possible, then pass it with `--account`. Do not infer authority from an X badge alone.
3. Generate a bounded query plan:

   ```bash
   python3 scripts/plan_queries.py '<identifier>' \
     --chain base --ticker NOXA --account '<official-handle>' \
     --window 6h --mode both
   ```

   Use at most eight UI searches per request unless the user asks for a deeper scan.
4. Collect public search results through the authenticated UI when Grok is unavailable or when direct public-post evidence is needed:

   ```bash
   node scripts/collect_search.mjs \
     --query-url '<planned X search URL>' \
     --expected-handle AgentJc11443 \
     --cutoff '<plan.window.cutoffUtc>' \
     --max-posts 30
   ```

   The collector must pass the signed-in account canary, remain on `/search`, read public post DOM only, and close its temporary tab. Never inspect cookies, local storage, DMs, notifications, or private account pages.
5. Run the deterministic local analyzer on the collected JSON:

   ```bash
   python3 scripts/analyze_posts.py --input '<collector-output.json>'
   ```

   Use its URL/text deduplication, author-capped sentiment, and manipulation indicators as triage—not as source credibility or factual verification.
6. Assess:
   - narrative direction and disagreement;
   - credible catalysts and risk claims;
   - source independence and authority;
   - freshness, engagement, spam, and coordinated promotion;
   - exact contract/chain match.
7. Corroborate consequential claims with primary sources, chain explorers, official contract registries, filings, exchange notices, security disclosures, or project documentation.
8. Return the X Signal Card defined in [references/signal-card.md](references/signal-card.md).

## Account Authority

Treat `@AgentJc11443` as agent-owned for autonomous research and routine private curation. Search, read, collect public posts, maintain saved searches, bookmarks, research follows, and private Lists without repeated approval when useful to the requested research.

Require task-specific approval immediately before:

- publishing a post, reply, quote, repost, like, poll, Space, or other public signal;
- sending a DM or contacting an external person;
- changing identity, recovery, 2FA, security, privacy, or billing settings;
- purchasing anything, deleting the account, or executing a wallet/trade action.

Never export browser credentials, cookies, tokens, or private messages.

## Confidence Rules

- Keep sentiment, source credibility, and factual confidence separate.
- Contract-address matches outrank ticker-only matches.
- `Latest` measures current flow; `Top` measures X-ranked relevance, not truth.
- Label X-only claims low confidence even when engagement is high.
- Do not claim high coverage when no independently resolved primary-source handle was searched.
- State query window, result count, unique-author count, coverage limits, and any search/rate-limit failure.
- Never present a guaranteed return or execute a trade from this skill.

## Failure Handling

- If Grok is exhausted or unavailable, disclose that it was not used and continue with authenticated X UI; do not silently substitute GPT and call it Grok.
- If the session canary fails, stop and report that the dedicated X session needs reauthentication.
- If selectors fail or X rate-limits search, return partial coverage and the exact failed phase. Do not bypass protections.
- If the identifier remains ambiguous, return candidate mappings and ask for the contract or chain before giving a directional conclusion.
