#!/usr/bin/env python3
"""Verify the rendered Control Tower React kiosk and publish a truthful status.

The check prefers Playwright because it can observe the DOM, console, failed
requests, and viewport overflow.  Dedicated hosts without Playwright fall back
to an installed Chromium browser's ``--dump-dom`` mode.  That fallback still
proves the React page rendered, but is reported as *degraded* because console,
network, and element-level overflow inspection are unavailable.

Screenshot capture is evidence, not a proxy for DOM correctness.  A skipped or
permission-blocked physical screenshot is never recorded as a pass.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUT = DATA / "mission-control-runtime-layout.json"
LIVE_DATA = DATA / "control-tower-live.json"
DASHBOARD_FALLBACK = DATA / "dashboard-data.json"
DEFAULT_KIOSK_URL = "http://127.0.0.1:5174/"
REQUIRED_TEXT = ("Josh 2.0 | Control Tower", "Live Work Board", "Today's Jobs")
REQUIRED_IDS = ("brain-feed", "today-jobs")
REQUIRED_ARIA_LABELS = ("Control Tower summary", "Live Work Board")
INTERNAL_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private filesystem path", re.compile(r"(?:/Users/|/home/)[^\s<]{2,}", re.I)),
    ("Python traceback", re.compile(r"Traceback \(most recent call last\)", re.I)),
    ("source stack", re.compile(r"(?:node_modules/|(?:src|scripts)/[^\s:]+:\d+)", re.I)),
    ("legacy product label", re.compile(r"(?:React v2 Mission Control|Mission Control v2|Local legacy fallback)", re.I)),
    ("secret-shaped text", re.compile(r"(?:Bearer\s+[A-Za-z0-9._~-]{12,}|\bsk-[A-Za-z0-9_-]{12,}|(?:api[_ -]?key|client_secret|refresh_token)\s*[:=]\s*\S+)", re.I)),
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def dashboard_safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    text = re.sub(r"(?:/Users/|/home/)[^\s'\"]+", "[private path]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~-]{12,}|\bsk-[A-Za-z0-9_-]{12,}", "[redacted credential]", text, flags=re.I)
    return text[:300] or type(exc).__name__


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return path.name


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def row(
    name: str,
    state: str,
    detail: str,
    *,
    required: bool = True,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if state not in {"pass", "fail", "degraded", "skipped"}:
        raise ValueError(f"unsupported check state: {state}")
    return {
        "name": name,
        "state": state,
        "ok": state != "fail",
        "required": required,
        "detail": detail,
        **({"evidence": evidence} if evidence else {}),
    }


def fetch(url: str, *, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback kiosk
        return int(response.status), response.read().decode("utf-8", errors="replace")


def check_http(url: str, timeout: float) -> dict[str, Any]:
    try:
        status, body = fetch(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - status should explain the boundary
        return row("kiosk-http", "fail", f"kiosk unreachable: {dashboard_safe_error(exc)}")
    missing = [token for token in ('id="root"', 'type="module"') if token not in body]
    if not 200 <= status < 400:
        return row("kiosk-http", "fail", f"unexpected HTTP {status}")
    if missing:
        return row("kiosk-http", "fail", f"HTTP {status}, but the Vite React shell is missing {', '.join(missing)}")
    return row("kiosk-http", "pass", f"HTTP {status}; current Vite React shell present", evidence={"bytes": len(body)})


def check_control_tower_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        return row("live-data-json", "fail", f"{path.name} failed: {dashboard_safe_error(exc)}")
    if not isinstance(data, dict):
        return row("live-data-json", "fail", "Control Tower JSON is not an object")
    object_fields = ("brainFeed", "agentBrainFeeds", "runtimeLayout")
    list_fields = ("crons", "codexJobs", "actionRequired")
    missing = [key for key in object_fields if not isinstance(data.get(key), dict)]
    missing.extend(key for key in list_fields if not isinstance(data.get(key), list))
    for stamp in ("lastUpdated", "sourceUpdatedAt"):
        if not isinstance(data.get(stamp), str) or not data.get(stamp):
            missing.append(stamp)
    if missing:
        return row("live-data-json", "fail", f"Control Tower JSON is missing canonical fields: {', '.join(missing)}")
    return row(
        "live-data-json",
        "pass",
        f"{path.name} parsed with canonical live-work, runtime, jobs, and source-freshness fields",
        evidence={
            "path": display_path(path),
            "brainFeed": len(data["brainFeed"]),
            "agentBrainFeeds": len(data["agentBrainFeeds"]),
            "crons": len(data["crons"]),
            "codexJobs": len(data["codexJobs"]),
            "actionRequired": len(data["actionRequired"]),
            "lastUpdated": data.get("lastUpdated"),
            "sourceUpdatedAt": data.get("sourceUpdatedAt"),
        },
    )


class RenderedDocumentParser(HTMLParser):
    """Small fallback parser for Chromium ``--dump-dom`` evidence."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.aria_labels: set[str] = set()
        self.text_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template"}:
            self._ignored_depth += 1
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        if values.get("aria-label"):
            self.aria_labels.add(str(values["aria-label"]))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.text_parts.append(data.strip())

    @property
    def visible_text(self) -> str:
        return " ".join(self.text_parts)


