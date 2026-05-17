// Minimal static server for docs/. Used by .claude/launch.json for the preview pane.
// Respects PORT env var so the harness can assign a free port.
//
//   PORT=9876 node scripts/serve.mjs

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const DOCS = resolve(HERE, "..", "docs");
const PORT = Number(process.env.PORT || 9876);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css":  "text/css; charset=utf-8",
  ".js":   "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg":  "image/svg+xml",
  ".png":  "image/png",
  ".jpg":  "image/jpeg",
  ".webp": "image/webp",
  ".ico":  "image/x-icon",
};

createServer(async (req, res) => {
  try {
    const urlPath = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    const safe = urlPath === "/" ? "/index.html" : urlPath;
    const filePath = join(DOCS, safe);
    if (!filePath.startsWith(DOCS)) { res.writeHead(403); return res.end("Forbidden"); }
    const buf = await readFile(filePath);
    res.writeHead(200, {
      "Content-Type": MIME[extname(filePath)] || "text/plain; charset=utf-8",
      "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    });
    res.end(buf);
  } catch (e) {
    if (e.code === "ENOENT" || e.code === "EISDIR") {
      res.writeHead(404); return res.end("Not found");
    }
    res.writeHead(500); res.end("Server error");
  }
}).listen(PORT, "127.0.0.1", () => {
  console.log(`FantasyGM dev server: http://127.0.0.1:${PORT}  (docs=${DOCS})`);
});
