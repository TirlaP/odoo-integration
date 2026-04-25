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
const {
  asListOfObjects,
  createProgressRenderer,
  normalizeSupplierName,
  nowIsoSeconds,
  parseCsvList,
  progressBar,
  progressPercent,
  readJsonFile,
  resolveUiMode,
  safeFilenameFragment,
  writeJsonAtomic,
} = require("./tecdoc_shared");

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function buildProgressLine({ idx, total, written, skipped, current }) {
  const pct = progressPercent(idx, total);
  const bar = progressBar(pct);
  return `${idx}/${total} ${String(pct).padStart(3, " ")}% ${bar} | written=${written} skipped=${skipped} | code=${String(current || "").slice(0, 28)}`;
}

function stripXrefMeta(meta) {
  const out = { ...(meta || {}) };
  for (const k of Object.keys(out)) {
    if (String(k).startsWith("xref_")) delete out[k];
  }
  return out;
}

function parseSplitArgs() {
  return parseArgs({
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
}

function resolveSplitConfig(args) {
  const outDir = path.resolve(args.values.out);
  const inDir = path.resolve(args.values.input || path.join(outDir, "by_code"));
  const byArticleDir = path.resolve(args.values.output || path.join(outDir, "by_article"));
  const summaryPath = path.join(outDir, "split_summary.json");

  const ui = resolveUiMode(args.values.ui);
  const progress = ui === "pretty" ? createProgressRenderer() : null;
  return { args, outDir, inDir, byArticleDir, summaryPath, ui, progress };
}

function validateSplitConfig(config) {
  const { inDir, byArticleDir } = config;
  if (!fs.existsSync(inDir)) {
    console.error(`Missing input directory: ${inDir}`);
    process.exitCode = 2;
    return false;
  }
  ensureDir(byArticleDir);
  return true;
}

function buildSplitTodo(config) {
  const { args, inDir } = config;
  const codesFilterRaw = parseCsvList(args.values.codes);
  const codesFilter = codesFilterRaw.length ? new Set(codesFilterRaw) : null;
  const limit = Number.parseInt(args.values.limit, 10) || 0;
  return limitedSplitTodo(splitInputFiles(inDir).filter((item) => shouldProcessCode(item.codeFromFile, codesFilter)), limit);
}

function splitInputFiles(inDir) {
  return fs
    .readdirSync(inDir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => path.join(inDir, f))
    .sort((a, b) => a.localeCompare(b))
    .map((filePath) => ({ filePath, codeFromFile: path.basename(filePath, ".json") }));
}

function limitedSplitTodo(files, limit) {
  const toProcess = [];
  for (const item of files) {
    toProcess.push(item);
    if (reachedLimit(toProcess, limit)) break;
  }
  return toProcess;
}

function shouldProcessCode(code, codesFilter) {
  return !codesFilter || codesFilter.has(code);
}

function reachedLimit(items, limit) {
  return limit > 0 && items.length >= limit;
}

function createSplitStats(config, toProcess) {
  return {
    started_at: nowIsoSeconds(),
    in_dir: config.inDir,
    out_dir: config.byArticleDir,
    files_total: toProcess.length,
    files_processed: 0,
    files_skipped: 0,
    articles_written: 0,
    articles_skipped: 0,
  };
}

function emitSplitTodo(config, toProcess) {
  const { ui, progress } = config;
  if (ui === "pretty" && progress) progress.note(`Split todo: ${toProcess.length} files`);
  if (ui === "json") console.log(JSON.stringify({ ts: nowIsoSeconds(), event: "split_todo", count: toProcess.length }));
}

function renderSplitProgress(config, stats, idx, total, current) {
  const { ui, progress } = config;
  if (ui !== "pretty" || !progress) return;
  progress.render(
    buildProgressLine({
      idx,
      total,
      written: stats.articles_written,
      skipped: stats.articles_skipped,
      current,
    }),
  );
}

function markSkippedFile(stats, articleSkipped = false) {
  stats.files_skipped += 1;
  stats.files_processed += 1;
  if (articleSkipped) stats.articles_skipped += 1;
}

function buildSplitOutputFile(config, code, article, index) {
  const supplierName = String(article.supplierName || "").trim();
  const supplierKey = normalizeSupplierName(supplierName) || safeFilenameFragment(supplierName);
  const supplierId = nullableString(article.supplierId);
  const articleId = nullableString(article.articleId) || String(index + 1);
  return path.join(config.byArticleDir, splitFileName(code, supplierKey, supplierId, articleId));
}

function nullableString(value) {
  return value == null ? "" : String(value);
}

function nullableValue(value) {
  return value == null ? null : value;
}

function splitFileName(code, supplierKey, supplierId, articleId) {
  return [
    safeFilenameFragment(code),
    safeFilenameFragment(supplierKey),
    safeFilenameFragment(supplierId),
    `${safeFilenameFragment(articleId)}.json`,
  ].join("__");
}

function buildSplitMeta({ config, payload, filePath, details, article, articles, supplierName }) {
  return {
    ...stripXrefMeta(payload.meta || {}),
    split_at: nowIsoSeconds(),
    split_from: {
      file: path.relative(config.outDir, filePath),
      original_count_articles: nullableValue(details.countArticles),
      original_articles_len: articles.length,
    },
    split_variant: {
      supplier_name: supplierName || null,
      supplier_id: nullableValue(article.supplierId),
      article_id: nullableValue(article.articleId),
    },
  };
}

function buildSingleArticleDetails(code, details, article) {
  return {
    articleNo: String(article.articleNo || details.articleNo || code).trim(),
    countArticles: 1,
    articles: [article],
  };
}

function buildSplitPayload({ config, payload, filePath, code, details, article, articles }) {
  const supplierName = String(article.supplierName || "").trim();
  return {
    code,
    outcome: "found",
    inputLines: payload.inputLines || [],
    tecdoc: {
      articleNumberDetails: buildSingleArticleDetails(code, details, article),
      crossReferencesBySupplier: [],
      articlesEnriched: [],
    },
    meta: buildSplitMeta({ config, payload, filePath, details, article, articles, supplierName }),
  };
}

function writeSplitArticles(config, stats, payload, filePath, code, details, articles) {
  let wroteAny = false;
  for (let i = 0; i < articles.length; i += 1) {
    const article = articles[i];
    const outFile = buildSplitOutputFile(config, code, article, i);
    if (!config.args.values.force && fs.existsSync(outFile)) {
      stats.articles_skipped += 1;
      continue;
    }
    writeJsonAtomic(outFile, buildSplitPayload({ config, payload, filePath, code, details, article, articles }));
    wroteAny = true;
    stats.articles_written += 1;
  }
  return wroteAny;
}

function processSplitItem(config, stats, item, idx, total) {
  const result = resolveSplitItem(config, stats, item);
  if (result.skip) return skipSplitItem(config, stats, idx, total, result.current, result.articleSkipped);
  renderSplitProgress(config, stats, idx, total, result.current);
}

function resolveSplitItem(config, stats, item) {
  const payloadResult = readSplitPayload(item);
  if (payloadResult.skip) return payloadResult;
  return resolveSplitPayload(config, stats, item, payloadResult.payload);
}

function readSplitPayload(item) {
  const { filePath, codeFromFile } = item;
  const readResult = readJsonFile(filePath);
  if (!readResult.ok) return unreadableSplitPayload(codeFromFile);
  if (!isFoundPayload(readResult.data)) return ignoredSplitPayload(readResult.data, codeFromFile);
  return { payload: readResult.data };
}

function unreadableSplitPayload(codeFromFile) {
  return { skip: true, current: codeFromFile, articleSkipped: true };
}

function ignoredSplitPayload(payload, codeFromFile) {
  return { skip: true, current: firstText(payload?.code, codeFromFile) };
}

function resolveSplitPayload(config, stats, item, payload) {
  const { filePath, codeFromFile } = item;
  const context = buildSplitContext(payload, filePath, codeFromFile);
  if (!hasSplitArticles(context)) return { skip: true, current: codeFromFile };
  const wroteAny = writeSplitArticles(config, stats, payload, filePath, context.code, context.details, context.articles);
  if (!wroteAny) stats.files_skipped += 1;
  stats.files_processed += 1;
  return { current: context.code };
}

function skipSplitItem(config, stats, idx, total, current, articleSkipped = false) {
  markSkippedFile(stats, articleSkipped);
  renderSplitProgress(config, stats, idx, total, current);
}

function isFoundPayload(payload) {
  return Boolean(payload) && payload.outcome === "found";
}

function buildSplitContext(payload, _filePath, codeFromFile) {
  const tecdoc = payload.tecdoc || {};
  const details = tecdoc.articleNumberDetails || {};
  return {
    details,
    code: firstText(payload.code, details.articleNo, codeFromFile),
    articles: asListOfObjects(details.articles),
  };
}

function firstText(...values) {
  return String(values.find((value) => value) || "").trim();
}

function hasSplitArticles(context) {
  return Boolean(context.code) && context.articles.length > 0;
}

function finishSplitRun(config, stats) {
  stats.finished_at = nowIsoSeconds();
  writeJsonAtomic(config.summaryPath, stats);
  finishSplitProgress(config, stats);
}

function finishSplitProgress(config, stats) {
  if (config.ui !== "pretty" || !config.progress) return;
  config.progress.end();
  config.progress.note(`Split done. articles_written=${stats.articles_written} articles_skipped=${stats.articles_skipped}`);
}

async function main() {
  const config = resolveSplitConfig(parseSplitArgs());
  if (!validateSplitConfig(config)) return;
  const toProcess = buildSplitTodo(config);
  const stats = createSplitStats(config, toProcess);
  emitSplitTodo(config, toProcess);
  for (let i = 0; i < toProcess.length; i += 1) {
    processSplitItem(config, stats, toProcess[i], i + 1, toProcess.length);
  }
  finishSplitRun(config, stats);
}

main().catch((err) => {
  console.error(JSON.stringify({ ts: nowIsoSeconds(), level: "error", event: "fatal", error: String(err) }));
  process.exitCode = 1;
});