def internal_text_leaks(visible_text: str) -> list[dict[str, str]]:
    leaks: list[dict[str, str]] = []
    for label, pattern in INTERNAL_TEXT_PATTERNS:
        match = pattern.search(visible_text)
        if match:
            # Avoid writing a complete secret-shaped match into dashboard-safe
            # status data.  The label and a short redacted prefix are enough.
            sample = match.group(0)
            safe_sample = "[redacted]" if label == "secret-shaped text" else "[private path]" if label == "private filesystem path" else sample[:100]
            leaks.append({"type": label, "sample": safe_sample})
    return leaks


def analyze_rendered_html(document: str) -> tuple[list[str], list[dict[str, str]], dict[str, Any]]:
    parser = RenderedDocumentParser()
    parser.feed(document)
    visible = html.unescape(parser.visible_text)
    failures: list[str] = []
    for token in REQUIRED_TEXT:
        if token not in visible:
            failures.append(f'missing visible text "{token}"')
    for token in REQUIRED_IDS:
        if token not in parser.ids:
            failures.append(f'missing #{token}')
    for token in REQUIRED_ARIA_LABELS:
        if token not in parser.aria_labels:
            failures.append(f'missing aria-label "{token}"')
    if "Control Tower hit a render error" in visible:
        failures.append("React error boundary is visible")
    leaks = internal_text_leaks(visible)
    if leaks:
        failures.append(f"{len(leaks)} visible internal-text leak(s)")
    evidence = {
        "visibleTextBytes": len(visible.encode("utf-8")),
        "requiredIdsFound": sorted(set(REQUIRED_IDS) & parser.ids),
        "requiredAriaLabelsFound": sorted(set(REQUIRED_ARIA_LABELS) & parser.aria_labels),
    }
    return failures, leaks, evidence


def ignorable_browser_request(url: str) -> bool:
    """Ignore browser chrome, not application data or assets."""
    return url.split("?", 1)[0].endswith("/favicon.ico")


def bundled_playwright_browser_missing(exc: BaseException) -> bool:
    """Return true only when Playwright's managed browser is not installed."""
    message = " ".join(str(exc).lower().split())
    return (
        "executable doesn't exist" in message
        or "executable does not exist" in message
        or (
            "playwright install" in message
            and ("download new browsers" in message or "browser" in message)
        )
    )


