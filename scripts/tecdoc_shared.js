const fs = require("node:fs");

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

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

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asListOfObjects(value) {
  return Array.isArray(value) ? value.filter(isPlainObject) : [];
}

function trimText(value) {
  return String(value || "").trim();
}

function uniqueSortedStrings(values) {
  return Array.from(new Set(values.map(trimText).filter(Boolean))).sort();
}

function safeJsonParse(text) {
  try {
    return { ok: true, data: JSON.parse(text) };
  } catch {
    return { ok: false, data: null };
  }
}

function readJsonFile(filePath) {
  try {
    return { ok: true, data: JSON.parse(fs.readFileSync(filePath, "utf8")) };
  } catch (error) {
    return { ok: false, data: null, error };
  }
}

function extractInfoMessage(data) {
  if (typeof data === "string") return data;
  if (!isPlainObject(data)) return "";
  const key = ["info", "message", "error", "detail", "status"].find((name) => {
    const value = data[name];
    return typeof value === "string" && value.trim();
  });
  return key ? data[key].trim() : "";
}

function isMaintenanceOrRateLimitedPayload(data) {
  const msg = extractInfoMessage(data);
  return Boolean(msg) && /maintenance|too many requests|rate limit|try again later|temporarily unavailable/i.test(msg);
}

function writeJsonAtomic(filePath, obj) {
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), "utf8");
  fs.renameSync(tmp, filePath);
}

function createProgressRenderer(options = {}) {
  const { minNoteIntervalMs = 0 } = options;
  let lastLineLen = 0;
  let lastNoteAt = 0;

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
    if (minNoteIntervalMs > 0) {
      const now = Date.now();
      if (now - lastNoteAt < minNoteIntervalMs) return;
      lastNoteAt = now;
    }
    clearLine();
    process.stderr.write(String(msg) + "\n");
  };

  const end = () => clearLine();
  return { render, note, end };
}

function resolveUiMode(requestedUi) {
  if (requestedUi && ["pretty", "json"].includes(requestedUi)) {
    return requestedUi;
  }
  return process.stdout.isTTY ? "pretty" : "json";
}

function progressPercent(done, total) {
  return total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0;
}

function progressBar(percent, width = 28) {
  const filled = Math.round((percent / 100) * width);
  return "[" + "#".repeat(filled) + "-".repeat(Math.max(0, width - filled)) + "]";
}

