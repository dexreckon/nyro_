// Copies the shared chat UI (../pwa) into www/ for the Android app.
// The app is bundled (no service worker needed) and gets its own CSP: strict, and wss/https-only for the server.
import { cpSync, rmSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "..", "pwa");
const out = join(root, "www");
const SKIP = new Set(["sw.js", "manifest.webmanifest"]);

const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self' https: wss:",   // production: your HTTPS/WSS server only (never plain http/ws)
  "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'",
].join("; ");

if (!existsSync(src)) throw new Error("shared UI not found at " + src);
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
cpSync(src, out, { recursive: true, filter: (p) => !SKIP.has(p.split(/[\\/]/).pop()) });

let html = readFileSync(join(out, "index.html"), "utf8");
html = html
  .replace(/<link rel="manifest"[^>]*>\n?/, "")
  .replace('<meta charset="utf-8">', `<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="${CSP}">`);
if (!html.includes("Content-Security-Policy")) throw new Error("failed to inject CSP");
writeFileSync(join(out, "index.html"), html);
console.log("www/ built from ../pwa (CSP injected, service worker + web manifest excluded)");
