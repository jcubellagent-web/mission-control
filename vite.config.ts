import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { createHash } from "node:crypto";
import { readFileSync, statSync, watch } from "node:fs";
import { spawn } from "node:child_process";
import { join, resolve } from "node:path";

const dataRoot = resolve(__dirname, "data");
const configLocalPath = resolve(__dirname, "v2-react", "config.local.js");
const liveWatchFiles = [
  "control-tower-hot.json",
  "control-tower-live.json",
  "brain-feed.json",
  "joshex-brain-feed.json",
  "jaimes-brain-feed.json",
  "jain-brain-feed.json",
  "agent-task-queue.json",
  "handoff-queue.json",
  "agent-context-registry.json",
  "memory-operations.json",
  "model-provider-budgets.json",
  "agentic-crypto-wallet.json",
  "modelUsage.json",
  "jain-daily-signals.json",
  "jain-signal-health.json",
  "personal-codex.json",
  "dashboard-data.json",
  "shared-events.json",
  "codex-jobs.json",
  "agent-heartbeats.json",
];
const walletRefreshTimeoutMs = 60_000;
const walletRefreshOutputLimit = 64 * 1024;
type WalletRefreshResult = { status: number; stdout: string; stderr: string; timedOut: boolean };
let walletRefreshInFlight: Promise<WalletRefreshResult> | null = null;

function boundedAppend(current: string, chunk: unknown) {
  if (current.length >= walletRefreshOutputLimit) return current;
  return (current + String(chunk ?? "")).slice(0, walletRefreshOutputLimit);
}

function contentEtag(body: Buffer | string) {
  return `"${createHash("sha256").update(body).digest("base64url")}"`;
}