def launch_playwright_browser(playwright: Any) -> tuple[Any, dict[str, Any]]:
    """Launch a full Playwright browser, reusing installed Chrome if needed.

    A non-installation launch failure remains a hard failure.  This prevents a
    crash, permission error, or incompatible browser from being mislabeled as
    a healthy fallback.
    """
    try:
        browser = playwright.chromium.launch(headless=True)
        return browser, {
            "engine": "playwright-chromium",
            "browser": "bundled Chromium",
            "browserFallback": {"used": False},
        }
    except Exception as exc:  # noqa: BLE001 - classify the launch boundary
        if not bundled_playwright_browser_missing(exc):
            raise
        bundled_error = exc

    #JAIMES: Reuse installed Chrome only when Playwright's bundled executable
    # is absent; all other launch failures stay fatal.
    attempts: list[tuple[str, dict[str, str], str]] = []
    configured = os.environ.get("CONTROL_TOWER_BROWSER", "").strip()
    if configured and Path(configured).is_file():
        attempts.append(("configured executable", {"executable_path": configured}, Path(configured).name))
    attempts.append(("Chrome channel", {"channel": "chrome"}, "Google Chrome"))
    configured_resolved = str(Path(configured).resolve()) if configured and Path(configured).is_file() else ""
    for candidate in browser_candidates():
        resolved = str(Path(candidate).resolve())
        if resolved == configured_resolved:
            continue
        attempts.append(("installed executable", {"executable_path": candidate}, Path(candidate).name))

    fallback_errors: list[str] = []
    for source, launch_args, browser_name in attempts:
        try:
            browser = playwright.chromium.launch(headless=True, **launch_args)
        except Exception as exc:  # noqa: BLE001 - try the next installed browser
            fallback_errors.append(f"{source}: {dashboard_safe_error(exc)}")
            continue
        return browser, {
            "engine": "playwright-chromium",
            "browser": browser_name,
            "browserFallback": {
                "used": True,
                "from": "bundled Chromium",
                "to": source,
                "reason": "Playwright-managed browser executable was not installed",
            },
        }

    detail = "; ".join(fallback_errors) or "no installed Chrome or Chromium candidate was available"
    raise RuntimeError(
        "Playwright-managed Chromium is unavailable and installed-browser fallback failed: "
        f"{detail}"
    ) from bundled_error


