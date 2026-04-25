#!/usr/bin/env node
/**
 * TecDoc cross-reference fetcher (Node.js, no deps)
 *
 * Reads existing TecDoc JSON files (created by `tecdoc_fetch_from_xml.js` or by the splitter) and enriches them
 * with cross-references through OEM numbers:
 *
 *   GET /artlookup/search-for-cross-references-through-oem-numbers/article-no/{articleNo}/supplierName/{supplierName}
 *
 * It only touches files where `outcome === "found"` and (in all-suppliers mode) `meta.xref_complete !== true`.
 *
 * Usage:
 *   RAPIDAPI_KEY=... node scripts/tecdoc_fetch_xrefs_for_found.js --out tecdoc_data/art_2026_01_01_js
 *
 * Common use-case: fetch XRefs only for one supplier (avoids slow multi-supplier runs):
 *   RAPIDAPI_KEY=... node scripts/tecdoc_fetch_xrefs_for_found.js --out tecdoc_data/art_2026_01_01_js --supplier "FEBI BILSTEIN"
 */
/* eslint-disable no-console */

const fs = require("node:fs");
const path = require("node:path");
const { parseArgs } = require("node:util");
const {
  asListOfObjects,
  createProgressRenderer,
  crossReferencePath,
  dedupeCrossRefs,
  fetchTecDocJson,
  formatDurationSeconds,
  isMaintenanceOrRateLimitedPayload,
  normalizeSupplierName,
  nowIsoSeconds,
  parseCsvList,
  progressBar,
  progressPercent,
  rateLimitClient,
  readJsonFile,
  reportFatal,
  resolveUiMode,
  trimText,
  uniqueSortedStrings,
  writeJsonAtomic,
} = require("./tecdoc_shared");

function entryHasTransientMaintenance(entry) {
  if (!entry || typeof entry !== "object") return false;
  if (entry.error === "maintenance") return true;
  return isMaintenanceOrRateLimitedPayload(entry.response);
}

function pickInputDir(outDir, explicitInputDir) {
  if (explicitInputDir) return path.resolve(explicitInputDir);
  const byArticleDir = path.join(outDir, "by_article");
  const byCodeDir = path.join(outDir, "by_code");
  if (fs.existsSync(byArticleDir)) {
    const anyJson = fs.readdirSync(byArticleDir).some((f) => f.endsWith(".json"));
    if (anyJson) return byArticleDir;
  }
  return byCodeDir;
}

class TecDocClient {
  constructor({ apiKey, apiHost, rps, timeoutMs, maxRetries }) {
    Object.assign(this, initialClientState({ apiKey, apiHost, rps, timeoutMs, maxRetries }));
  }

  async _rateLimit() {
    await rateLimitClient(this);
  }

  async _getJson(pathname) {
    return fetchTecDocJson({
      apiKey: this.apiKey,
      apiHost: this.apiHost,
      baseUrl: this.baseUrl,
      maxRetries: this.maxRetries,
      timeoutMs: this.timeoutMs,
      timeoutMode: "continue",
      logger: null,
      rateLimit: () => this._rateLimit(),
      detectMaintenance: true,
    }, pathname);
  }

  async crossReferences(articleNo, supplierName) {
    return this._getJson(crossReferencePath(articleNo, supplierName));
  }
}

function initialClientState({ apiKey, apiHost, rps, timeoutMs, maxRetries }) {
  return {
    apiKey,
    apiHost,
    baseUrl: `https://${apiHost}`,
    minDelayMs: rps > 0 ? Math.ceil(1000 / rps) : 0,
    timeoutMs,
    maxRetries,
    _lastRequestAt: 0,
    _rateQueue: Promise.resolve(),
  };
}

function buildProgressLine({ done, total, inflight, concurrency, foundComplete, foundIncomplete, errors, current, startedAtMs }) {
  const pct = progressPercent(done, total);
  const bar = progressBar(pct);
  const elapsedSec = startedAtMs ? (Date.now() - startedAtMs) / 1000 : 0;
  const rate = elapsedSec > 0 ? done / elapsedSec : 0;
  const eta = etaText(rate, total, done);
  return `${done}/${total} ${String(pct).padStart(3, " ")}% ${bar} | inflight=${inflight}/${concurrency} | complete=${foundComplete} incomplete=${foundIncomplete} err=${errors} | eta=${eta} | code=${String(current || "").slice(0, 24)}`;
}

function etaText(rate, total, done) {
  if (rate <= 0) return "?";
  return formatDurationSeconds((total - done) / rate);
}

function supplierFilterEmpty(supplierFilter) {
  return !supplierFilter || supplierFilter.size === 0;
}

