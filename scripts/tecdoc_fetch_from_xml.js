#!/usr/bin/env node
/**
 * TecDoc XML Fetcher (Node.js, no dependencies)
 *
 * For each unique <Cod> in an XML file:
 *  1) GET /articles/article-number-details/.../article-no/{code}
 *  2) For each supplierName returned, GET
 *     /artlookup/search-for-cross-references-through-oem-numbers/article-no/{articleNo}/supplierName/{supplierName}
 *
 * Outputs:
 *  - <out>/by_code/<code>.json
 *  - <out>/not_found.jsonl
 *  - <out>/_progress.json
 *  - <out>/summary.json
 *
 * Usage:
 *  RAPIDAPI_KEY=... node scripts/tecdoc_fetch_from_xml.js --xml ART_2026_01_01.xml --resume --limit 20
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
  normalizeSupplierName,
  nowIsoSeconds,
  progressBar,
  progressPercent,
  rateLimitClient,
  reportFatal,
  resolveUiMode,
  safeFilenameFragment,
  trimText,
  uniqueSortedStrings,
  writeJsonAtomic,
} = require("./tecdoc_shared");

class AbortRunError extends Error {
  constructor(message, details) {
    super(message);
    this.name = "AbortRunError";
    this.details = details || {};
  }
}

class SkipCodeError extends Error {
  constructor(message, details) {
    super(message);
    this.name = "SkipCodeError";
    this.details = details || {};
  }
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "unknown";
  return formatDurationSeconds(Math.round(seconds));
}

function padLeft(str, len) {
  const s = String(str);
  if (s.length >= len) return s;
  return " ".repeat(len - s.length) + s;
}

function decodeXmlEntities(input) {
  if (!input) return "";
  return String(input)
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) =>
      String.fromCodePoint(Number.parseInt(hex, 16)),
    )
    .replace(/&#([0-9]+);/g, (_, dec) => String.fromCodePoint(Number.parseInt(dec, 10)));
}

function hasArticles(resp) {
  if (!resp || typeof resp !== "object") return false;
  const articles = asListOfObjects(resp.articles);
  return articles.length > 0;
}

function parseXmlLines(xmlText) {
  // NOTE: This assumes the XML format is stable (Articole/Linie with simple tags).
  // It is intentionally dependency-free.
  const byCode = new Map(); // preserves insertion order for codes
  const orderedCodes = [];

  const lineRe = /<Linie>([\s\S]*?)<\/Linie>/g;
  let match;
  while ((match = lineRe.exec(xmlText))) {
    addXmlLine(byCode, orderedCodes, fieldsFromLineBlock(match[1]));
  }

  return { byCode, orderedCodes };
}

function fieldsFromLineBlock(block) {
  const fields = {};
  const fieldRe = /<([A-Za-z0-9_]+)>([\s\S]*?)<\/\1>/g;
  let match;
  while ((match = fieldRe.exec(block))) {
    fields[match[1]] = decodeXmlEntities(String(match[2] || "").trim());
  }
  return fields;
}

function addXmlLine(byCode, orderedCodes, fields) {
  const code = trimText(fields.Cod);
  if (!code) return;
  if (!byCode.has(code)) byCode.set(code, []);
  byCode.get(code).push(fields);
  orderedCodes.push(code);
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function readJsonIfExists(p) {
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

function parseIsoToMs(iso) {
  if (!iso) return null;
  const ms = Date.parse(String(iso));
  return Number.isFinite(ms) ? ms : null;
}

function appendJsonl(p, obj) {
  fs.appendFileSync(p, `${JSON.stringify(obj)}\n`, "utf8");
}

function writeCodeFile(outFile, payload) {
  // Non-atomic is fine here (per-code file), but keep it simple and consistent.
  fs.writeFileSync(outFile, JSON.stringify(payload, null, 2), "utf8");
}

function createLogger({ logPath, ui, progress }) {
  const logLine = (level, event, fields = {}) => {
    const payload = { ts: nowIsoSeconds(), level, event, ...fields };
    const line = JSON.stringify(payload);
    writeLoggerOutput({ ui, progress, level, event, fields, line });
    appendLogFile(logPath, line);
  };
  return {
    info: (event, fields) => logLine("info", event, fields),
    warn: (event, fields) => logLine("warn", event, fields),
    error: (event, fields) => logLine("error", event, fields),
  };
}

function appendLogFile(logPath, line) {
  if (logPath) fs.appendFileSync(logPath, `${line}\n`, "utf8");
}

function prettySummaryMessage(event, fields) {
  const messages = {
    xml_parse_done: `Parsed XML: total_lines=${fields.total_lines} unique_codes=${fields.unique_codes}`,
    run_done: `Done: found=${fields.found} not_found=${fields.not_found} errors=${fields.errors} skipped=${fields.skipped}`,
  };
  return messages[event] || "";
}

function writePrettySummary(progress, message) {
  if (!message) return;
  if (progress) progress.note(message);
  else console.log(message);
}

function requestFailureExtra(event, fields) {
  const builder = REQUEST_EXTRA_BUILDERS[event] || emptyExtra;
  return builder(fields);
}

const REQUEST_EXTRA_BUILDERS = {
  tecdoc_request_failed: (fields) => ` error=${fields.error || "?"} attempt=${fields.attempt || "?"} url=${String(fields.url || "").slice(0, 120)}`,
};

function emptyExtra() {
  return "";
}

function fieldCodeSuffix(fields) {
  return fields && fields.code ? ` code=${fields.code}` : "";
}

function writePrettyWarning(progress, level, event, fields) {
  if (!progress || level === "info") return;
  progress.note(`[${level}] ${event}${fieldCodeSuffix(fields)}${requestFailureExtra(event, fields)}`);
}

function writeLoggerOutput({ ui, progress, level, event, fields, line }) {
  if (ui === "json") {
    console.log(line);
    return;
  }
  if (ui !== "pretty") return;
  writePrettySummary(progress, prettySummaryMessage(event, fields));
  writePrettyWarning(progress, level, event, fields);
}

function buildProgressLine({ total, code, stats, startedAtMs, recentDurationsMs }) {
  const handled = stats.processed + stats.skipped;
  const pct = progressPercent(handled, total);
  const bar = progressBar(pct);

  const avgMs = averageDuration(recentDurationsMs);
  const remaining = Math.max(0, total - handled);
  const concurrency = Math.max(1, Number(stats.concurrency) || 1);
  const etaSeconds = etaFromAverage(avgMs, remaining, concurrency);
  const elapsedSeconds = (Date.now() - startedAtMs) / 1000;

  const prefix = `${padLeft(handled, String(total).length)}/${total} ${padLeft(pct, 3)}% ${bar}`;
  const counters = `found=${stats.found} nf=${stats.not_found} err=${stats.errors} skip=${stats.skipped}`;
  const inflight = inflightText(stats.inflight, concurrency);
  const eta = `eta=${formatDuration(etaSeconds)} elapsed=${formatDuration(elapsedSeconds)}`;
  const current = currentCodeText(code);
  const calls = `xref_calls=${stats.cross_ref_calls} xref_to=${stats.cross_ref_timeouts || 0}`;
  return [prefix, counters, calls, inflight, eta, current].filter(Boolean).join(" | ");
}

function averageDuration(recentDurationsMs) {
  return recentDurationsMs.length ? recentDurationsMs.reduce((a, b) => a + b, 0) / recentDurationsMs.length : null;
}

function etaFromAverage(avgMs, remaining, concurrency) {
  return avgMs ? (avgMs * remaining) / 1000 / concurrency : Number.NaN;
}

function inflightText(inflight, concurrency) {
  return inflight ? `inflight=${inflight}/${concurrency}` : "";
}

function currentCodeText(code) {
  return code ? `code=${String(code).slice(0, 24)}` : "";
}

class TecDocClient {
  constructor({
    apiKey,
    apiHost,
    typeId,
    langId,
    countryFilterId,
    rps,
    timeoutMs,
    maxRetries,
    logger,
    timeoutMode,
  }) {
    this.apiKey = apiKey;
    this.apiHost = apiHost;
    this.baseUrl = `https://${apiHost}`;
    this.typeId = typeId;
    this.langId = langId;
    this.countryFilterId = countryFilterId;
    this.minDelayMs = rps > 0 ? Math.ceil(1000 / rps) : 0;
    this.timeoutMs = timeoutMs;
    this.maxRetries = maxRetries;
    this.logger = logger;
    this.timeoutMode = timeoutMode || "abort-run"; // abort-run | skip-code | continue
    this._lastRequestAt = 0;
    this._rateQueue = Promise.resolve();
  }

  async _rateLimit() {
    await rateLimitClient(this);
  }

  async _getJson(pathname, { purpose } = {}) {
    return fetchTecDocJson({
      apiKey: this.apiKey,
      apiHost: this.apiHost,
      baseUrl: this.baseUrl,
      maxRetries: this.maxRetries,
      timeoutMs: this.timeoutMs,
      timeoutMode: this.timeoutMode,
      logger: this.logger,
      rateLimit: () => this._rateLimit(),
      softTimeoutPurpose: "xref",
      createAbortError: (message, details) => new AbortRunError(message, details),
      createSkipError: (message, details) => new SkipCodeError(message, details),
    }, pathname, { purpose });
  }

  async articleNumberDetails(articleNo) {
    const encoded = encodeURIComponent(String(articleNo));
    const p = `/articles/article-number-details/type-id/${this.typeId}/lang-id/${this.langId}/country-filter-id/${this.countryFilterId}/article-no/${encoded}`;
    return this._getJson(p, { purpose: "details" });
  }

  async crossReferences(articleNo, supplierName) {
    return this._getJson(crossReferencePath(articleNo, supplierName), { purpose: "xref" });
  }
}

async function resolveDetails(client, code) {
  const details = await client.articleNumberDetails(code);
  return { details };
}

function parseFetchArgs() {
  return parseArgs({
    options: {
      xml: { type: "string", default: path.join(process.cwd(), "ART_2026_01_01.xml") },
      out: { type: "string", default: path.join(process.cwd(), "tecdoc_data", "art_2026_01_01_js") },
      "api-key": { type: "string", default: process.env.RAPIDAPI_KEY || "" },
      "type-id": { type: "string", default: "1" },
      "lang-id": { type: "string", default: "21" },
      "country-filter-id": { type: "string", default: "63" },
      rps: { type: "string", default: "5" },
      timeout: { type: "string", default: "20" },
      retries: { type: "string", default: "1" },
      concurrency: { type: "string", default: "5" },
      limit: { type: "string", default: "0" },
      resume: { type: "boolean", default: true },
      force: { type: "boolean", default: false },
      codes: { type: "string", default: "" },
      ui: { type: "string", default: "" },
      status: { type: "boolean", default: false },
      "write-remaining": { type: "string", default: "" },
      "continue-on-timeout": { type: "boolean", default: false },
      "skip-code-on-timeout": { type: "boolean", default: true },
      "skip-xref": { type: "boolean", default: true },
      "dry-run": { type: "boolean", default: false },
    },
    allowPositionals: false,
  });
}

function resolveFetchConfig(args) {
  const xmlPath = path.resolve(args.values.xml);
  const outDir = path.resolve(args.values.out);
  const ui = resolveUiMode(args.values.ui);
  const progress = ui === "pretty" ? createProgressRenderer({ minNoteIntervalMs: 750 }) : null;
  return {
    args,
    xmlPath,
    outDir,
    byCodeDir: path.join(outDir, "by_code"),
    progressPath: path.join(outDir, "_progress.json"),
    notFoundPath: path.join(outDir, "not_found.jsonl"),
    summaryPath: path.join(outDir, "summary.json"),
    logPath: path.join(outDir, "tecdoc_fetch_xml_js.log"),
    ui,
    progress,
  };
}

function prepareFetchDirs(config) {
  ensureDir(config.outDir);
  ensureDir(config.byCodeDir);
}

function validateFetchConfig(config, logger) {
  if (missingXml(config, logger)) return false;
  if (missingApiKey(config, logger)) return false;
  return true;
}

function missingXml(config, logger) {
  if (fs.existsSync(config.xmlPath)) return false;
    logger.error("xml_missing", { path: config.xmlPath });
    process.exitCode = 2;
  return true;
}

function missingApiKey(config, logger) {
  if (!requiresApiKey(config)) return false;
  if (config.args.values["api-key"]) return false;
    logger.error("missing_api_key", { hint: "Pass --api-key or set RAPIDAPI_KEY env var" });
    process.exitCode = 2;
  return true;
}

function requiresApiKey(config) {
  return !config.args.values["dry-run"] && !config.args.values.status;
}

function parseFetchXml(config, logger) {
  logger.info("xml_parse_start", { path: config.xmlPath });
  const xmlText = fs.readFileSync(config.xmlPath, "utf8");
  const parsed = parseXmlLines(xmlText);
  logger.info("xml_parse_done", { total_lines: parsed.orderedCodes.length, unique_codes: parsed.byCode.size });
  return parsed;
}

function codesFilterFromArgs(args) {
  return args.values.codes ? new Set(args.values.codes.split(",").map((s) => s.trim()).filter(Boolean)) : null;
}

function targetCodes(uniqueCodes, limit) {
  return limit > 0 ? uniqueCodes.slice(0, limit) : uniqueCodes;
}

function loadProgress(config) {
  const progress = progressSnapshot(config);
  return {
    processedCodes: progressProcessedCodes(progress),
    timeouts: progressTimeouts(progress),
  };
}

function progressProcessedCodes(progress) {
  const processedCodes = new Set();
  for (const code of progress.processed_codes || []) processedCodes.add(String(code));
  return processedCodes;
}

function progressTimeouts(progress) {
  const timeouts = new Map();
  for (const [code, meta] of Object.entries(progress.timeouts || {})) addTimeoutMeta(timeouts, code, meta);
  return timeouts;
}

function progressSnapshot(config) {
  return config.args.values.resume ? readJsonIfExists(config.progressPath) || {} : {};
}

function addTimeoutMeta(timeouts, code, meta) {
  if (code && meta && typeof meta === "object") timeouts.set(String(code), meta);
}

function codeAllowed(code, codesFilter) {
  return !codesFilter || codesFilter.has(trimText(code));
}

function codeOutputPath(config, code) {
  return path.join(config.byCodeDir, `${safeFilenameFragment(code)}.json`);
}

function codeAlreadyDone(config, state, code) {
  return state.processedCodes.has(code) || fs.existsSync(codeOutputPath(config, code));
}

function timeoutStillCoolingDown(meta) {
  const nextRetryMs = parseIsoToMs(meta?.next_retry_at);
  return Boolean(nextRetryMs && Date.now() < nextRetryMs);
}

function shouldSkipFetchCode(config, state, code) {
  const timeoutMeta = state.timeouts.get(code);
  return shouldSkipTimeout(config, timeoutMeta) || shouldSkipExistingCode(config, state, code);
}

function shouldSkipTimeout(config, timeoutMeta) {
  return Boolean(timeoutMeta && !config.args.values.force && timeoutStillCoolingDown(timeoutMeta));
}

function shouldSkipExistingCode(config, state, code) {
  return !config.args.values.force && codeAlreadyDone(config, state, code);
}

function buildFetchTodo(config, state, codesToProcess, codesFilter, stats) {
  const todo = [];
  for (let i = 0; i < codesToProcess.length; i += 1) {
    const code = trimText(codesToProcess[i]);
    if (!codeAllowed(code, codesFilter)) continue;
    if (shouldSkipFetchCode(config, state, code)) {
      stats.skipped += 1;
      continue;
    }
    todo.push({ code, idx: i + 1 });
  }
  return todo;
}

function createFetchClient(config, logger) {
  const args = config.args;
  return new TecDocClient({
    apiKey: args.values["api-key"],
    apiHost: "tecdoc-catalog.p.rapidapi.com",
    typeId: intArg(args, "type-id", 1),
    langId: intArg(args, "lang-id", 21),
    countryFilterId: intArg(args, "country-filter-id", 63),
    rps: floatArg(args, "rps", 1),
    timeoutMs: intArg(args, "timeout", 60) * 1000,
    maxRetries: intArg(args, "retries", 6),
    logger,
    timeoutMode: timeoutModeFromArgs(args),
  });
}

function intArg(args, name, fallback) {
  return Number.parseInt(args.values[name], 10) || fallback;
}

function floatArg(args, name, fallback) {
  return Number.parseFloat(args.values[name]) || fallback;
}

function timeoutModeFromArgs(args) {
  if (args.values["skip-code-on-timeout"]) return "skip-code";
  if (args.values["continue-on-timeout"]) return "continue";
  return "abort-run";
}

function createFetchStats(codesToProcess, concurrency) {
  return {
    total_unique_codes: codesToProcess.length,
    processed: 0,
    skipped: 0,
    found: 0,
    not_found: 0,
    errors: 0,
    cross_ref_calls: 0,
    cross_ref_timeouts: 0,
    inflight: 0,
    concurrency,
    started_at: nowIsoSeconds(),
  };
}

function createProgressSaver(config, state) {
  const saveProgress = () => {
    const timeoutsObj = {};
    for (const [code, meta] of state.timeouts.entries()) timeoutsObj[code] = meta;
    writeJsonAtomic(config.progressPath, {
      processed_codes: Array.from(state.processedCodes),
      timeouts: timeoutsObj,
      updated_at: nowIsoSeconds(),
    });
  };
  let chain = Promise.resolve();
  return {
    saveProgress,
    saveLocked: () => {
      chain = chain.then(async () => saveProgress());
      return chain;
    },
  };
}

function renderFetchProgress(config, state, stats, startedAtMs, recentDurationsMs) {
  if (config.ui !== "pretty" || !config.progress || !state.uiState.idx) return;
  config.progress.render(buildProgressLine({
    total: state.codesToProcess.length,
    code: state.uiState.code,
    stats,
    startedAtMs,
    recentDurationsMs,
  }));
}

function pushDuration(recentDurationsMs, ms) {
  if (!Number.isFinite(ms) || ms <= 0) return;
  recentDurationsMs.push(ms);
  if (recentDurationsMs.length > 50) recentDurationsMs.shift();
}

function createNextFetchItem(todo) {
  let cursor = 0;
  return () => {
    if (cursor >= todo.length) return null;
    const item = todo[cursor];
    cursor += 1;
    return item;
  };
}

function markNoArticles(config, state, stats, code, details, byCode) {
  stats.not_found += 1;
  stats.processed += 1;
  state.processedCodes.add(code);
  state.timeouts.delete(code);
  appendJsonl(config.notFoundPath, {
    code,
    reason: "no_articles",
    status: details.status ?? null,
    inputLines: byCode.get(code) || [],
    fetched_at: nowIsoSeconds(),
  });
}

async function fetchCrossReferences(config, client, stats, supplierNames, lookupArticleNo, disabledXrefVariants) {
  const crossReferencesBySupplier = [];
  const bySupplier = new Map();
  if (config.args.values["skip-xref"]) return { crossReferencesBySupplier, bySupplier };
  for (const supplierName of supplierNames) {
    const entry = await fetchSupplierCrossRefs(client, stats, supplierName, lookupArticleNo, disabledXrefVariants);
    crossReferencesBySupplier.push(entry);
    bySupplier.set(supplierName, entry);
  }
  return { crossReferencesBySupplier, bySupplier };
}

async function fetchSupplierCrossRefs(client, stats, supplierName, lookupArticleNo, disabledXrefVariants) {
  const variantsTried = [];
  let chosenResponse = null;
  for (const variant of [supplierName, normalizeSupplierName(supplierName)].filter(Boolean)) {
    const result = await fetchCrossRefVariant(client, stats, lookupArticleNo, variant, disabledXrefVariants);
    variantsTried.push(result.variantLabel);
    if (shouldReturnCrossRefResult(result)) return { supplierName, supplierNameVariantsTried: variantsTried, response: result.response };
    chosenResponse = nextChosenCrossRefResponse(chosenResponse, result);
  }
  return { supplierName, supplierNameVariantsTried: variantsTried, response: chosenResponse };
}

function shouldReturnCrossRefResult(result) {
  return Boolean(result.done);
}

function nextChosenCrossRefResponse(chosenResponse, result) {
  return result.response || chosenResponse;
}

async function fetchCrossRefVariant(client, stats, lookupArticleNo, variant, disabledXrefVariants) {
  if (disabledXrefVariants.has(variant)) {
    return { variantLabel: `${variant} (skipped_timeout)`, skip: true };
  }
  stats.cross_ref_calls += 1;
  const resp = await client.crossReferences(lookupArticleNo, variant);
  if (isCrossRefTimeout(resp)) {
    stats.cross_ref_timeouts += 1;
    disabledXrefVariants.add(variant);
    return { variantLabel: variant, timeout: true };
  }
  return crossRefVariantResponse(variant, resp);
}

function isCrossRefTimeout(resp) {
  return Boolean(resp && resp.error === "timeout");
}

function crossRefVariantResponse(variant, resp) {
  const respArticles = dedupeCrossRefs((resp.data || {}).articles);
  if (!isSuccessfulDataResponse(resp)) return { variantLabel: variant, response: null };
  return {
    variantLabel: variant,
    done: respArticles.length > 0,
    response: { ...resp.data, articles: respArticles },
  };
}

function isSuccessfulDataResponse(resp) {
  return Boolean(resp.ok && resp.data);
}

function buildArticlesEnriched(config, articles, bySupplier) {
  return articles.map((article) => {
    const supplierName = trimText(article.supplierName);
    return {
      ...article,
      crossReferences: articleCrossReferences(config, bySupplier, supplierName),
    };
  });
}

function articleCrossReferences(config, bySupplier, supplierName) {
  if (config.args.values["skip-xref"]) return null;
  return (bySupplier.get(supplierName) || {}).response || null;
}

function buildCodePayload({ client, code, byCode, detailsData, crossReferencesBySupplier, articlesEnriched, start }) {
  return {
    code,
    outcome: "found",
    inputLines: byCode.get(code) || [],
    tecdoc: {
      articleNumberDetails: detailsData,
      crossReferencesBySupplier,
      articlesEnriched,
    },
    meta: {
      fetched_at: nowIsoSeconds(),
      duration_ms: Date.now() - start,
      lang_id: client.langId,
      country_filter_id: client.countryFilterId,
      type_id: client.typeId,
    },
  };
}

function markCodeFound(config, state, stats, code, payload) {
  writeCodeFile(codeOutputPath(config, code), payload);
  stats.found += 1;
  stats.processed += 1;
  state.processedCodes.add(code);
  state.timeouts.delete(code);
}

function markTimedOutCode(config, state, stats, code, err, byCode, retryDelaySeconds) {
  stats.not_found += 1;
  stats.processed += 1;
  const prev = state.timeouts.get(code) || {};
  const attempts = (Number(prev.attempts) || 0) + 1;
  state.timeouts.set(code, {
    attempts,
    last_timeout_at: nowIsoSeconds(),
    next_retry_at: new Date(Date.now() + retryDelaySeconds * 1000).toISOString(),
    last_url: timeoutLastUrl(err, prev),
  });
  appendJsonl(config.notFoundPath, timeoutJsonLine(code, err, byCode));
}

function timeoutLastUrl(err, prev) {
  return (err.details && err.details.url) || prev.last_url || null;
}

function timeoutJsonLine(code, err, byCode) {
  return { code, reason: "timeout", timeout: err.details || {}, inputLines: byCode.get(code) || [], fetched_at: nowIsoSeconds() };
}

function markErroredCode(config, state, stats, code, err, byCode) {
  stats.errors += 1;
  stats.processed += 1;
  state.processedCodes.add(code);
  state.timeouts.delete(code);
  appendJsonl(config.notFoundPath, { code, reason: "error", error: String(err), inputLines: byCode.get(code) || [], fetched_at: nowIsoSeconds() });
}

async function processFetchItem(context, item) {
  const { state, stats } = context;
  const { code, idx } = item;
  state.uiState.idx = idx;
  state.uiState.code = code;
  stats.inflight += 1;
  const start = Date.now();
  try {
    await processFetchDetails(context, code, start);
  } catch (err) {
    markFetchError(context, code, err);
  } finally {
    await finalizeFetchItem(context, start);
  }
}

async function processFetchDetails(context, code, start) {
  const { config, state, client, stats, byCode, disabledXrefVariants } = context;
  const { details } = await resolveDetails(client, code);
  const detailsData = details.data;
  if (!details.ok || !hasArticles(detailsData)) return markNoArticles(config, state, stats, code, details, byCode);
  const articles = asListOfObjects(detailsData.articles);
  const supplierNames = uniqueSortedStrings(articles.map((article) => article.supplierName));
  const lookupArticleNo = trimText(detailsData.articleNo) || code;
  const { crossReferencesBySupplier, bySupplier } = await fetchCrossReferences(config, client, stats, supplierNames, lookupArticleNo, disabledXrefVariants);
  const articlesEnriched = buildArticlesEnriched(config, articles, bySupplier);
  const payload = buildCodePayload({ client, code, byCode, detailsData, crossReferencesBySupplier, articlesEnriched, start });
  markCodeFound(config, state, stats, code, payload);
}

function markFetchError(context, code, err) {
  const { config, state, stats, byCode } = context;
  if (err instanceof SkipCodeError) {
    markTimedOutCode(config, state, stats, code, err, byCode, context.retryDelaySeconds);
    return;
  }
  markErroredCode(config, state, stats, code, err, byCode);
}

async function finalizeFetchItem(context, start) {
  const { state, stats, recentDurationsMs, saver } = context;
  stats.inflight = Math.max(0, stats.inflight - 1);
  pushDuration(recentDurationsMs, Date.now() - start);
  state.sinceSave += 1;
  if (state.sinceSave >= 10) {
    state.sinceSave = 0;
    await saver.saveLocked();
  }
}

function createFetchWorker(context, nextItem) {
  return async () => {
    while (!context.state.runAborted) {
      const item = nextItem();
      if (!item) return;
      const keepGoing = await runFetchWorkerItem(context, item);
      if (!keepGoing) return;
    }
  };
}

async function runFetchWorkerItem(context, item) {
  try {
    await processFetchItem(context, item);
    return true;
  } catch (err) {
    return handleFetchWorkerError(context, item, err);
  }
}

function handleFetchWorkerError(context, item, err) {
  if (!(err instanceof AbortRunError)) return true;
  context.state.runAborted = true;
  context.state.aborted = {
    code: item.code,
    reason: err.message || "aborted",
    details: err.details || {},
    aborted_at: nowIsoSeconds(),
  };
  process.exitCode = 3;
  return false;
}

function finishFetchRun(config, state, stats, saver, logger) {
  saver.saveProgress();
  stats.finished_at = nowIsoSeconds();
  if (state.aborted) stats.aborted = state.aborted;
  writeJsonAtomic(config.summaryPath, stats);
  logger.info("run_done", stats);
  finishPrettyFetchProgress(config, state);
}

function finishPrettyFetchProgress(config, state) {
  if (config.ui !== "pretty" || !config.progress) return;
  if (state.heartbeat) clearInterval(state.heartbeat);
  config.progress.end();
}

function countStatusCodes(config, state, codesToProcess, codesFilter) {
  let existingCount = 0;
  let remainingCount = 0;
  for (const code of codesToProcess) {
    if (!codeAllowed(code, codesFilter)) continue;
    if (codeAlreadyDone(config, state, String(code))) existingCount += 1;
    else remainingCount += 1;
  }
  return { existingCount, remainingCount };
}

function remainingStatusCodes(config, state, codesToProcess, codesFilter) {
  const remaining = [];
  for (const code of codesToProcess) {
    if (!codeAllowed(code, codesFilter)) continue;
    if (codeAlreadyDone(config, state, String(code))) continue;
    remaining.push(String(code));
  }
  return remaining;
}

function statusSummary(config, state, orderedCodes, codesToProcess, counts) {
  return {
    xml: config.xmlPath,
    out: config.outDir,
    total_lines: orderedCodes.length,
    unique_codes: state.uniqueCodeCount,
    target_codes: codesToProcess.length,
    already_done: counts.existingCount,
    remaining: counts.remainingCount,
    timeouts_scheduled: state.timeouts.size,
    not_found_file: config.notFoundPath,
    progress_file: config.progressPath,
    by_code_dir: config.byCodeDir,
  };
}

function writeRemainingStatus(config, state, summary, codesToProcess, codesFilter) {
  if (!config.args.values["write-remaining"]) return;
  const remaining = remainingStatusCodes(config, state, codesToProcess, codesFilter);
  fs.writeFileSync(path.resolve(config.args.values["write-remaining"]), remaining.join("\n") + "\n", "utf8");
  summary.remaining_written_to = path.resolve(config.args.values["write-remaining"]);
  summary.remaining_written_count = remaining.length;
}

function printStatus(config, summary) {
  if (config.ui === "json") {
    console.log(JSON.stringify({ ts: nowIsoSeconds(), level: "info", event: "status", ...summary }));
    return;
  }
  console.log(`XML: ${summary.xml}`);
  console.log(`Out: ${summary.out}`);
  console.log(`Codes: unique=${summary.unique_codes} target=${summary.target_codes}`);
  console.log(`Done: ${summary.already_done} | Remaining: ${summary.remaining}`);
  if (summary.remaining_written_to) console.log(`Remaining list: ${summary.remaining_written_to}`);
}

function renderStatus(config, state, orderedCodes, codesToProcess, codesFilter) {
  const counts = countStatusCodes(config, state, codesToProcess, codesFilter);
  const summary = statusSummary(config, state, orderedCodes, codesToProcess, counts);
  writeRemainingStatus(config, state, summary, codesToProcess, codesFilter);
  printStatus(config, summary);
}

function createFetchState(config, byCode, codesToProcess) {
  return {
    ...loadProgress(config),
    codesToProcess,
    uniqueCodeCount: byCode.size,
    uiState: { idx: 0, code: "" },
    sinceSave: 0,
    runAborted: false,
    aborted: null,
    heartbeat: null,
  };
}

function startHeartbeat(config, state, stats, startedAtMs, recentDurationsMs) {
  if (config.ui !== "pretty" || !config.progress) return;
  state.heartbeat = setInterval(() => renderFetchProgress(config, state, stats, startedAtMs, recentDurationsMs), 1000);
  state.heartbeat.unref?.();
}

async function runFetchQueue(config, state, client, stats, byCode, todo) {
  const recentDurationsMs = [];
  startHeartbeat(config, state, stats, Date.now(), recentDurationsMs);
  const saver = createProgressSaver(config, state);
  const context = {
    config,
    state,
    client,
    stats,
    byCode,
    disabledXrefVariants: new Set(),
    recentDurationsMs,
    saver,
    retryDelaySeconds: 60 * 60,
  };
  const worker = createFetchWorker(context, createNextFetchItem(todo));
  await Promise.all(Array.from({ length: stats.concurrency }, () => worker()));
  await saver.saveLocked();
  return saver;
}

async function main() {
  const config = resolveFetchConfig(parseFetchArgs());
  prepareFetchDirs(config);
  const logger = createLogger({ logPath: config.logPath, ui: config.ui, progress: config.progress });
  if (!validateFetchConfig(config, logger)) return;
  const { byCode, orderedCodes } = parseFetchXml(config, logger);
  if (config.args.values["dry-run"]) return;
  await runFetchMain(config, logger, byCode, orderedCodes);
}

async function runFetchMain(config, logger, byCode, orderedCodes) {
  const limit = Number.parseInt(config.args.values.limit, 10) || 0;
  const codesToProcess = targetCodes(Array.from(byCode.keys()), limit);
  const codesFilter = codesFilterFromArgs(config.args);
  const state = createFetchState(config, byCode, codesToProcess);
  if (config.args.values.status) return renderStatus(config, state, orderedCodes, codesToProcess, codesFilter);
  const client = createFetchClient(config, logger);
  const concurrency = Math.max(1, Number.parseInt(config.args.values.concurrency, 10) || 1);
  const stats = createFetchStats(codesToProcess, concurrency);
  const todo = buildFetchTodo(config, state, codesToProcess, codesFilter, stats);
  const saver = await runFetchQueue(config, state, client, stats, byCode, todo);
  finishFetchRun(config, state, stats, saver, logger);
}

main().catch(reportFatal);
