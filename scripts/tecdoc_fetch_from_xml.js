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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function nowIsoSeconds() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "unknown";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m${String(r).padStart(2, "0")}s`;
  return `${r}s`;
}

function padLeft(str, len) {
  const s = String(str);
  if (s.length >= len) return s;
  return " ".repeat(len - s.length) + s;
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

function asListOfObjects(value) {
  return Array.isArray(value) ? value.filter((x) => x && typeof x === "object" && !Array.isArray(x)) : [];
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
    const block = match[1];
    const fields = {};

    const fieldRe = /<([A-Za-z0-9_]+)>([\s\S]*?)<\/\1>/g;
    let m2;
    while ((m2 = fieldRe.exec(block))) {
      const tag = m2[1];
      const rawValue = m2[2];
      fields[tag] = decodeXmlEntities(String(rawValue || "").trim());
    }

    const code = String(fields.Cod || "").trim();
    if (!code) continue;

    if (!byCode.has(code)) byCode.set(code, []);
    byCode.get(code).push(fields);
    orderedCodes.push(code);
  }

  return { byCode, orderedCodes };
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

function writeJsonAtomic(p, obj) {
  const tmp = `${p}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), "utf8");
  fs.renameSync(tmp, p);
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
    if (ui === "json") {
      console.log(line);
    } else if (ui === "pretty") {
      // In pretty mode, keep stdout clean; show only high-signal messages.
      if (event === "xml_parse_done" || event === "run_done") {
        const msg =
          event === "xml_parse_done"
            ? `Parsed XML: total_lines=${fields.total_lines} unique_codes=${fields.unique_codes}`
            : `Done: found=${fields.found} not_found=${fields.not_found} errors=${fields.errors} skipped=${fields.skipped}`;
        if (progress) progress.note(msg);
        else console.log(msg);
      }
    }
    if (logPath) fs.appendFileSync(logPath, `${line}\n`, "utf8");
    if (ui === "pretty" && progress && level !== "info") {
      const extra =
        event === "tecdoc_request_failed"
          ? ` error=${fields.error || "?"} attempt=${fields.attempt || "?"} url=${String(fields.url || "").slice(0, 120)}`
          : "";
      progress.note(
        `[${level}] ${event}${fields && fields.code ? ` code=${fields.code}` : ""}${extra}`,
      );
    }
  };
  return {
    info: (event, fields) => logLine("info", event, fields),
    warn: (event, fields) => logLine("warn", event, fields),
    error: (event, fields) => logLine("error", event, fields),
  };
}

function createProgressRenderer() {
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
    const now = Date.now();
    // Avoid spamming notes.
    if (now - lastNoteAt < 750) return;
    lastNoteAt = now;
    clearLine();
    process.stderr.write(String(msg) + "\n");
  };

  const end = () => {
    clearLine();
  };

  return { render, note, end };
}