function supplierAliases(supplierName) {
  const raw = trimText(supplierName);
  return raw ? [raw, raw.toUpperCase(), normalizeSupplierName(raw)] : [];
}

function supplierMatchesFilter(supplierName, supplierFilter) {
  return supplierFilterEmpty(supplierFilter) || supplierAliases(supplierName).some((alias) => supplierFilter.has(alias));
}

function shouldRetryExistingEntry(existingEntry, retryIncomplete) {
  return retryIncomplete && (existingEntry.error || entryHasTransientMaintenance(existingEntry));
}

function shouldFetchSupplier({ existingEntry, force, retryIncomplete }) {
  return Boolean(force || !existingEntry || shouldRetryExistingEntry(existingEntry, retryIncomplete));
}

function parseXrefArgs() {
  return parseArgs({
    options: {
      out: { type: "string", default: path.join(process.cwd(), "tecdoc_data", "art_2026_01_01_js") },
      input: { type: "string", default: "" },
      "api-key": { type: "string", default: process.env.RAPIDAPI_KEY || "" },
      rps: { type: "string", default: "1" },
      timeout: { type: "string", default: "60" },
      retries: { type: "string", default: "6" },
      concurrency: { type: "string", default: "1" },
      force: { type: "boolean", default: false },
      "retry-incomplete": { type: "boolean", default: false },
      supplier: { type: "string", default: "" },
      codes: { type: "string", default: "" },
      ui: { type: "string", default: "" },
    },
    allowPositionals: false,
  });
}

function buildSupplierFilter(rawValues) {
  const supplierFilter = new Set();
  for (const supplier of rawValues) {
    supplierFilter.add(supplier);
    supplierFilter.add(supplier.toUpperCase());
    supplierFilter.add(normalizeSupplierName(supplier));
  }
  return supplierFilter;
}

function buildCodesFilter(rawValues) {
  const codes = rawValues.map((code) => trimText(code)).filter(Boolean);
  return codes.length ? new Set(codes) : null;
}

function resolveXrefConfig(args) {
  const outDir = path.resolve(args.values.out);
  const supplierFilterRaw = parseCsvList(args.values.supplier);
  const codesFilterRaw = parseCsvList(args.values.codes);
  const ui = resolveUiMode(args.values.ui);
  return {
    args,
    outDir,
    byCodeDir: pickInputDir(outDir, args.values.input),
    summaryPath: path.join(outDir, "xref_summary.json"),
    supplierFilterRaw,
    supplierFilter: buildSupplierFilter(supplierFilterRaw),
    codesFilterRaw,
    codesFilter: buildCodesFilter(codesFilterRaw),
    ui,
    progress: ui === "pretty" ? createProgressRenderer() : null,
  };
}

function validateXrefConfig(config) {
  if (!config.args.values["api-key"]) {
    console.error("Missing RAPIDAPI_KEY (set env var or pass --api-key).");
    process.exitCode = 2;
    return false;
  }
  if (!fs.existsSync(config.byCodeDir)) {
    console.error(`Missing directory: ${config.byCodeDir}`);
    process.exitCode = 2;
    return false;
  }
  return true;
}

function createXrefClient(config) {
  const args = config.args;
  return new TecDocClient({
    apiKey: args.values["api-key"],
    apiHost: "tecdoc-catalog.p.rapidapi.com",
    rps: Number.parseFloat(args.values.rps) || 3,
    timeoutMs: (Number.parseInt(args.values.timeout, 10) || 20) * 1000,
    maxRetries: Number.parseInt(args.values.retries, 10) || 1,
  });
}

function listJsonFiles(dirPath) {
  return fs.readdirSync(dirPath).filter((fileName) => fileName.endsWith(".json")).map((fileName) => path.join(dirPath, fileName));
}

function payloadCode(payload, filePath) {
  return trimText(payload.code || path.basename(filePath, ".json"));
}

function codeMatchesFilter(code, codesFilter) {
  return !codesFilter || codesFilter.has(code);
}

function articleSupplierNames(articles) {
  return uniqueSortedStrings(articles.map((article) => article.supplierName));
}

function existingSupplierMap(existing) {
  return new Map(existing.map((entry) => [trimText(entry.supplierName), entry]).filter(([key]) => key));
}

function xrefContextFromPayload(config, filePath, payload) {
  if (!isFoundPayload(payload)) return null;
  const code = payloadCode(payload, filePath);
  if (!codeMatchesFilter(code, config.codesFilter)) return null;
  const details = articleDetails(payload);
  const articles = detailArticles(details);
  const supplierNames = articleSupplierNames(articles);
  const targetSuppliers = supplierNames.filter((supplier) => supplierMatchesFilter(supplier, config.supplierFilter));
  return { payload, filePath, code, details, articles, supplierNames, targetSuppliers };
}

