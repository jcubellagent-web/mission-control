---
name: authorize-1password-prompts
description: Promptly handle expected 1Password CLI authorization sheets for trusted jobs running on Josh 2.0 or JAIMES. Use when an authorized command invokes `op read`, `op run`, or another approved 1Password CLI operation and may display “1Password Access Requested,” especially when an SSH job would otherwise wait for or time out on the host-local prompt.
---

# Authorize expected 1Password prompts

Keep the authorization wait under two seconds when the host desktop is available.

## Before launch

1. Confirm the job is user-authorized and expected to invoke 1Password.
2. Identify the owning host, command, expected requesting process, and expected account or vault label without exposing secrets.
3. Keep the command private. Never publish credentials, item paths, vault content, screenshots, or raw CLI output to shared state.

## Launch and authorize

1. Start the SSH command with a PTY and `yield_time_ms` of 1000. Do not begin with a 10–30 second wait.
2. If the command remains active after the first yield, immediately capture the owning host’s desktop to a uniquely named file in `/private/tmp`, copy it locally, and inspect it with `view_image`.
3. Approve only when the sheet title is `1Password Access Requested`, the requesting process matches the launched job, the displayed account is expected, and the action is ordinary CLI access. Stop on any password change, recovery, MFA, wallet, payment, new credential, or broader-access request.
4. Click the freshly observed `Authorize` button immediately. Derive coordinates from the current screenshot; never reuse coordinates across hosts, display modes, or stale screenshots.
5. Poll the original command after one second. Verify it resumed from its terminal output rather than inferring success from the click.
6. If authorization timed out, rerun the same command once and begin the screenshot-and-click sequence during the first one-second yield. Escalate repeated failure instead of looping.

## Persistent access

Prefer the canonical host service account or the approved host-local Keychain cache for routine unattended work. Treat `Always Allow`, new service accounts, new API keys, permission broadening, and credential persistence as security-sensitive configuration; use them only through the checked-in bootstrap procedure and its required authorization.

## Closeout

1. Remove temporary prompt screenshots from both machines.
2. Verify the command completed and record only a dashboard-safe result.
3. Inspect every Terminal window's `busy` state on the dedicated host. Close all completed (`busy=false`) windows before finishing; never close a busy window without identifying its process and purpose.
4. Verify no completed Terminal windows remain. If no busy Terminal windows exist, quit Terminal entirely and verify the process stopped. Do not let `.command` launcher windows accumulate behind Control Tower.
5. Restore and verify Control Tower kiosk mode on Josh 2.0 after live GUI work.
6. Close Screen Sharing if it was used.
