#!/usr/bin/env node
/**
 * TecDoc JSON splitter (Node.js, no deps)
 *
 * The TecDoc "article-number-details" endpoint can return multiple "articles" for the same articleNo,
 * one per supplier (and sometimes multiple per supplier). The existing exporter stores these as:
 *   <out>/by_code/<code>.json   (with tecdoc.articleNumberDetails.articles = [ ...many suppliers... ])
 *
 * This script splits each by_code file into multiple files, each containing exactly ONE article entry,
 * so that subsequent steps (e.g. cross-reference fetching) can work per supplier/article.
 *
 * Output:
 *   <out>/by_article/<code>__<SUPPLIERKEY>__<supplierId>__<articleId>.json
 *   <out>/split_summary.json
 *
 * Usage:
 *   node scripts/tecdoc_split_by_supplier.js --out tecdoc_data/art_2026_01_01_js
 */
/* eslint-disable no-console */

const fs = require("node:fs");
const path = require("node:path");
const { parseArgs } = require("node:util");

function nowIsoSeconds() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function safeFilenameFragment(value) {
  const s = String(value || "").trim().replace(/[^A-Za-z0-9._-]+/g, "_");
  return s || "_";
}

function normalizeSupplierName(name) {
  return String(name || "")
    .normalize("NFKD")
    .replace(/[^0-9A-Za-z]+/g, "")
    .toUpperCase();
}

function parseCsvList(value) {
  if (!value) return [];
  return String(value)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function asListOfObjects(value) {
  return Array.isArray(value) ? value.filter((x) => x && typeof x === "object" && !Array.isArray(x)) : [];
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function writeJsonAtomic(filePath, obj) {
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), "utf8");
  fs.renameSync(tmp, filePath);
}

function createProgressRenderer() {
  let lastLineLen = 0;
  const clearLine = () => {
    process.stderr.write("\r" + " ".repeat(Math.max(0, lastLineLen)) + "\r");
    lastLineLen = 0;
  };
  const render = (line) => {
    const clipped = String(line || "");
    const pad = Math.max(0, lastLineLen - clipped.length);
    process.stderr.write("\r" + clipped + (pad ? " ".repeat(pad) : ""));
    lastLineLen = Math.max(lastLineLen, clipped.length);
  };
  const note = (msg) => {
    clearLine();
    process.stderr.write(String(msg) + "\n");
  };
  const end = () => clearLine();
  return { render, note, end };
}

function buildProgressLine({ idx, total, written, skipped, current }) {
  const pct = total > 0 ? Math.min(100, Math.floor((idx / total) * 100)) : 0;
  const width = 28;
  const filled = Math.round((pct / 100) * width);
  const bar = "[" + "#".repeat(filled) + "-".repeat(Math.max(0, width - filled)) + "]";
  return `${idx}/${total} ${String(pct).padStart(3, " ")}% ${bar} | written=${written} skipped=${skipped} | code=${String(current || "").slice(0, 28)}`;
}

function stripXrefMeta(meta) {
  const out = { ...(meta || {}) };
  for (const k of Object.keys(out)) {
    if (String(k).startsWith("xref_")) delete out[k];
  }
  return out;
}