function articleDetails(payload) {
  return payload?.tecdoc?.articleNumberDetails;
}

function detailArticles(details) {
  return asListOfObjects(details?.articles);
}

function isFoundPayload(payload) {
  return Boolean(payload) && payload.outcome === "found";
}

function supplierFilterHasNoMatch(config, context) {
  return !supplierFilterEmpty(config.supplierFilter) && context.targetSuppliers.length === 0;
}

function fileWasAttempted(meta, hasTransient) {
  return Boolean(meta.xref_fetched_at) || meta.xref_complete === false || hasTransient;
}

function fileDone(meta, hasTransient) {
  return meta.xref_complete === true && !hasTransient;
}

function shouldSkipAllSupplierMode(config, meta, hasTransient) {
  return fileDone(meta, hasTransient) || (fileWasAttempted(meta, hasTransient) && !config.args.values["retry-incomplete"]);
}

function supplierModeNeedsWork(config, context, existingBySupplier) {
  return config.args.values.force || context.targetSuppliers.some((supplierName) => {
    const existingEntry = existingBySupplier.get(supplierName) || null;
    return shouldFetchSupplier({ existingEntry, force: false, retryIncomplete: config.args.values["retry-incomplete"] });
  });
}

function shouldQueueXrefFile(config, context, existingBySupplier, hasTransient) {
  if (!config.args.values.force && supplierFilterEmpty(config.supplierFilter)) {
    return !shouldSkipAllSupplierMode(config, context.payload.meta || {}, hasTransient);
  }
  return supplierModeNeedsWork(config, context, existingBySupplier);
}

function collectXrefTodo(config) {
  const todo = [];
  let skippedNoMatch = 0;
  for (const filePath of listJsonFiles(config.byCodeDir)) {
    const context = readXrefContext(config, filePath);
    if (!context) continue;
    if (shouldSkipNoSupplierMatch(config, context)) {
      skippedNoMatch += 1;
      continue;
    }
    queueXrefContext(config, todo, context);
  }
  return { todo, skippedNoMatch };
}

function shouldSkipNoSupplierMatch(config, context) {
  return supplierFilterHasNoMatch(config, context);
}

function queueXrefContext(config, todo, context) {
  const existing = asListOfObjects(context.payload?.tecdoc?.crossReferencesBySupplier);
  const hasTransient = existing.some((entry) => entryHasTransientMaintenance(entry));
  const existingBySupplier = existingSupplierMap(existing);
  if (shouldQueueXrefFile(config, context, existingBySupplier, hasTransient)) {
    todo.push({ filePath: context.filePath, code: context.code, targetSuppliers: context.targetSuppliers });
  }
}

function readXrefContext(config, filePath) {
  const readResult = readJsonFile(filePath);
  return readResult.ok ? xrefContextFromPayload(config, filePath, readResult.data) : null;
}

function emitXrefTodo(config, todo, skippedNoMatch) {
  emitPrettyXrefTodo(config, todo, skippedNoMatch);
  if (config.ui === "json") console.log(JSON.stringify({ ts: nowIsoSeconds(), event: "xref_todo", count: todo.length }));
}

function emitPrettyXrefTodo(config, todo, skippedNoMatch) {
  if (config.ui !== "pretty" || !config.progress) return;
  const suffix = config.supplierFilter.size > 0 ? ` (supplier filter, skipped_no_match=${skippedNoMatch})` : "";
  config.progress.note(`Xref todo: ${todo.length} files${suffix}`);
}

function createNextItem(todo) {
  let cursor = 0;
  return () => {
    if (cursor >= todo.length) return null;
    const item = todo[cursor];
    cursor += 1;
    return item;
  };
}

function createXrefStats(config, todo, skippedNoMatch, concurrency) {
  return {
    total: todo.length,
    done: 0,
    inflight: 0,
    concurrency,
    complete: 0,
    incomplete: 0,
    errors: 0,
    skipped_no_match: skippedNoMatch,
    supplier_filter: config.supplierFilterRaw.length ? config.supplierFilterRaw : null,
    codes_filter: config.codesFilterRaw.length ? config.codesFilterRaw : null,
    started_at_ms: Date.now(),
    started_at: nowIsoSeconds(),
  };
}

function xrefCurrentLabel(item) {
  return Array.isArray(item.targetSuppliers) && item.targetSuppliers.length === 1
    ? `${item.code} ${item.targetSuppliers[0]}`
    : item.code;
}

