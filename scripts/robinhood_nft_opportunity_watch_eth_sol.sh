#!/usr/bin/env bash
set -euo pipefail

export ENABLE_EXTERNAL_NFT_FEEDS="${ENABLE_EXTERNAL_NFT_FEEDS:-1}"
export CRYPTOGORILLA_TRANSCRIPT_LIMIT="${CRYPTOGORILLA_TRANSCRIPT_LIMIT:-1}"
export MAGIC_EDEN_SECONDARY_SYMBOLS="${MAGIC_EDEN_SECONDARY_SYMBOLS:-okay_bears,degenerate_ape_academy,mad_lads,famous_fox_federation,solana_monkey_business,claynosaurz,kanpai_pandas,ggsg,doge_capital}"

out="$(/opt/homebrew/bin/python3 /Users/jc_agent/.hermes/scripts/robinhood_nft_opportunity_watch.py)"
if [[ -n "$out" ]]; then
  printf '```text\n%s\n```\n' "$out"
fi
