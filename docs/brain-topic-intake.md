# Brain Topic Intake

The **🧠 Brain** Telegram topic is a zero-command inbox for Josh's shared agent ecosystem.

## User workflow

- Send text, a link, photo, screenshot, voice note, audio, video, or document.
- Add any caption or description. The post and attachment are catalogued automatically.
- Prefix a text/caption with `remember:` or `memory:` to create a governed shared-memory candidate. It is proposed for review; it never silently overwrites policy or preferences.
- Ask JAIMES in the Brain topic to find, summarize, tag, or retrieve an item.

## Storage contract

- Catalog database: `~/brain/catalog.sqlite3`
- Content-addressed blobs: `~/brain/blobs/<sha-prefix>/<sha256>.<ext>`
- Watcher configuration: `~/brain/config.json`
- Watcher cursor: `~/brain/watcher_state.json`
- Source scripts: `scripts/brain_topic_catalog.py` and `scripts/brain_topic_watcher.py`
- Runtime copies on JAIMES: `~/.hermes/scripts/`

Files are deduplicated by SHA-256. Telegram chat/thread/message IDs preserve provenance. Text-like files are indexed into FTS5; other media remain searchable by caption, tags, MIME type, filename, and checksum.

## Safety

- Only the configured Control Center chat, Brain thread ID, and Josh sender ID are accepted.
- Everything is archived, but only explicit `remember:`/`memory:` posts are proposed to the governed memory registry.
- Promotion requires an explicit confirmation flag and uses `ecosystem_memory.py propose`; no direct durable-policy writes.
- Blobs and the SQLite catalog stay outside Git and Control Tower.

## Activation

1. Create the Telegram forum topic named `🧠 Brain`.
2. Send `brain ready` in that topic.
3. Read the resulting Hermes session `thread_id` from `~/.hermes/state.db`.
4. Set `thread_id` in `~/brain/config.json`.
5. Install/load `launchd/com.jaimes.brain-topic-watcher.plist`.
6. Send one text note and one attachment; verify both appear via:
   `python3 ~/.hermes/scripts/brain_topic_catalog.py status`
   `python3 ~/.hermes/scripts/brain_topic_catalog.py search "<caption word>"`

#JAIMES: Brain Feed remains live status; this catalog is the durable content store and explicit memory requests enter the governed review path.