function renderXrefProgress(config, stats, current) {
  if (config.ui !== "pretty" || !config.progress) return;
  config.progress.render(
    buildProgressLine({
      done: stats.done,
      total: stats.total,
      inflight: stats.inflight,
      concurrency: stats.concurrency,
      foundComplete: stats.complete,
      foundIncomplete: stats.incomplete,
      errors: stats.errors,
      startedAtMs: stats.started_at_ms,
      current,
    }),
  );
}

function articleNumberMap(articles) {
  const articleNoBySupplier = new Map();
  for (const article of articles) {
    const supplierName = trimText(article.supplierName);
    const articleNo = trimText(article.articleNo);
    addArticleNo(articleNoBySupplier, supplierName, articleNo);
  }
  return articleNoBySupplier;
}

function addArticleNo(articleNoBySupplier, supplierName, articleNo) {
  if (supplierName && articleNo && !articleNoBySupplier.has(supplierName)) articleNoBySupplier.set(supplierName, articleNo);
}

function selectedSupplierNames(config, targetSuppliers, supplierNamesAll) {
  return Array.isArray(targetSuppliers) && targetSuppliers.length
    ? targetSuppliers
    : supplierNamesAll.filter((supplier) => supplierMatchesFilter(supplier, config.supplierFilter));
}

function countError(errorCounts, error) {
  errorCounts[error] = (errorCounts[error] || 0) + 1;
}

function missingArticleEntry(supplierName, errorCounts) {
  const error = "missing_article_no";
  countError(errorCounts, error);
  return { supplierName, supplierNameVariantsTried: [], error, response: null };
}

function xrefVariants(supplierName) {
  return Array.from(new Set([supplierName, normalizeSupplierName(supplierName)].filter(Boolean)));
}

function successfulXrefResponse(data) {
  if (!data) return null;
  const articles = dedupeCrossRefs((data || {}).articles);
  return articles.length > 0 ? { ...data, articles } : data;
}

function failedVariantState(resp, errorCounts) {
  const error = resp.error || `HTTP ${resp.status || 0}`;
  countError(errorCounts, error);
  return { error, stop: isMaintenanceError(resp), chosen: maintenancePayload(resp) };
}

function isMaintenanceError(resp) {
  return resp.error === "maintenance";
}

function maintenancePayload(resp) {
  return isMaintenanceError(resp) ? resp.data || null : null;
}

async function fetchSupplierXref(client, supplierName, articleNoForSupplier, errorCounts) {
  const variantsTried = [];
  for (const variant of xrefVariants(supplierName)) {
    variantsTried.push(variant);
    const resp = await client.crossReferences(articleNoForSupplier, variant);
    if (resp.ok) return { supplierName, supplierNameVariantsTried: variantsTried, error: null, response: successfulXrefResponse(resp.data) };
    const failed = failedVariantState(resp, errorCounts);
    if (failed.stop) return { supplierName, supplierNameVariantsTried: variantsTried, error: failed.error, response: failed.chosen };
  }
  return { supplierName, supplierNameVariantsTried: variantsTried, error: "failed", response: null };
}

async function supplierXrefEntry(context, supplierName, existingBySupplier, errorCounts) {
  const existingEntry = existingBySupplier.get(supplierName) || null;
  if (!supplierNeedsFetch(context, existingEntry)) return existingEntry;
  const articleNoForSupplier = articleNoForSupplierName(context, supplierName);
  if (!articleNoForSupplier) return missingArticleEntry(supplierName, errorCounts);
  return fetchSupplierXref(context.client, supplierName, articleNoForSupplier, errorCounts);
}

function articleNoForSupplierName(context, supplierName) {
  return trimText(context.articleNoBySupplier.get(supplierName) || context.lookupArticleNo);
}

function supplierNeedsFetch(context, existingEntry) {
  return shouldFetchSupplier({ existingEntry, force: context.force, retryIncomplete: context.retryIncomplete });
}

async function fetchSupplierEntries(context, supplierNames, existingBySupplier, errorCounts) {
  const entries = [];
  for (const supplierName of supplierNames) {
    entries.push(await supplierXrefEntry(context, supplierName, existingBySupplier, errorCounts));
  }
  return entries;
}

function mergeSupplierEntries(existingBySupplier, entries) {
  for (const entry of entries) {
    if (!entry || !entry.supplierName) continue;
    existingBySupplier.set(trimText(entry.supplierName), entry);
  }
  return Array.from(existingBySupplier.values());
}

function enrichArticles(articles, mergedCrossReferencesBySupplier) {
  const bySupplier = existingSupplierMap(mergedCrossReferencesBySupplier);
  return articles.map((article) => ({
    ...article,
    crossReferences: bySupplier.get(trimText(article.supplierName)) || null,
  }));
}

