// Copies pdfjs-dist's static runtime assets (worker script, WASM decoders,
// cmaps, standard fonts) into public/pdfjs/ so Vite serves them as plain
// static files at a stable, absolute URL (/pdfjs/...) — the same direct,
// no-bundler-magic approach confirmed working in the C0 spike (a real
// browser, plain relative/absolute URLs, no import.meta.url worker
// resolution quirks). Re-run automatically on every `npm install` so these
// stay in sync with whatever pdfjs-dist version is actually installed.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PDFJS_DIR = path.join(__dirname, "..", "node_modules", "pdfjs-dist");
const OUT_DIR = path.join(__dirname, "..", "public", "pdfjs");

function copyFile(relSrc, relDest) {
  const src = path.join(PDFJS_DIR, relSrc);
  const dest = path.join(OUT_DIR, relDest ?? relSrc);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function copyDir(relSrc, relDest) {
  const src = path.join(PDFJS_DIR, relSrc);
  const dest = path.join(OUT_DIR, relDest ?? relSrc);
  fs.rmSync(dest, { recursive: true, force: true });
  fs.cpSync(src, dest, { recursive: true });
}

copyFile("build/pdf.worker.mjs");
copyDir("wasm");
copyDir("cmaps");
copyDir("standard_fonts");

console.log(`[copy-pdfjs-assets] copied worker + wasm/cmaps/standard_fonts to ${OUT_DIR}`);
