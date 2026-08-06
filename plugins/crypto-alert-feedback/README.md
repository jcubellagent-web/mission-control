# Crypto Alert Feedback

This OpenCLAW plugin owns the `calert:` Telegram callback namespace for Crypto
Alerts topic 20. A fire or ice tap is recorded immediately, then the plugin
posts `Why <emoji>?` as a reply to the rated alert with one skip button. The
next plain-text message from that sender in the same topic is captured as the
optional reason and claimed before a normal model turn begins.

Private feedback text is stored only in an owner-readable, hash-chained JSONL
ledger. Control Tower and shared telemetry receive no reason text, usernames,
message contents, or Telegram identifiers. A separate owner-readable learning
summary contains counts by public alert pattern so later crypto research loops
can compare recurring positive and negative signals without reading chat text.

The alert sender should use callback data in these forms:

- `calert:rate:f:<16-hex-alert-key>`
- `calert:rate:i:<16-hex-alert-key>`

The plugin must be loaded by the same OpenCLAW gateway that already consumes
updates for the JAIMES Telegram bot. Never run a second `getUpdates` consumer.

#JAIMES: Crypto feedback is a gateway plugin so button callbacks and the next
# message stay inside the canonical Telegram update consumer.