def playwright_render(url: str, timeout: float, screenshot_path: Path | None) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return row("rendered-react", "skipped", "Playwright unavailable; trying installed Chromium", required=False), [], False

    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    bad_responses: list[str] = []
    failures: list[str] = []
    leaks: list[dict[str, str]] = []
    viewport_evidence: list[dict[str, Any]] = []
    screenshot_files: list[Path] = []
    try:
        with sync_playwright() as playwright:
            browser, browser_evidence = launch_playwright_browser(playwright)
            probes = (
                ("desktop", {"width": 1440, "height": 1000}, screenshot_path),
                (
                    "mobile",
                    {"width": 390, "height": 844},
                    screenshot_path.with_name(f"{screenshot_path.stem}-mobile{screenshot_path.suffix}") if screenshot_path else None,
                ),
            )
            for label, viewport, probe_screenshot in probes:
                page = browser.new_page(viewport=viewport, device_scale_factor=1)

                def on_console(message: Any, *, probe_label: str = label) -> None:
                    location = message.location or {}
                    location_url = str(location.get("url") or "")
                    if message.type == "error" and not ignorable_browser_request(location_url):
                        safe_message = dashboard_safe_error(RuntimeError(message.text))
                        console_errors.append(f"{probe_label}: {safe_message}"[:300])

                page.on("console", on_console)
                page.on("pageerror", lambda error, probe_label=label: page_errors.append(f"{probe_label}: {dashboard_safe_error(error)}"[:300]))
                page.on(
                    "requestfailed",
                    lambda request, probe_label=label: failed_requests.append(f"{probe_label}: {request.method} {safe_url(request.url)}"[:300])
                    if not ignorable_browser_request(request.url) else None,
                )
                page.on(
                    "response",
                    lambda response, probe_label=label: bad_responses.append(f"{probe_label}: {response.status} {safe_url(response.url)}"[:300])
                    if response.status >= 400 and not ignorable_browser_request(response.url) else None,
                )
                response = page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                page.wait_for_selector("#brain-feed", timeout=int(timeout * 1000))
                page.wait_for_timeout(1250)
                document = page.content()
                probe_failures, probe_leaks, semantics = analyze_rendered_html(document)
                failures.extend(f"{label}: {failure}" for failure in probe_failures)
                for leak in probe_leaks:
                    if leak not in leaks:
                        leaks.append(leak)
                overflow = page.evaluate(
                    """() => {
                      const root = document.documentElement;
                      const viewport = root.clientWidth;
                      const pageOverflow = Math.max(0, root.scrollWidth - viewport);
                      const offenders = [...document.querySelectorAll('body *')]
                        .filter((element) => {
                          const style = getComputedStyle(element);
                          if (style.display === 'none' || style.visibility === 'hidden') return false;
                          const rect = element.getBoundingClientRect();
                          return rect.width > 0 && (rect.right > viewport + 2 || rect.left < -2);
                        })
                        .slice(0, 12)
                        .map((element) => ({
                          tag: element.tagName.toLowerCase(),
                          id: element.id || '',
                          className: String(element.className || '').slice(0, 100),
                          right: Math.round(element.getBoundingClientRect().right),
                        }));
                      return { viewport, pageOverflow, offenders };
                    }"""
                )
                if overflow.get("pageOverflow", 0) > 2:
                    failures.append(f"{label}: horizontal page overflow is {overflow['pageOverflow']}px")
                viewport_evidence.append({
                    "name": label,
                    "width": viewport["width"],
                    "height": viewport["height"],
                    "httpStatus": response.status if response else None,
                    "semantics": semantics,
                    "overflow": overflow,
                })
                if probe_screenshot:
                    probe_screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(probe_screenshot), full_page=True)
                    if probe_screenshot.is_file() and probe_screenshot.stat().st_size > 0:
                        screenshot_files.append(probe_screenshot)
                page.close()
            if console_errors:
                failures.append(f"{len(console_errors)} console error(s)")
            if page_errors:
                failures.append(f"{len(page_errors)} uncaught page error(s)")
            if failed_requests:
                failures.append(f"{len(failed_requests)} failed request(s)")
            if bad_responses:
                failures.append(f"{len(bad_responses)} HTTP error response(s)")
            screenshot_written = bool(screenshot_path and len(screenshot_files) == len(probes))
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return row("rendered-react", "fail", f"Playwright render failed: {dashboard_safe_error(exc)}"), [], False

    evidence = {
        **browser_evidence,
        "viewports": viewport_evidence,
        "consoleErrors": console_errors[:8],
        "pageErrors": page_errors[:8],
        "failedRequests": failed_requests[:8],
        "httpErrorResponses": bad_responses[:8],
        "screenshotsCaptured": len(screenshot_files),
    }
    if failures:
        return row("rendered-react", "fail", "; ".join(failures), evidence=evidence), leaks, screenshot_written
    return row("rendered-react", "pass", "Desktop and mobile React semantics, console, network, and horizontal layout verified", evidence=evidence), leaks, screenshot_written


