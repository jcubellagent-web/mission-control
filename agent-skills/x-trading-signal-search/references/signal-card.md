# X Signal Card

## Query ladder

Build from precise to broad:

1. Exact contract address in `Latest` and `Top`.
2. Contract plus chain, ticker, project, or official handle.
3. Ticker/cashtag plus chain and project disambiguators.
4. Catalyst terms: listing, launch, partnership, unlock, migration, airdrop.
5. Risk terms: scam, rug, exploit, hack, freeze, honeypot, delist.
6. Market terms: bullish, bearish, buy, sell, whale, liquidity, volume.
7. Official, exchange, security-researcher, and credible-specialist accounts.

For sub-day windows, use X's date filter for coarse retrieval and apply the exact UTC cutoff to returned timestamps.

## Source tiers

- Tier 1: official project, chain, exchange, regulator, filing, explorer, or named security disclosure.
- Tier 2: established reporter, researcher, market maker, or analyst with attributable evidence.
- Tier 3: known community participant with a relevant history but incomplete primary evidence.
- Tier 4: unknown, promotional, anonymous, referral-driven, or contract-ambiguous account.

Do not infer credibility from a verification badge alone.

## Manipulation indicators

- identical or near-identical text across accounts;
- repeated referral links, giveaways, or engagement bait;
- ticker promotion without the exact contract or chain;
- sudden one-sided claims from newly observed accounts;
- engagement disconnected from unique-author count;
- recycled screenshots or claims with no source link;
- coordinated timing or wording.

## Output

Return:

```text
X Signal Card
Asset: <identifier, ticker, project, chain>
Window: <UTC cutoff and modes searched>
Coverage: <queries, posts, unique authors, partial failures>
Sentiment: <bullish / bearish / mixed / unclear, with strength>
Confidence: <high / medium / low, separate from sentiment>

Dominant narratives
- <narrative and supporting source links>

Credible catalysts
- <claim, source tier, corroboration status>

Risks and counter-signals
- <risk, source tier, corroboration status>

Manipulation/spam assessment
- <indicators and estimated impact on the sample>

Primary/onchain verification
- <verified, contradicted, or unresolved facts>

Trading relevance
- <watch / investigate / avoid / no edge, with conditions—not an execution>

Coverage limits
- <rate limits, ambiguity, missing sources, or UI gaps>
```

Link directly to representative X posts and corroborating primary sources. Do not paste a private feed, DM, cookie, token, or raw account export.
