# Inbox Coordinator

Trusted Josh 2.0 OpenCLAW hook for the J.A.I.N Control Center Inbox.

It claims only untagged Telegram messages in group `-1003589561528`, topic
`1`, then passes the prompt over a pipe to the host-local fast-ack helper. The
helper sends the acknowledgement, creates one live card, and submits one
asynchronous coordinator worker. Prompt text is never placed in a process
argument or plugin log.

Direct `@JAIMES`/`@JAIN` mentions are claimed silently by Josh 2.0 so its main
model does not also answer; the JAIMES bot remains the visible owner. `#jaimes`
and plain-language delegation requests are routing hints and remain owned by
Josh 2.0 for explicit worker delegation.

## Verify

```bash
npm test
openclaw plugins inspect inbox-coordinator --runtime --json
```
