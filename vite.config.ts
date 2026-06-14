import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const dataRoot = resolve(__dirname, "data");
const configLocalPath = resolve(__dirname, "v2-react", "config.local.js");
const liveWatchFiles = [
  "brain-feed.json",
  "joshex-brain-feed.json",
  "jaimes-brain-feed.json",
  "jain-brain-feed.json",
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
  return {
    ok: true,
    source: "Josh 2.0 local live feed",
    updatedAt: new Date(newest || Date.now()).toISOString(),
    files,
  };
}

function liveSourceSignature() {
  return JSON.stringify(liveSourcePayload().files);
}

function writeLocalLiveEvent(res: any) {
  res.write(`event: mission-control\ndata: ${JSON.stringify(liveSourcePayload())}\n\n`);
}

function serveMissionControlFiles(req: any, res: any, next: any) {
  const pathname = String(req.url || "").split("?")[0];
  if (pathname === "/api/live-source") {
    const body = JSON.stringify(liveSourcePayload());
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(body);
    return;
  }

  if (pathname === "/events/mission-control") {
    res.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store",
      "Connection": "keep-alive",
    });
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
    const interval = setInterval(tick, 1_000);
    req.on("close", () => clearInterval(interval));
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
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(body);
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
  },
});
