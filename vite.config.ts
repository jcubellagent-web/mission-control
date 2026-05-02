import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

function missionControlDataPlugin() {
  const dataRoot = resolve(__dirname, "data");
  return {
    name: "mission-control-data",
    configureServer(server: any) {
      server.middlewares.use("/data", (req: any, res: any, next: any) => {
        const rawPath = String(req.url || "").split("?")[0].replace(/^\/+/, "");
        if (!rawPath || rawPath.includes("..") || !rawPath.endsWith(".json")) {
          next();
          return;
        }
        try {
          const body = readFileSync(join(dataRoot, rawPath));
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(body);
        } catch {
          next();
        }
      });
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