function requestAcceptsEtag(req: any, etag: string) {
  const header = req.headers?.["if-none-match"];
  const raw = Array.isArray(header) ? header.join(",") : String(header || "");
  const normalizedEtag = etag.replace(/^W\//, "");
  return raw.split(",").some((candidate) => {
    const normalizedCandidate = candidate.trim().replace(/^W\//, "");
    return normalizedCandidate === "*" || normalizedCandidate === normalizedEtag;
  });
}

function writePrivateJson(req: any, res: any, body: Buffer | string) {
  const etag = contentEtag(body);
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "private, no-cache, max-age=0, must-revalidate");
  res.setHeader("ETag", etag);
  if (requestAcceptsEtag(req, etag)) {
    res.statusCode = 304;
    res.end();
    return;
  }
  res.setHeader("Content-Length", Buffer.isBuffer(body) ? body.byteLength : Buffer.byteLength(body));
  if (String(req.method || "GET").toUpperCase() === "HEAD") {
    res.end();
    return;
  }
  res.end(body);
}

function activeHotProjection() {
  const hot = JSON.parse(readFileSync(join(dataRoot, "control-tower-hot.json"), "utf8"));
  const activeWorks = Array.isArray(hot?.activeWorks) ? hot.activeWorks : [];
  const activeModelRoutes = Array.isArray(hot?.activeModelRoutes) ? hot.activeModelRoutes : [];
  const activeByAgent = activeWorks.reduce((counts: Record<string, number>, work: any) => {
    const agent = typeof work?.ownerAgent === "string" ? work.ownerAgent : "unknown";
    counts[agent] = (counts[agent] || 0) + 1;
    return counts;
  }, {});
  return JSON.stringify({
    schemaVersion: hot?.schemaVersion,
    revision: hot?.revision,
    generatedAt: hot?.generatedAt,
    storeUpdatedAt: hot?.storeUpdatedAt,
    source: hot?.source,
    freshness: hot?.freshness,
    counts: {
      activeWorks: activeWorks.length,
      activeModelRoutes: activeModelRoutes.length,
      activeByAgent,
    },
    activeWorks,
    activeModelRoutes,
  });
}

function liveEventsProjection() {
  const shared = JSON.parse(readFileSync(join(dataRoot, "shared-events.json"), "utf8"));
  const events = (Array.isArray(shared?.events) ? shared.events : [])
    .filter((event: any) => !event?.privacy || event.privacy === "dashboard-safe")
    .slice(0, 96);
  return JSON.stringify({
    source: "Control Tower shared event ledger",
    events,
  });
}

function runWalletRefresh(): Promise<WalletRefreshResult> {
  if (walletRefreshInFlight) return walletRefreshInFlight;
  //JAIMES: Keep wallet refresh single-flight and asynchronous so scheduled refreshes never freeze Control Tower HTTP/SSE.
  const operation = new Promise<WalletRefreshResult>((resolveResult) => {
    const child = spawn("python3", ["scripts/refresh_agentic_robinhood_wallet_live.py"], {
      cwd: __dirname,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let forceKill: ReturnType<typeof setTimeout> | undefined;
    const finish = (result: WalletRefreshResult) => {
      if (settled) return;
      settled = true;
      if (timeout) clearTimeout(timeout);
      if (forceKill) clearTimeout(forceKill);
      resolveResult(result);
    };
    child.stdout?.on("data", (chunk) => { stdout = boundedAppend(stdout, chunk); });
    child.stderr?.on("data", (chunk) => { stderr = boundedAppend(stderr, chunk); });
    child.on("error", (error) => finish({ status: 125, stdout, stderr: boundedAppend(stderr, error.message), timedOut: false }));
    child.on("close", (code) => finish({
      status: timedOut ? 124 : (code ?? 125),
      stdout,
      stderr,
      timedOut,
    }));
    timeout = setTimeout(() => {
      timedOut = true;
      stderr = boundedAppend(stderr, "wallet refresh timed out");
      child.kill("SIGTERM");
      forceKill = setTimeout(() => {
        if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      }, 5_000);
      forceKill.unref?.();
    }, walletRefreshTimeoutMs);
    timeout.unref?.();
  });
  walletRefreshInFlight = operation.finally(() => {
    walletRefreshInFlight = null;
  });
  return walletRefreshInFlight;
}

function liveSourcePayload() {
  const files: Record<string, { mtime: number | null; size: number | null }> = {};
  let newest = 0;
  for (const file of liveWatchFiles) {
    try {
      const stat = statSync(join(dataRoot, file));
      newest = Math.max(newest, stat.mtimeMs);
      files[file] = { mtime: stat.mtimeMs, size: stat.size };
    } catch {
      files[file] = { mtime: null, size: null };
    }
  }
  let hotRevision = 0;
  let changedDomains: string[] = [];
  let nextExpiryAt: string | null = null;
  try {
    const hot = JSON.parse(readFileSync(join(dataRoot, "control-tower-hot.json"), "utf8"));
    hotRevision = Number(hot?.revision || 0);
    nextExpiryAt = typeof hot?.freshness?.nextExpiryAt === "string" ? hot.freshness.nextExpiryAt : null;
    changedDomains = Array.isArray(hot?.changedDomains)
      ? hot.changedDomains.filter((item: unknown): item is string => typeof item === "string").slice(0, 12)
      : [];
  } catch {
    // Compatibility: the hot projection is introduced behind the existing
    // sidecar/SSE lane and may not exist during the first migration build.
  }
  return {
    ok: true,
    source: "Josh 2.0 local live feed",
    updatedAt: new Date(newest || Date.now()).toISOString(),
    revision: hotRevision,
    changedDomains,
    nextExpiryAt,
    workLeaseState: nextExpiryAt && Date.now() >= Date.parse(nextExpiryAt) ? `expired:${nextExpiryAt}` : `active:${nextExpiryAt || "none"}`,
    files,
  };
}

function liveSourceSignature() {
  const payload = liveSourcePayload();
  return JSON.stringify({ files: payload.files, workLeaseState: payload.workLeaseState });
}

function writeLocalLiveEvent(res: any) {
  const payload = liveSourcePayload();
  if (payload.revision) res.write(`id: ${payload.revision}\n`);
  res.write(`event: mission-control\ndata: ${JSON.stringify(payload)}\n\n`);
}

function serveMissionControlFiles(req: any, res: any, next: any) {
  const pathname = String(req.url || "").split("?")[0];
  if (pathname === "/api/live-events") {
    try {
      writePrivateJson(req, res, liveEventsProjection());
    } catch {
      res.statusCode = 503;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Cache-Control", "no-store");
      res.end(JSON.stringify({ ok: false, error: "live event projection unavailable" }));
    }
    return;
  }

  if (pathname === "/api/control-tower-hot") {
    try {
      writePrivateJson(req, res, activeHotProjection());
    } catch {
      res.statusCode = 503;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Cache-Control", "no-store");
      res.end(JSON.stringify({ ok: false, error: "active work projection unavailable" }));
    }
    return;
  }

  if (pathname === "/api/live-source") {
    const body = JSON.stringify(liveSourcePayload());
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(body);
    return;
  }

  if (pathname === "/actions/agentic-crypto-refresh") {
    void runWalletRefresh().then((result) => {
      if (res.writableEnded) return;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Cache-Control", "no-store");
      if (result.status === 0) {
        res.end(result.stdout || JSON.stringify({ ok: true }));
      } else {
        res.statusCode = 500;
        res.end(JSON.stringify({
          ok: false,
          error: (result.stderr || result.stdout || "wallet refresh failed").slice(0, 500),
          timedOut: result.timedOut,
        }));
      }
    }).catch((error) => {
      if (res.writableEnded) return;
      res.statusCode = 500;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Cache-Control", "no-store");
      res.end(JSON.stringify({ ok: false, error: String(error?.message || "wallet refresh failed").slice(0, 500) }));
    });
    return;
  }

  if (pathname === "/events/mission-control") {
    res.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
    });
    res.write("retry: 2000\n\n");
    res.flushHeaders?.();
    let lastSignature = "";
    let lastHeartbeat = Date.now();
    const tick = () => {
      const signature = liveSourceSignature();
      const now = Date.now();
      if (signature !== lastSignature) {
        writeLocalLiveEvent(res);
        lastSignature = signature;
        lastHeartbeat = now;
      } else if (now - lastHeartbeat > 15_000) {
        res.write(": heartbeat\n\n");
        lastHeartbeat = now;
      }
    };
    tick();
    let watchTimer: ReturnType<typeof setTimeout> | undefined;
    const watcher = watch(dataRoot, { persistent: false }, (_eventType, filename) => {
      if (!filename || !liveWatchFiles.includes(String(filename))) return;
      if (watchTimer) clearTimeout(watchTimer);
      watchTimer = setTimeout(tick, 35);
    });
    // #JAIMES: one local mtime stream fans sidecar changes into the kiosk;
    // client-side polling remains a separate reconciliation fallback.
    const interval = setInterval(tick, 1_000);
    const close = () => {
      clearInterval(interval);
      if (watchTimer) clearTimeout(watchTimer);
      watcher.close();
    };
    req.once("close", close);
    res.once("close", close);
    return;
  }

  if (pathname === "/config.local.js") {
    try {
      const body = readFileSync(configLocalPath);
      res.setHeader("Content-Type", "application/javascript; charset=utf-8");
      res.setHeader("Cache-Control", "no-store");
      res.end(body);
      return;
    } catch {
      next();
      return;
    }
  }

  if (!pathname.startsWith("/data/")) {
    next();
    return;
  }

  const rawPath = pathname.replace(/^\/data\/+/, "");
  if (!rawPath || rawPath.includes("..") || !rawPath.endsWith(".json")) {
    next();
    return;
  }

  try {
    const body = readFileSync(join(dataRoot, rawPath));
    writePrivateJson(req, res, body);
  } catch {
    next();
  }
}

function missionControlDataPlugin() {
  return {
    name: "mission-control-data",
    configureServer(server: any) {
      server.middlewares.use(serveMissionControlFiles);
    },
    configurePreviewServer(server: any) {
      server.middlewares.use(serveMissionControlFiles);
    },
    transformIndexHtml: {
      order: "post" as const,
      handler(html: string) {
        const configScript = '<script type="module" src="/config.local.js"></script>';
        if (html.includes("/config.local.js")) return html;
        return html.replace("</head>", `    ${configScript}\n  </head>`);
      },
    },
  };
}

export default defineConfig({
  root: "v2-react",
  plugins: [react(), missionControlDataPlugin()],
  build: {
    outDir: "../dist/v2-react",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5174,
    // The independent JAIMES black-box probe reaches this read-only feed
    // through a tailnet-only Tailscale Serve path. Keep the listener local and
    // allow only the canonical tailnet hostname at Vite's host boundary.
    allowedHosts: ["josh2.tail2a17bd.ts.net"],
  },
});