function updateXrefPayload(config, payload, data) {
  payload.tecdoc = payload.tecdoc || {};
  payload.tecdoc.crossReferencesBySupplier = data.mergedCrossReferencesBySupplier;
  payload.tecdoc.articlesEnriched = data.articlesEnriched;
  payload.meta = payload.meta || {};
  payload.meta.xref_fetched_at = nowIsoSeconds();
  payload.meta.xref_duration_ms = data.durationMs;
  payload.meta.xref_complete = !data.mergedCrossReferencesBySupplier.some((entry) => entry && entry.error);
  payload.meta.xref_supplier_count = data.supplierNames.length;
  payload.meta.xref_supplier_count_total = data.supplierNamesAll.length;
  if (config.supplierFilterRaw.length) payload.meta.xref_supplier_filter = config.supplierFilterRaw;
  payload.meta.xref_error_counts = data.errorCounts;
}

function recordWriteResult(stats, payload) {
  if (payload.meta.xref_complete) stats.complete += 1;
  else stats.incomplete += 1;
}

async function enrichXrefPayload(config, client, payload, targetSuppliers, start) {
  const details = articleDetails(payload);
  const articles = detailArticles(details);
  const supplierNamesAll = articleSupplierNames(articles);
  const supplierNames = selectedSupplierNames(config, targetSuppliers, supplierNamesAll);
  const existingBySupplier = existingSupplierMap(existingCrossReferences(payload));
  const errorCounts = {};
  const context = xrefFetchContext(config, client, payload, details, articles);
  const entries = await fetchSupplierEntries(context, supplierNames, existingBySupplier, errorCounts);
  const mergedCrossReferencesBySupplier = mergeSupplierEntries(existingBySupplier, entries);
  updateXrefPayload(config, payload, {
    mergedCrossReferencesBySupplier,
    articlesEnriched: enrichArticles(articles, mergedCrossReferencesBySupplier),
    durationMs: Date.now() - start,
    supplierNames,
    supplierNamesAll,
    errorCounts,
  });
}

function existingCrossReferences(payload) {
  return asListOfObjects(payload?.tecdoc?.crossReferencesBySupplier);
}

function xrefFetchContext(config, client, payload, details, articles) {
  return {
    client,
    force: config.args.values.force,
    retryIncomplete: config.args.values["retry-incomplete"],
    lookupArticleNo: trimText(details?.articleNo || payload.code),
    articleNoBySupplier: articleNumberMap(articles),
  };
}

async function processXrefItem(config, client, stats, item) {
  const currentLabel = xrefCurrentLabel(item);
  stats.inflight += 1;
  renderXrefProgress(config, stats, currentLabel);
  const start = Date.now();
  try {
    const readResult = readJsonFile(item.filePath);
    if (!readResult.ok) {
      stats.errors += 1;
      return;
    }
    const payload = readResult.data;
    await enrichXrefPayload(config, client, payload, item.targetSuppliers, start);
    writeJsonAtomic(item.filePath, payload);
    recordWriteResult(stats, payload);
  } catch {
    stats.errors += 1;
  } finally {
    stats.inflight = Math.max(0, stats.inflight - 1);
    stats.done += 1;
    renderXrefProgress(config, stats, currentLabel);
  }
}

function createXrefWorker(config, client, stats, nextItem) {
  return async () => {
    while (true) {
      const item = nextItem();
      if (!item) return;
      await processXrefItem(config, client, stats, item);
    }
  };
}

function finishXrefRun(config, stats) {
  stats.finished_at = nowIsoSeconds();
  writeJsonAtomic(config.summaryPath, stats);
  finishXrefProgress(config, stats);
}

function finishXrefProgress(config, stats) {
  if (config.ui !== "pretty" || !config.progress) return;
  config.progress.end();
  config.progress.note(`Xref done. complete=${stats.complete} incomplete=${stats.incomplete} err=${stats.errors}`);
}

async function main() {
  const config = resolveXrefConfig(parseXrefArgs());
  if (!validateXrefConfig(config)) return;
  const client = createXrefClient(config);
  const concurrency = Math.max(1, Number.parseInt(config.args.values.concurrency, 10) || 1);
  const { todo, skippedNoMatch } = collectXrefTodo(config);
  emitXrefTodo(config, todo, skippedNoMatch);
  const stats = createXrefStats(config, todo, skippedNoMatch, concurrency);
  const worker = createXrefWorker(config, client, stats, createNextItem(todo));
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  finishXrefRun(config, stats);
}

main().catch(reportFatal);