def browser_candidates() -> list[str]:
    candidates = [
        os.environ.get("CONTROL_TOWER_BROWSER", ""),
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    return [candidate for index, candidate in enumerate(candidates) if candidate and candidate not in candidates[:index] and Path(candidate).is_file()]


def chromium_render(url: str, timeout: float, screenshot_path: Path | None) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    candidates = browser_candidates()
    if not candidates:
        return row("rendered-react", "fail", "no Playwright or installed Chromium render engine is available"), [], False
    browser = candidates[0]
    with tempfile.TemporaryDirectory(prefix="control-tower-render-") as profile:
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-data-dir={profile}",
            "--window-size=1440,1000",
            "--virtual-time-budget=3000",
            "--dump-dom",
        ]
        if screenshot_path:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            command.append(f"--screenshot={screenshot_path}")
        command.append(url)
        process = subprocess.Popen(  # noqa: S603 - fixed local browser command
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        timed_out_after_dom = False
        try:
            stdout, stderr = process.communicate(timeout=max(15, timeout + 8))
        except subprocess.TimeoutExpired as exc:
            # Some macOS Chrome builds emit the complete DOM but retain a
            # background browser process.  Preserve that evidence, then stop
            # the isolated process group rather than reporting a false failure.
            timed_out_after_dom = True
            partial_stdout = exc.stdout or ""
            partial_stderr = exc.stderr or ""
            if isinstance(partial_stdout, bytes):
                partial_stdout = partial_stdout.decode("utf-8", errors="replace")
            if isinstance(partial_stderr, bytes):
                partial_stderr = partial_stderr.decode("utf-8", errors="replace")
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                stdout, stderr = process.communicate()
            stdout = stdout or partial_stdout
            stderr = stderr or partial_stderr
        except Exception as exc:  # noqa: BLE001
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            return row("rendered-react", "fail", f"Chromium render failed: {dashboard_safe_error(exc)}"), [], False
    if process.returncode not in {0, -signal.SIGTERM, -signal.SIGKILL} or not stdout.strip():
        detail = dashboard_safe_error(RuntimeError(stderr)) if stderr.strip() else "no rendered DOM returned"
        return row("rendered-react", "fail", f"Chromium render failed (rc={process.returncode}): {detail}"), [], False
    failures, leaks, evidence = analyze_rendered_html(stdout)
    evidence.update({
        "engine": Path(browser).name,
        "limitations": ["console", "request failures", "element overflow", "mobile viewport"],
        "processStoppedAfterDom": timed_out_after_dom,
    })
    screenshot_written = bool(screenshot_path and screenshot_path.is_file() and screenshot_path.stat().st_size > 0)
    if failures:
        return row("rendered-react", "fail", "; ".join(failures), evidence=evidence), leaks, screenshot_written
    return row(
        "rendered-react",
        "degraded",
        "React semantics verified with Chromium DOM fallback; console, request, and overflow inspection unavailable",
        evidence=evidence,
    ), leaks, screenshot_written


def check_rendered_react(url: str, timeout: float, screenshot_path: Path | None) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    result, leaks, screenshot_written = playwright_render(url, timeout, screenshot_path)
    if result["state"] != "skipped":
        return result, leaks, screenshot_written
    return chromium_render(url, timeout, screenshot_path)


def check_physical_screenshot(screenshot_written: bool, strict: bool) -> dict[str, Any]:
    if screenshot_written:
        return row("screenshot", "pass", "browser screenshot evidence captured", required=strict)
    helper = Path.home() / "scripts" / "capture_mission_control_screen.sh"
    if not helper.exists():
        state = "fail" if strict else "skipped"
        return row("screenshot", state, "physical screenshot helper unavailable", required=strict)
    last_text = ""
    for attempt in range(3):
        try:
            proc = subprocess.run([str(helper)], cwd=ROOT, capture_output=True, text=True, timeout=25, check=False)
        except Exception as exc:  # noqa: BLE001
            last_text = dashboard_safe_error(exc)
            break
        output = " ".join((proc.stdout + " " + proc.stderr).split())
        if proc.returncode == 0 and "SCREENSHOT_OK" in output:
            suffix = "" if attempt == 0 else f" after retry {attempt}"
            return row("screenshot", "pass", f"physical kiosk screenshot captured{suffix}", required=strict)
        last_text = dashboard_safe_error(RuntimeError(output)) if output else f"screenshot helper failed rc={proc.returncode}"
        time.sleep(1)
    state = "fail" if strict else "degraded"
    return row("screenshot", state, f"physical screenshot not verified: {last_text}", required=strict)


def summarize(checks: list[dict[str, Any]]) -> tuple[bool, str, list[str], list[str]]:
    failures = [check["detail"] for check in checks if check["state"] == "fail" and check["required"]]
    degraded = [check["detail"] for check in checks if check["state"] in {"degraded", "skipped"}]
    if failures:
        return False, "attention", failures, degraded
    if degraded:
        return True, "degraded", failures, degraded
    return True, "ok", failures, degraded


def self_test() -> int:
    good = """
    <html><body><h1>Josh 2.0 | Control Tower</h1>
    <section aria-label="Control Tower summary"></section>
    <section id="brain-feed" aria-label="Live Work Board">Live Work Board</section>
    <aside id="today-jobs">Today's Jobs</aside></body></html>
    """
    failures, leaks, _ = analyze_rendered_html(good)
    bad_failures, bad_leaks, _ = analyze_rendered_html(good.replace("Today's Jobs", "/Users/private/token"))
    missing_browser_detected = bundled_playwright_browser_missing(
        RuntimeError("BrowserType.launch: Executable doesn't exist. Please run playwright install.")
    )
    unrelated_failure_rejected = not bundled_playwright_browser_missing(
        RuntimeError("BrowserType.launch: browser process crashed")
    )
    ok = (
        not failures
        and not leaks
        and bool(bad_failures)
        and bool(bad_leaks)
        and missing_browser_detected
        and unrelated_failure_rejected
    )
    print(json.dumps({
        "ok": ok,
        "goodFailures": failures,
        "badFailures": bad_failures,
        "badLeaks": bad_leaks,
        "missingBrowserDetected": missing_browser_detected,
        "unrelatedFailureRejected": unrelated_failure_rejected,
    }, indent=2))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the rendered Control Tower React kiosk.")
    parser.add_argument("--url", default=DEFAULT_KIOSK_URL)
    parser.add_argument("--data", "--dashboard", dest="data_path", type=Path, help="Generated Control Tower JSON; defaults to slim live data, then dashboard fallback.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--screenshot-path", type=Path)
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--strict-visual", action="store_true", help="Treat unavailable screenshot evidence as a failure.")
    parser.add_argument("--strict-browser", action="store_true", help="Require full Playwright console/network/overflow inspection.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    parsed_url = urllib.parse.urlsplit(args.url)
    if parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("--url must target the loopback Control Tower kiosk")
    if args.screenshot_path and args.screenshot_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        parser.error("--screenshot-path must end in .png, .jpg, or .jpeg")

    checked_at = utc_now()
    data_path = args.data_path or (LIVE_DATA if LIVE_DATA.exists() else DASHBOARD_FALLBACK)
    checks = [check_http(args.url, args.timeout), check_control_tower_json(data_path)]
    render_check, leaks, screenshot_written = check_rendered_react(args.url, args.timeout, args.screenshot_path)
    if args.strict_browser and render_check["state"] == "degraded":
        render_check = row(
            "rendered-react",
            "fail",
            f"strict browser evidence required: {render_check['detail']}",
            evidence=render_check.get("evidence"),
        )
    checks.append(render_check)
    if args.no_screenshot:
        checks.append(row("screenshot", "skipped", "screenshot explicitly disabled", required=False))
    else:
        checks.append(check_physical_screenshot(screenshot_written, args.strict_visual))

    ok, status, issues, degraded = summarize(checks)
    if ok and status == "ok":
        summary = "Rendered Control Tower semantics, console, network, layout, data, and screenshot verified."
    elif ok:
        summary = "Required Control Tower checks passed with explicitly degraded visual/runtime evidence."
    else:
        summary = "; ".join(issues)
    payload = {
        "ok": ok,
        "status": status,
        "checkedAt": checked_at,
        "url": safe_url(args.url),
        "summary": summary,
        "issues": issues,
        "degradedReasons": degraded,
        "checks": checks,
        "textQuality": {"visibleInternalTextLeaks": leaks},
        "source": "mission_control_runtime_layout_check.py",
    }
    atomic_write(args.output, payload)
    print(json.dumps(payload, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