async function main() {
  const args = parseArgs({
    options: {
      out: { type: "string", default: path.join(process.cwd(), "tecdoc_data", "art_2026_01_01_js") },
      input: { type: "string", default: "" }, // defaults to <out>/by_code
      output: { type: "string", default: "" }, // defaults to <out>/by_article
      codes: { type: "string", default: "" }, // comma-separated
      limit: { type: "string", default: "0" },
      force: { type: "boolean", default: false },
      ui: { type: "string", default: "" }, // pretty/json
    },
    allowPositionals: false,
  });

  const outDir = path.resolve(args.values.out);
  const inDir = path.resolve(args.values.input || path.join(outDir, "by_code"));
  const byArticleDir = path.resolve(args.values.output || path.join(outDir, "by_article"));
  const summaryPath = path.join(outDir, "split_summary.json");

  const ui =
    args.values.ui && ["pretty", "json"].includes(args.values.ui)
      ? args.values.ui
      : process.stdout.isTTY
        ? "pretty"
        : "json";

  const progress = ui === "pretty" ? createProgressRenderer() : null;

  if (!fs.existsSync(inDir)) {
    console.error(`Missing input directory: ${inDir}`);
    process.exitCode = 2;
    return;
  }
  ensureDir(byArticleDir);

  const codesFilterRaw = parseCsvList(args.values.codes);
  const codesFilter = codesFilterRaw.length ? new Set(codesFilterRaw) : null;
  const limit = Number.parseInt(args.values.limit, 10) || 0;

  const files = fs
    .readdirSync(inDir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => path.join(inDir, f))
    .sort((a, b) => a.localeCompare(b));

  const toProcess = [];
  for (const filePath of files) {
    const codeFromFile = path.basename(filePath, ".json");
    if (codesFilter && !codesFilter.has(codeFromFile)) continue;
    toProcess.push({ filePath, codeFromFile });
    if (limit > 0 && toProcess.length >= limit) break;
  }

  const stats = {
    started_at: nowIsoSeconds(),
    in_dir: inDir,
    out_dir: byArticleDir,
    files_total: toProcess.length,
    files_processed: 0,
    files_skipped: 0,
    articles_written: 0,
    articles_skipped: 0,
  };

  if (ui === "pretty" && progress) progress.note(`Split todo: ${toProcess.length} files`);
  if (ui === "json") console.log(JSON.stringify({ ts: nowIsoSeconds(), event: "split_todo", count: toProcess.length }));

  let idx = 0;
  for (const item of toProcess) {
    idx += 1;
    const filePath = item.filePath;
    const codeFromFile = item.codeFromFile;
    let payload;
    try {
      payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
    } catch {
      stats.files_skipped += 1;
      stats.files_processed += 1;
      stats.articles_skipped += 1;
      if (ui === "pretty" && progress) {
        progress.render(
          buildProgressLine({
            idx,
            total: toProcess.length,
            written: stats.articles_written,
            skipped: stats.articles_skipped,
            current: codeFromFile,
          }),
        );
      }
      continue;
    }

    if (!payload || payload.outcome !== "found") {
      stats.files_skipped += 1;
      stats.files_processed += 1;
      if (ui === "pretty" && progress) {
        progress.render(
          buildProgressLine({
            idx,
            total: toProcess.length,
            written: stats.articles_written,
            skipped: stats.articles_skipped,
            current: payload?.code || codeFromFile,
          }),
        );
      }
      continue;
    }

    const tecdoc = payload.tecdoc || {};
    const details = tecdoc.articleNumberDetails || {};
    const code = String(payload.code || details.articleNo || codeFromFile || "").trim();
    const articles = asListOfObjects(details.articles);

    if (!code || articles.length === 0) {
      stats.files_skipped += 1;
      stats.files_processed += 1;
      if (ui === "pretty" && progress) {
        progress.render(
          buildProgressLine({
            idx,
            total: toProcess.length,
            written: stats.articles_written,
            skipped: stats.articles_skipped,
            current: codeFromFile,
          }),
        );
      }
      continue;
    }

    let wroteAny = false;
    for (let i = 0; i < articles.length; i += 1) {
      const a = articles[i];
      const supplierName = String(a.supplierName || "").trim();
      const supplierKey = normalizeSupplierName(supplierName) || safeFilenameFragment(supplierName);
      const supplierId = a.supplierId != null ? String(a.supplierId) : "";
      const articleId = a.articleId != null ? String(a.articleId) : String(i + 1);

      const outFile = path.join(
        byArticleDir,
        `${safeFilenameFragment(code)}__${safeFilenameFragment(supplierKey)}__${safeFilenameFragment(supplierId)}__${safeFilenameFragment(articleId)}.json`,
      );

      if (!args.values.force && fs.existsSync(outFile)) {
        stats.articles_skipped += 1;
        continue;
      }

      const meta = stripXrefMeta(payload.meta || {});
      const splitPayload = {
        code,
        outcome: "found",
        inputLines: payload.inputLines || [],
        tecdoc: {
          articleNumberDetails: {
            articleNo: String(a.articleNo || details.articleNo || code).trim(),
            countArticles: 1,
            articles: [a],
          },
          // Intentionally omit xrefs in split outputs; they can be fetched later per supplier/article.
          crossReferencesBySupplier: [],
          articlesEnriched: [],
        },
        meta: {
          ...meta,
          split_at: nowIsoSeconds(),
          split_from: {
            file: path.relative(outDir, filePath),
            original_count_articles: details.countArticles ?? null,
            original_articles_len: articles.length,
          },
          split_variant: {
            supplier_name: supplierName || null,
            supplier_id: a.supplierId ?? null,
            article_id: a.articleId ?? null,
          },
        },
      };

      writeJsonAtomic(outFile, splitPayload);
      wroteAny = true;
      stats.articles_written += 1;
    }

    if (!wroteAny) stats.files_skipped += 1;
    stats.files_processed += 1;

    if (ui === "pretty" && progress) {
      progress.render(
        buildProgressLine({
          idx,
          total: toProcess.length,
          written: stats.articles_written,
          skipped: stats.articles_skipped,
          current: code,
        }),
      );
    }
  }

  stats.finished_at = nowIsoSeconds();
  writeJsonAtomic(summaryPath, stats);
  if (ui === "pretty" && progress) {
    progress.end();
    progress.note(`Split done. articles_written=${stats.articles_written} articles_skipped=${stats.articles_skipped}`);
  }
}

main().catch((err) => {
  console.error(JSON.stringify({ ts: nowIsoSeconds(), level: "error", event: "fatal", error: String(err) }));
  process.exitCode = 1;
});