function buildProgressLine({ idx, total, code, stats, startedAtMs, recentDurationsMs }) {
  const handled = stats.processed + stats.skipped;
  const pct = total > 0 ? Math.min(100, Math.floor((handled / total) * 100)) : 0;
  const width = 28;
  const filled = Math.round((pct / 100) * width);
  const bar = "[" + "#".repeat(filled) + "-".repeat(Math.max(0, width - filled)) + "]";

  const avgMs =
    recentDurationsMs.length > 0
      ? recentDurationsMs.reduce((a, b) => a + b, 0) / recentDurationsMs.length
      : null;
  const remaining = Math.max(0, total - handled);
  const concurrency = Math.max(1, Number(stats.concurrency) || 1);
  const etaSeconds = avgMs ? (avgMs * remaining) / 1000 / concurrency : Number.NaN;
  const elapsedSeconds = (Date.now() - startedAtMs) / 1000;

  const prefix = `${padLeft(handled, String(total).length)}/${total} ${padLeft(pct, 3)}% ${bar}`;
  const counters = `found=${stats.found} nf=${stats.not_found} err=${stats.errors} skip=${stats.skipped}`;
  const inflight = stats.inflight ? `inflight=${stats.inflight}/${concurrency}` : "";
  const eta = `eta=${formatDuration(etaSeconds)} elapsed=${formatDuration(elapsedSeconds)}`;
  const current = code ? `code=${String(code).slice(0, 24)}` : "";
  const calls = `xref_calls=${stats.cross_ref_calls} xref_to=${stats.cross_ref_timeouts || 0}`;
  return [prefix, counters, calls, inflight, eta, current].filter(Boolean).join(" | ");
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
    if (!this.minDelayMs) return;
    // Serialize rate-limiting across concurrent requests.
    this._rateQueue = this._rateQueue.then(async () => {
      const now = Date.now();
      const elapsed = now - this._lastRequestAt;
      if (elapsed < this.minDelayMs) await sleep(this.minDelayMs - elapsed);
      this._lastRequestAt = Date.now();
    });
    await this._rateQueue;
  }

  async _getJson(pathname, { purpose } = {}) {
    const url = `${this.baseUrl}${pathname}`;
    let backoffMs = 1000;
    let lastError = null;

    for (let attempt = 1; attempt <= this.maxRetries; attempt += 1) {
      await this._rateLimit();
      const start = Date.now();
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), this.timeoutMs);

      try {
        const res = await fetch(url, {
          method: "GET",
          headers: {
            "x-rapidapi-key": this.apiKey,
            "x-rapidapi-host": this.apiHost,
          },
          signal: controller.signal,
        });
        const durationMs = Date.now() - start;

        if (res.status === 404) return { ok: true, status: 404, data: null, durationMs, attempt };

        if (res.status === 429) {
          const retryAfter = res.headers.get("retry-after");
          const waitMs =
            retryAfter && /^\d+$/.test(retryAfter) ? Number.parseInt(retryAfter, 10) * 1000 : backoffMs;
          this.logger.warn("tecdoc_rate_limited", { url, attempt, status: res.status, waitMs, durationMs });
          await sleep(waitMs);
          backoffMs = Math.min(backoffMs * 2, 60_000);
          continue;
        }

        if (res.status >= 500 && res.status <= 599) {
          this.logger.warn("tecdoc_server_error", { url, attempt, status: res.status, durationMs });
          await sleep(backoffMs);
          backoffMs = Math.min(backoffMs * 2, 60_000);
          continue;
        }

        if (!res.ok) {
          lastError = `HTTP ${res.status}`;
          this.logger.warn("tecdoc_http_error", { url, attempt, status: res.status, durationMs });
          // For 4xx (other than 404/429), don't hammer retries.
          if (res.status >= 400 && res.status < 500) return { ok: false, status: res.status, data: null, durationMs, attempt, error: lastError };
          await sleep(backoffMs);
          backoffMs = Math.min(backoffMs * 2, 60_000);
          continue;
        }

        const text = await res.text();
        try {
          const data = JSON.parse(text);
          return { ok: true, status: res.status, data, durationMs, attempt };
        } catch {
          lastError = "invalid_json";
          this.logger.warn("tecdoc_invalid_json", { url, attempt, status: res.status, durationMs });
          return { ok: false, status: res.status, data: null, durationMs, attempt, error: lastError };
        }
      } catch (err) {
        const durationMs = Date.now() - start;
        const isTimeout = err && err.name === "AbortError";
        lastError = isTimeout ? "timeout" : String(err);
        this.logger.warn("tecdoc_request_failed", { url, attempt, error: lastError, durationMs });
        // Cross-reference calls are often slow/unreliable; treat timeouts as soft failures
        // so the job can continue and you can retry later.
        if (isTimeout && purpose === "xref") {
          return { ok: false, status: 0, data: null, durationMs, attempt, error: "timeout" };
        }
        if (isTimeout && this.timeoutMode === "abort-run") {
          throw new AbortRunError("timeout", { url, attempt, durationMs });
        }
        if (isTimeout && this.timeoutMode === "skip-code") {
          throw new SkipCodeError("timeout", { url, attempt, durationMs });
        }
        await sleep(backoffMs);
        backoffMs = Math.min(backoffMs * 2, 60_000);
      } finally {
        clearTimeout(t);
      }
    }

    return { ok: false, status: 0, data: null, durationMs: 0, attempt: this.maxRetries, error: lastError };
  }

  async articleNumberDetails(articleNo) {
    const encoded = encodeURIComponent(String(articleNo));
    const p = `/articles/article-number-details/type-id/${this.typeId}/lang-id/${this.langId}/country-filter-id/${this.countryFilterId}/article-no/${encoded}`;
    return this._getJson(p, { purpose: "details" });
  }

  async crossReferences(articleNo, supplierName) {
    const encodedArticle = encodeURIComponent(String(articleNo));
    const encodedSupplier = encodeURIComponent(String(supplierName));
    const p = `/artlookup/search-for-cross-references-through-oem-numbers/article-no/${encodedArticle}/supplierName/${encodedSupplier}`;
    return this._getJson(p, { purpose: "xref" });
  }
}