function formatDurationSeconds(totalSeconds) {
  const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const s = sec % 60;
  const totalMinutes = Math.floor(sec / 60);
  const m = totalMinutes % 60;
  const totalHours = Math.floor(totalMinutes / 60);
  const h = totalHours % 24;
  const d = Math.floor(totalHours / 24);
  if (d > 0) return `${d}d ${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m`;
  if (totalHours > 0) return `${String(totalHours).padStart(2, "0")}h ${String(m).padStart(2, "0")}m`;
  return `${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;
}

function makeRapidApiHeaders(apiKey, apiHost) {
  return {
    "x-rapidapi-key": apiKey,
    "x-rapidapi-host": apiHost,
  };
}

function isTimeoutError(error) {
  return Boolean(error && error.name === "AbortError");
}

function nextBackoffMs(backoffMs) {
  return Math.min(backoffMs * 2, 60_000);
}

function retryAfterMs(response, fallbackMs) {
  const retryAfter = response.headers.get("retry-after");
  return retryAfter && /^\d+$/.test(retryAfter) ? Number.parseInt(retryAfter, 10) * 1000 : fallbackMs;
}

function requestResult(value) {
  return { retry: false, value };
}

function retryResult(backoffMs, error = null) {
  return { retry: true, backoffMs, error };
}

async function handleRetryDelay(waitMs, backoffMs, error = null) {
  await sleep(waitMs);
  return retryResult(nextBackoffMs(backoffMs), error);
}

function fetchResult({ ok, status, data, durationMs, attempt, error }) {
  return { ok, status, data, durationMs, attempt, error };
}

function fetchOk(status, data, durationMs, attempt) {
  return fetchResult({ ok: true, status, data, durationMs, attempt });
}

function fetchError(status, data, durationMs, attempt, error) {
  return fetchResult({ ok: false, status, data, durationMs, attempt, error });
}

function logFetchWarn(logger, event, fields) {
  if (logger && typeof logger.warn === "function") logger.warn(event, fields);
}

function shouldRetryServerStatus(status) {
  return status >= 500 && status <= 599;
}

function shouldReturnClientError(status) {
  return status >= 400 && status < 500;
}

async function handleNotFoundResponse(_ctx, res, durationMs, attempt) {
  return requestResult(fetchOk(res.status, null, durationMs, attempt));
}

async function handleRateLimitedResponse(ctx, res, durationMs, attempt, backoffMs) {
  const waitMs = retryAfterMs(res, backoffMs);
  logFetchWarn(ctx.logger, "tecdoc_rate_limited", { url: ctx.url, attempt, status: res.status, waitMs, durationMs });
  return handleRetryDelay(waitMs, backoffMs, `HTTP ${res.status}`);
}

async function handleServerErrorResponse(ctx, res, durationMs, attempt, backoffMs) {
  logFetchWarn(ctx.logger, "tecdoc_server_error", { url: ctx.url, attempt, status: res.status, durationMs });
  return handleRetryDelay(backoffMs, backoffMs, `HTTP ${res.status}`);
}

async function handleNotOkResponse(ctx, res, durationMs, attempt, backoffMs) {
  const error = `HTTP ${res.status}`;
  logFetchWarn(ctx.logger, "tecdoc_http_error", { url: ctx.url, attempt, status: res.status, durationMs });
  if (shouldReturnClientError(res.status)) return requestResult(fetchError(res.status, null, durationMs, attempt, error));
  return handleRetryDelay(backoffMs, backoffMs, error);
}

async function handleMaintenancePayload(ctx, res, data, durationMs, attempt, backoffMs) {
  if (attempt < ctx.maxRetries) return handleRetryDelay(backoffMs, backoffMs, "maintenance");
  return requestResult(fetchError(res.status, data, durationMs, attempt, "maintenance"));
}

async function handleJsonPayload(ctx, res, data, durationMs, attempt, backoffMs) {
  if (ctx.detectMaintenance && isMaintenanceOrRateLimitedPayload(data)) {
    return handleMaintenancePayload(ctx, res, data, durationMs, attempt, backoffMs);
  }
  return requestResult(fetchOk(res.status, data, durationMs, attempt));
}

async function handleOkResponse(ctx, res, durationMs, attempt, backoffMs) {
  const parsed = safeJsonParse(await res.text());
  if (!parsed.ok) {
    logFetchWarn(ctx.logger, "tecdoc_invalid_json", { url: ctx.url, attempt, status: res.status, durationMs });
    return requestResult(fetchError(res.status, null, durationMs, attempt, "invalid_json"));
  }
  return handleJsonPayload(ctx, res, parsed.data, durationMs, attempt, backoffMs);
}

const HTTP_RESPONSE_HANDLERS = [
  [(res) => res.status === 404, handleNotFoundResponse],
  [(res) => res.status === 429, handleRateLimitedResponse],
  [(res) => shouldRetryServerStatus(res.status), handleServerErrorResponse],
  [(res) => !res.ok, handleNotOkResponse],
  [() => true, handleOkResponse],
];

function httpResponseHandler(res) {
  return HTTP_RESPONSE_HANDLERS.find(([matches]) => matches(res))[1];
}

async function handleHttpResponse(ctx, res, durationMs, attempt, backoffMs) {
  return httpResponseHandler(res)(ctx, res, durationMs, attempt, backoffMs);
}

function shouldSoftFailTimeout(ctx) {
  return isTimeoutError(ctx.error) && ctx.softTimeoutPurpose && ctx.purpose === ctx.softTimeoutPurpose;
}

function timeoutErrorMode(ctx) {
  if (shouldSoftFailTimeout(ctx)) return "soft";
  if (isTimeoutError(ctx.error)) return ctx.timeoutMode;
  return "";
}

function throwAbortTimeout(ctx, attempt, durationMs) {
  throw ctx.createAbortError("timeout", { url: ctx.url, attempt, durationMs });
}

function throwSkipTimeout(ctx, attempt, durationMs) {
  throw ctx.createSkipError("timeout", { url: ctx.url, attempt, durationMs });
}

function timeoutAction(mode) {
  return {
    soft: (ctx, attempt, durationMs) => requestResult(fetchError(0, null, durationMs, attempt, "timeout")),
    "abort-run": throwAbortTimeout,
    "skip-code": throwSkipTimeout,
  }[mode];
}

async function handleFetchError(ctx, attempt, durationMs, backoffMs) {
  const error = isTimeoutError(ctx.error) ? "timeout" : String(ctx.error);
  logFetchWarn(ctx.logger, "tecdoc_request_failed", { url: ctx.url, attempt, error, durationMs });
  const mode = timeoutErrorMode({ ...ctx, error: ctx.error });
  const action = timeoutAction(mode);
  if (action) return action(ctx, attempt, durationMs);
  return handleRetryDelay(backoffMs, backoffMs, error);
}

async function runFetchAttempt(ctx, url, options, attempt, backoffMs) {
  await ctx.rateLimit();
  const start = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ctx.timeoutMs);
  try {
    const res = await fetch(url, { method: "GET", headers: makeRapidApiHeaders(ctx.apiKey, ctx.apiHost), signal: controller.signal });
    return handleHttpResponse({ ...ctx, url, purpose: options.purpose }, res, Date.now() - start, attempt, backoffMs);
  } catch (error) {
    return handleFetchError({ ...ctx, url, error, purpose: options.purpose }, attempt, Date.now() - start, backoffMs);
  } finally {
    clearTimeout(timer);
  }
}

async function fetchTecDocJson(ctx, pathname, options = {}) {
  const url = `${ctx.baseUrl}${pathname}`;
  let backoffMs = 1000;
  let lastError = null;
  for (let attempt = 1; attempt <= ctx.maxRetries; attempt += 1) {
    const result = await runFetchAttempt(ctx, url, options, attempt, backoffMs);
    if (isFinalFetchResult(result)) return result.value;
    backoffMs = result.backoffMs;
    lastError = nextLastFetchError(lastError, result);
  }
  return fetchError(0, null, 0, ctx.maxRetries, lastError);
}

function isFinalFetchResult(result) {
  return !result.retry;
}

function nextLastFetchError(lastError, result) {
  return result.error || result.value?.error || lastError;
}

function dedupeKey(item) {
  return [
    trimText(item.crossManufacturerName),
    trimText(item.crossNumber),
    trimText(item.searchLevel),
  ].join("|");
}

function dedupeCrossRefs(items) {
  const list = asListOfObjects(items);
  const seen = new Set();
  const out = [];
  for (const item of list) {
    const key = dedupeKey(item);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function crossReferencePath(articleNo, supplierName) {
  const encodedArticle = encodeURIComponent(String(articleNo));
  const encodedSupplier = encodeURIComponent(String(supplierName));
  return `/artlookup/search-for-cross-references-through-oem-numbers/article-no/${encodedArticle}/supplierName/${encodedSupplier}`;
}

function reportFatal(error) {
  console.log(JSON.stringify({ ts: nowIsoSeconds(), level: "error", event: "fatal", error: String(error) }));
  process.exitCode = 1;
}

async function rateLimitClient(client) {
  if (!client.minDelayMs) return;
  client._rateQueue = client._rateQueue.then(async () => {
    const now = Date.now();
    const elapsed = now - client._lastRequestAt;
    if (elapsed < client.minDelayMs) await sleep(client.minDelayMs - elapsed);
    client._lastRequestAt = Date.now();
  });
  await client._rateQueue;
}

module.exports = {
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
  safeFilenameFragment,
  trimText,
  uniqueSortedStrings,
  writeJsonAtomic,
};
