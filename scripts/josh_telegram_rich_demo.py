#!/usr/bin/env python3
"""Send a table-first Telegram rich-message demo to Josh."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from josh_work_card import send_rich_message  # type: ignore  # noqa: E402


RICH_HTML = """
<h2>Telegram UX Demo</h2>
<p>Table-first digest format using Bot API rich messages.</p>
<table bordered="true" striped="true">
  <caption>Control Tower snapshot</caption>
  <tr>
    <th>Lane</th>
    <th>Status</th>
    <th>Next</th>
  </tr>
  <tr>
    <td>Josh 2.0</td>
    <td>Testing</td>
    <td>Verify iOS render</td>
  </tr>
  <tr>
    <td>Telegram</td>
    <td>Native rich path</td>
    <td>Use for digests</td>
  </tr>
  <tr>
    <td>Fallback</td>
    <td>Plain table</td>
    <td>Safe if API rejects</td>
  </tr>
</table>
<details>
  <summary>Implementation notes</summary>
  <p>This was sent through the new local helper. Native path uses sendRichMessage; fallback uses regular sendMessage.</p>
</details>
<footer>JOSH 2.0 table UX prototype</footer>
""".strip()


FALLBACK_TEXT = """Telegram UX Demo

Table-first digest format fallback.

Lane       | Status           | Next
-----------|------------------|----------------
Josh 2.0   | Testing          | Verify iOS render
Telegram   | Native rich path | Use for digests
Fallback   | Plain table      | Safe if API rejects

Details: Native path uses sendRichMessage; fallback uses regular sendMessage.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    if args.dry_run:
        print(json.dumps({"ok": True, "rich_html": RICH_HTML, "fallback_text": FALLBACK_TEXT}, indent=2))
        return 0

    result = send_rich_message(RICH_HTML, FALLBACK_TEXT, timeout=args.timeout, silent=False)
    safe_result = {
        "ok": bool(result.get("ok")),
        "native_rich_message": bool(result.get("native_rich_message")),
        "message_id": (result.get("result") or {}).get("message_id"),
        "error": result.get("error"),
        "rich_error": result.get("rich_error"),
    }
    print(json.dumps(safe_result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