function dedupeCrossRefs(items) {
  const list = asListOfObjects(items);
  const seen = new Set();
  const out = [];
  for (const item of list) {
    const key = [
      String(item.crossManufacturerName || "").trim(),
      String(item.crossNumber || "").trim(),
      String(item.searchLevel || "").trim(),
    ].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

async function resolveDetails(client, code, { enableFallback }) {
  void enableFallback;
  const details = await client.articleNumberDetails(code);
  return { details };
}

async function main() {
  const args = parseArgs({
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
      ui: { type: "string", default: "" }, // auto: tty => pretty, else json
      status: { type: "boolean", default: false },
      "write-remaining": { type: "string", default: "" },
      "continue-on-timeout": { type: "boolean", default: false },
      "skip-code-on-timeout": { type: "boolean", default: true },
      "skip-xref": { type: "boolean", default: true },
      "dry-run": { type: "boolean", default: false },
    },
    allowPositionals: false,
  });

  const xmlPath = path.resolve(args.values.xml);
  const outDir = path.resolve(args.values.out);
  const byCodeDir = path.join(outDir, "by_code");
  const progressPath = path.join(outDir, "_progress.json");
  const notFoundPath = path.join(outDir, "not_found.jsonl");
  const summaryPath = path.join(outDir, "summary.json");
  const logPath = path.join(outDir, "tecdoc_fetch_xml_js.log");

  ensureDir(outDir);
  ensureDir(byCodeDir);

  const ui =
    args.values.ui && ["pretty", "json"].includes(args.values.ui)
      ? args.values.ui
      : process.stdout.isTTY
        ? "pretty"
        : "json";

  const progress = ui === "pretty" ? createProgressRenderer() : null;
  const logger = createLogger({ logPath, ui, progress });

  if (!fs.existsSync(xmlPath)) {
    logger.error("xml_missing", { path: xmlPath });
    process.exitCode = 2;
    return;
  }

  if (!args.values["dry-run"] && !args.values.status && !args.values["api-key"]) {
    logger.error("missing_api_key", { hint: "Pass --api-key or set RAPIDAPI_KEY env var" });
    process.exitCode = 2;
    return;
  }

  logger.info("xml_parse_start", { path: xmlPath });
  const xmlText = fs.readFileSync(xmlPath, "utf8");
  const { byCode, orderedCodes } = parseXmlLines(xmlText);
  const uniqueCodes = Array.from(byCode.keys());
  logger.info("xml_parse_done", { total_lines: orderedCodes.length, unique_codes: uniqueCodes.length });

  if (args.values["dry-run"]) return;

  const limit = Number.parseInt(args.values.limit, 10) || 0;
  const codesFilter = args.values.codes
    ? new Set(args.values.codes.split(",").map((s) => s.trim()).filter(Boolean))
    : null;

  const codesToProcess = limit > 0 ? uniqueCodes.slice(0, limit) : uniqueCodes;
  const processedCodes = new Set();
  const timeouts = new Map(); // code -> {attempts, last_timeout_at, next_retry_at, last_url}
  if (args.values.resume) {
    const progress = readJsonIfExists(progressPath) || {};
    for (const c of progress.processed_codes || []) processedCodes.add(String(c));
    const t = progress.timeouts || {};
    for (const [code, meta] of Object.entries(t)) {
      if (!code) continue;
      if (meta && typeof meta === "object") timeouts.set(String(code), meta);
    }
  }

  if (args.values.status) {
    // Status-only mode: show counts without making API calls.
    let existingCount = 0;
    let remainingCount = 0;
    for (const code of codesToProcess) {
      if (codesFilter && !codesFilter.has(String(code).trim())) continue;
      const outFile = path.join(byCodeDir, `${safeFilenameFragment(code)}.json`);
      if (fs.existsSync(outFile) || processedCodes.has(String(code))) existingCount += 1;
      else remainingCount += 1;
    }
    const summary = {
      xml: xmlPath,
      out: outDir,
      total_lines: orderedCodes.length,
      unique_codes: uniqueCodes.length,
      target_codes: codesToProcess.length,
      already_done: existingCount,
      remaining: remainingCount,
      timeouts_scheduled: timeouts.size,
      not_found_file: notFoundPath,
      progress_file: progressPath,
      by_code_dir: byCodeDir,
    };
    if (args.values["write-remaining"]) {
      const remaining = [];
      for (const code of codesToProcess) {
        if (codesFilter && !codesFilter.has(String(code).trim())) continue;
        const outFile = path.join(byCodeDir, `${safeFilenameFragment(code)}.json`);
        if (fs.existsSync(outFile) || processedCodes.has(String(code))) continue;
        remaining.push(String(code));
      }
      fs.writeFileSync(path.resolve(args.values["write-remaining"]), remaining.join("\n") + "\n", "utf8");
      summary.remaining_written_to = path.resolve(args.values["write-remaining"]);
      summary.remaining_written_count = remaining.length;
    }
    if (ui === "json") {
      console.log(JSON.stringify({ ts: nowIsoSeconds(), level: "info", event: "status", ...summary }));
    } else {
      console.log(`XML: ${summary.xml}`);
      console.log(`Out: ${summary.out}`);
      console.log(`Codes: unique=${summary.unique_codes} target=${summary.target_codes}`);
      console.log(`Done: ${summary.already_done} | Remaining: ${summary.remaining}`);
      if (summary.remaining_written_to) console.log(`Remaining list: ${summary.remaining_written_to}`);
    }
    return;
  }

  const client = new TecDocClient({
    apiKey: args.values["api-key"],
    apiHost: "tecdoc-catalog.p.rapidapi.com",
    typeId: Number.parseInt(args.values["type-id"], 10) || 1,
    langId: Number.parseInt(args.values["lang-id"], 10) || 21,
    countryFilterId: Number.parseInt(args.values["country-filter-id"], 10) || 63,
    rps: Number.parseFloat(args.values.rps) || 1,
    timeoutMs: (Number.parseInt(args.values.timeout, 10) || 60) * 1000,
    maxRetries: Number.parseInt(args.values.retries, 10) || 6,
    logger,
    timeoutMode: args.values["skip-code-on-timeout"]
      ? "skip-code"
      : args.values["continue-on-timeout"]
        ? "continue"
        : "abort-run",
  });

  const concurrency = Math.max(1, Number.parseInt(args.values.concurrency, 10) || 1);

  const stats = {
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

  const retryDelaySeconds = 60 * 60; // 1 hour between retries for timed-out codes

  const saveProgress = () => {
    const timeoutsObj = {};
    for (const [code, meta] of timeouts.entries()) timeoutsObj[code] = meta;
    writeJsonAtomic(progressPath, {
      processed_codes: Array.from(processedCodes),
      timeouts: timeoutsObj,
      updated_at: nowIsoSeconds(),
    });
  };

  const startedAtMs = Date.now();
  const recentDurationsMs = [];
  const pushDuration = (ms) => {
    if (!Number.isFinite(ms) || ms <= 0) return;
    recentDurationsMs.push(ms);
    if (recentDurationsMs.length > 50) recentDurationsMs.shift();
  };

  let aborted = null;
  const uiState = { idx: 0, code: "" };
  let heartbeat = null;
  if (ui === "pretty" && progress) {
    heartbeat = setInterval(() => {
      if (!uiState.idx) return;
      progress.render(
        buildProgressLine({
          idx: uiState.idx,
          total: codesToProcess.length,
          code: uiState.code,
          stats,
          startedAtMs,
          recentDurationsMs,
        }),
      );
    }, 1000);
    // Don't keep process alive just for UI.
    heartbeat.unref?.();
  }

  const disabledXrefVariants = new Set();

  // Build queue of codes to process and pre-count skips.
  const todo = [];
  for (let i = 0; i < codesToProcess.length; i += 1) {
    const code = String(codesToProcess[i]).trim();
    if (codesFilter && !codesFilter.has(code)) continue;

    const outFile = path.join(byCodeDir, `${safeFilenameFragment(code)}.json`);

    const timeoutMeta = timeouts.get(code);
    if (timeoutMeta && !args.values.force) {
      const nextRetryMs = parseIsoToMs(timeoutMeta.next_retry_at);
      if (nextRetryMs && Date.now() < nextRetryMs) {
        stats.skipped += 1;
        continue;
      }
    }
    if (!args.values.force && (processedCodes.has(code) || fs.existsSync(outFile))) {
      stats.skipped += 1;
      continue;
    }

    todo.push({ code, idx: i + 1 });
  }

  let queueCursor = 0;
  const nextItem = () => {
    if (queueCursor >= todo.length) return null;
    const item = todo[queueCursor];
    queueCursor += 1;
    return item;
  };

  const saveLocked = (() => {
    let chain = Promise.resolve();
    return () => {
      chain = chain.then(async () => {
        saveProgress();
      });
      return chain;
    };
  })();
  let sinceSave = 0;

  const processOne = async ({ code, idx }) => {
    uiState.idx = idx;
    uiState.code = code;
    stats.inflight += 1;
    const outFile = path.join(byCodeDir, `${safeFilenameFragment(code)}.json`);
    const start = Date.now();
    try {
      const { details } = await resolveDetails(client, code, { enableFallback: false });
      const detailsData = details.data;

      if (!details.ok || !hasArticles(detailsData)) {
        stats.not_found += 1;
        stats.processed += 1;
        processedCodes.add(code);
        timeouts.delete(code);
        appendJsonl(notFoundPath, {
          code,
          reason: "no_articles",
          status: details.status ?? null,
          inputLines: byCode.get(code) || [],
          fetched_at: nowIsoSeconds(),
        });
        return;
      }

      const articles = asListOfObjects(detailsData.articles);
      const supplierNames = Array.from(
        new Set(articles.map((a) => String(a.supplierName || "").trim()).filter(Boolean)),
      ).sort();

      const lookupArticleNo =
        String(detailsData.articleNo || "").trim() || code;

      const crossReferencesBySupplier = [];
      const bySupplier = new Map();

      if (!args.values["skip-xref"]) {
        for (const supplierName of supplierNames) {
          const variantsTried = [];
          const variants = [supplierName, normalizeSupplierName(supplierName)].filter(Boolean);
          let chosenResponse = null;

          for (const variant of variants) {
            if (disabledXrefVariants.has(variant)) {
              variantsTried.push(`${variant} (skipped_timeout)`);
              continue;
            }
            variantsTried.push(variant);
            stats.cross_ref_calls += 1;
            const resp = await client.crossReferences(lookupArticleNo, variant);
            if (resp && resp.error === "timeout") {
              stats.cross_ref_timeouts += 1;
              disabledXrefVariants.add(variant);
              continue;
            }
            const respArticles = dedupeCrossRefs((resp.data || {}).articles);
            if (resp.ok && resp.data && respArticles.length > 0) {
              chosenResponse = { ...resp.data, articles: respArticles };
              break;
            }
            if (resp.ok && resp.data) chosenResponse = { ...resp.data, articles: respArticles };
          }

          const entry = {
            supplierName,
            supplierNameVariantsTried: variantsTried,
            response: chosenResponse,
          };
          crossReferencesBySupplier.push(entry);
          bySupplier.set(supplierName, entry);
        }
      }

      const articlesEnriched = articles.map((a) => {
        const supplierName = String(a.supplierName || "").trim();
        return {
          ...a,
          crossReferences: args.values["skip-xref"]
            ? null
            : (bySupplier.get(supplierName) || {}).response || null,
        };
      });

      const payload = {
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

      writeCodeFile(outFile, payload);
      stats.found += 1;
      stats.processed += 1;
      processedCodes.add(code);
      timeouts.delete(code);
    } catch (err) {
      if (err instanceof SkipCodeError) {
        stats.not_found += 1;
        stats.processed += 1;
        const prev = timeouts.get(code) || {};
        const attempts = (Number(prev.attempts) || 0) + 1;
        const nextRetryAt = new Date(Date.now() + retryDelaySeconds * 1000).toISOString();
        timeouts.set(code, {
          attempts,
          last_timeout_at: nowIsoSeconds(),
          next_retry_at: nextRetryAt,
          last_url: (err.details && err.details.url) || prev.last_url || null,
        });
        appendJsonl(notFoundPath, {
          code,
          reason: "timeout",
          timeout: err.details || {},
          inputLines: byCode.get(code) || [],
          fetched_at: nowIsoSeconds(),
        });
        return;
      }
      // Any other error: record and keep going.
      stats.errors += 1;
      stats.processed += 1;
      processedCodes.add(code);
      timeouts.delete(code);
      appendJsonl(notFoundPath, {
        code,
        reason: "error",
        error: String(err),
        inputLines: byCode.get(code) || [],
        fetched_at: nowIsoSeconds(),
      });
    } finally {
      stats.inflight = Math.max(0, stats.inflight - 1);
      pushDuration(Date.now() - start);
      sinceSave += 1;
      if (sinceSave >= 10) {
        sinceSave = 0;
        await saveLocked();
      }
    }
  };

  let runAborted = false;
  const worker = async () => {
    while (!runAborted) {
      const item = nextItem();
      if (!item) return;
      try {
        await processOne(item);
      } catch (err) {
        if (err instanceof AbortRunError) {
          runAborted = true;
          aborted = {
            code: item.code,
            reason: err.message || "aborted",
            details: err.details || {},
            aborted_at: nowIsoSeconds(),
          };
          process.exitCode = 3;
          return;
        }
      }
    }
  };

  const workers = Array.from({ length: concurrency }, () => worker());
  await Promise.all(workers);
  await saveLocked();

  saveProgress();
  stats.finished_at = nowIsoSeconds();
  if (aborted) stats.aborted = aborted;
  writeJsonAtomic(summaryPath, stats);
  logger.info("run_done", stats);
  if (ui === "pretty" && progress) {
    if (heartbeat) clearInterval(heartbeat);
    progress.end();
  }
}

main().catch((err) => {
  // One wide event for the whole process failure.
  console.log(JSON.stringify({ ts: nowIsoSeconds(), level: "error", event: "fatal", error: String(err) }));
  process.exitCode = 1;
});
