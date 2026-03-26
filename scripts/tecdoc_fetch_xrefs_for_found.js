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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function nowIsoSeconds() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
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

function extractInfoMessage(data) {
  if (!data) return "";
  if (typeof data === "string") return data;
  if (typeof data !== "object") return "";
  for (const key of ["info", "message", "error", "detail", "status"]) {
    const v = data[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

function isMaintenanceOrRateLimitedPayload(data) {
  const msg = extractInfoMessage(data);
  if (!msg) return false;
  return /maintenance|too many requests|rate limit|try again later|temporarily unavailable/i.test(msg);
}

function entryHasTransientMaintenance(entry) {
  if (!entry || typeof entry !== "object") return false;
  if (entry.error === "maintenance") return true;
  return isMaintenanceOrRateLimitedPayload(entry.response);
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

function writeJsonAtomic(filePath, obj) {
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), "utf8");
  fs.renameSync(tmp, filePath);
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
    this.apiKey = apiKey;
    this.apiHost = apiHost;
    this.baseUrl = `https://${apiHost}`;
    this.minDelayMs = rps > 0 ? Math.ceil(1000 / rps) : 0;
    this.timeoutMs = timeoutMs;
    this.maxRetries = maxRetries;
    this._lastRequestAt = 0;
    this._rateQueue = Promise.resolve();
  }

  async _rateLimit() {
    if (!this.minDelayMs) return;
    this._rateQueue = this._rateQueue.then(async () => {
      const now = Date.now();
      const elapsed = now - this._lastRequestAt;
      if (elapsed < this.minDelayMs) await sleep(this.minDelayMs - elapsed);
      this._lastRequestAt = Date.now();
    });
    await this._rateQueue;
  }

  async _getJson(pathname) {
    const url = `${this.baseUrl}${pathname}`;
    let backoffMs = 1000;

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
          await sleep(backoffMs);
          backoffMs = Math.min(backoffMs * 2, 60_000);
          continue;
        }
        if (res.status >= 500 && res.status <= 599) {
          await sleep(backoffMs);
          backoffMs = Math.min(backoffMs * 2, 60_000);
          continue;
        }
        if (!res.ok) return { ok: false, status: res.status, data: null, durationMs, attempt, error: `HTTP ${res.status}` };

        const text = await res.text();
        try {
          const data = JSON.parse(text);
          // Provider sometimes returns HTTP 200 with a "maintenance / too many requests" message.
          // Treat it as a transient failure so we mark the file as incomplete and can retry later.
          if (isMaintenanceOrRateLimitedPayload(data)) {
            if (attempt < this.maxRetries) {
              await sleep(backoffMs);
              backoffMs = Math.min(backoffMs * 2, 60_000);
              continue;
            }
            return { ok: false, status: res.status, data, durationMs, attempt, error: "maintenance" };
          }
          return { ok: true, status: res.status, data, durationMs, attempt };
        } catch {
          return { ok: false, status: res.status, data: null, durationMs, attempt, error: "invalid_json" };
        }
      } catch (err) {
        const durationMs = Date.now() - start;
        const isTimeout = err && err.name === "AbortError";
        const error = isTimeout ? "timeout" : String(err);
        if (attempt < this.maxRetries) {
          await sleep(backoffMs);
          backoffMs = Math.min(backoffMs * 2, 60_000);
          continue;
        }
        return { ok: false, status: 0, data: null, durationMs, attempt, error };
      } finally {
        clearTimeout(t);
      }
    }
    return { ok: false, status: 0, data: null, durationMs: 0, attempt: this.maxRetries, error: "failed" };
  }

  async crossReferences(articleNo, supplierName) {
    const encodedArticle = encodeURIComponent(String(articleNo));
    const encodedSupplier = encodeURIComponent(String(supplierName));
    const p = `/artlookup/search-for-cross-references-through-oem-numbers/article-no/${encodedArticle}/supplierName/${encodedSupplier}`;
    return this._getJson(p);
  }
}

function buildProgressLine({ done, total, inflight, concurrency, foundComplete, foundIncomplete, errors, current, startedAtMs }) {
  const pct = total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0;
  const width = 28;
  const filled = Math.round((pct / 100) * width);
  const bar = "[" + "#".repeat(filled) + "-".repeat(Math.max(0, width - filled)) + "]";
  const elapsedSec = startedAtMs ? (Date.now() - startedAtMs) / 1000 : 0;
  const rate = elapsedSec > 0 ? done / elapsedSec : 0;
  const remainingSec = rate > 0 ? (total - done) / rate : null;
  const eta = remainingSec == null ? "?" : formatDurationSeconds(remainingSec);
  return `${done}/${total} ${String(pct).padStart(3, " ")}% ${bar} | inflight=${inflight}/${concurrency} | complete=${foundComplete} incomplete=${foundIncomplete} err=${errors} | eta=${eta} | code=${String(current || "").slice(0, 24)}`;
}

function supplierMatchesFilter(supplierName, supplierFilter) {
  if (!supplierFilter || supplierFilter.size === 0) return true;
  const raw = String(supplierName || "").trim();
  if (!raw) return false;
  const norm = normalizeSupplierName(raw);
  return supplierFilter.has(raw) || supplierFilter.has(raw.toUpperCase()) || supplierFilter.has(norm);
}

function shouldFetchSupplier({ existingEntry, force, retryIncomplete }) {
  if (force) return true;
  if (!existingEntry) return true;
  if (retryIncomplete && (existingEntry.error || entryHasTransientMaintenance(existingEntry))) return true;
  return false;
}

async function main() {
  const args = parseArgs({
    options: {
      out: { type: "string", default: path.join(process.cwd(), "tecdoc_data", "art_2026_01_01_js") },
      input: { type: "string", default: "" }, // defaults to <out>/by_article if present, else <out>/by_code
      "api-key": { type: "string", default: process.env.RAPIDAPI_KEY || "" },
      rps: { type: "string", default: "1" },
      timeout: { type: "string", default: "60" },
      retries: { type: "string", default: "6" },
      concurrency: { type: "string", default: "1" },
      force: { type: "boolean", default: false },
      "retry-incomplete": { type: "boolean", default: false },
      supplier: { type: "string", default: "" }, // comma-separated
      codes: { type: "string", default: "" }, // comma-separated
      ui: { type: "string", default: "" }, // pretty/json
    },
    allowPositionals: false,
  });

  const outDir = path.resolve(args.values.out);
  const byCodeDir = pickInputDir(outDir, args.values.input);
  const summaryPath = path.join(outDir, "xref_summary.json");

  const supplierFilterRaw = parseCsvList(args.values.supplier);
  const supplierFilter = new Set();
  for (const s of supplierFilterRaw) {
    supplierFilter.add(s);
    supplierFilter.add(s.toUpperCase());
    supplierFilter.add(normalizeSupplierName(s));
  }

  const codesFilterRaw = parseCsvList(args.values.codes);
  const codesFilter = codesFilterRaw.length ? new Set(codesFilterRaw.map((c) => String(c).trim()).filter(Boolean)) : null;

  const ui =
    args.values.ui && ["pretty", "json"].includes(args.values.ui)
      ? args.values.ui
      : process.stdout.isTTY
        ? "pretty"
        : "json";

  const progress = ui === "pretty" ? createProgressRenderer() : null;

  if (!args.values["api-key"]) {
    console.error("Missing RAPIDAPI_KEY (set env var or pass --api-key).");
    process.exitCode = 2;
    return;
  }
  if (!fs.existsSync(byCodeDir)) {
    console.error(`Missing directory: ${byCodeDir}`);
    process.exitCode = 2;
    return;
  }

  const client = new TecDocClient({
    apiKey: args.values["api-key"],
    apiHost: "tecdoc-catalog.p.rapidapi.com",
    rps: Number.parseFloat(args.values.rps) || 3,
    timeoutMs: (Number.parseInt(args.values.timeout, 10) || 20) * 1000,
    maxRetries: Number.parseInt(args.values.retries, 10) || 1,
  });

  const concurrency = Math.max(1, Number.parseInt(args.values.concurrency, 10) || 1);

  const files = fs
    .readdirSync(byCodeDir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => path.join(byCodeDir, f));

  const todo = [];
  let skippedNoMatch = 0;
  for (const filePath of files) {
    let payload;
    try {
      payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
    } catch {
      continue;
    }
    if (!payload || payload.outcome !== "found") continue;
    if (codesFilter && !codesFilter.has(String(payload.code || path.basename(filePath, ".json")).trim())) continue;

    const details = payload?.tecdoc?.articleNumberDetails;
    const articles = asListOfObjects(details?.articles);
    const supplierNames = Array.from(
      new Set(articles.map((a) => String(a.supplierName || "").trim()).filter(Boolean)),
    ).sort();

    const targetSuppliers = supplierNames.filter((s) => supplierMatchesFilter(s, supplierFilter));
    if (supplierFilter.size > 0 && targetSuppliers.length === 0) {
      skippedNoMatch += 1;
      continue;
    }

    const existing = asListOfObjects(payload?.tecdoc?.crossReferencesBySupplier);
    const hasTransient = existing.some((e) => entryHasTransientMaintenance(e));
    const existingBySupplier = new Map(
      existing.map((e) => [String(e.supplierName || "").trim(), e]).filter(([k]) => k),
    );

    const meta = payload.meta || {};
    if (!args.values.force && supplierFilter.size === 0) {
      // Backwards-compatible file-level behavior when running in "all suppliers" mode.
      const wasAttempted = Boolean(meta.xref_fetched_at) || meta.xref_complete === false || hasTransient;
      if (meta.xref_complete === true && !hasTransient) continue;
      if (wasAttempted && !args.values["retry-incomplete"]) continue;
    } else {
      // Supplier-filtered (or forced) mode: decide based on per-supplier needs.
      let needsWork = args.values.force;
      if (!needsWork) {
        for (const supplierName of targetSuppliers) {
          const existingEntry = existingBySupplier.get(supplierName) || null;
          if (shouldFetchSupplier({ existingEntry, force: args.values.force, retryIncomplete: args.values["retry-incomplete"] })) {
            needsWork = true;
            break;
          }
        }
      }
      if (!needsWork) continue;
    }

    todo.push({ filePath, code: payload.code || path.basename(filePath, ".json"), targetSuppliers });
  }

  if (ui === "pretty" && progress) {
    const suffix =
      supplierFilter.size > 0
        ? ` (supplier filter, skipped_no_match=${skippedNoMatch})`
        : "";
    progress.note(`Xref todo: ${todo.length} files${suffix}`);
  }
  if (ui === "json") console.log(JSON.stringify({ ts: nowIsoSeconds(), event: "xref_todo", count: todo.length }));

  let cursor = 0;
  const nextItem = () => {
    if (cursor >= todo.length) return null;
    const item = todo[cursor];
    cursor += 1;
    return item;
  };

  const stats = {
    total: todo.length,
    done: 0,
    inflight: 0,
    concurrency,
    complete: 0,
    incomplete: 0,
    errors: 0,
    skipped_no_match: skippedNoMatch,
    supplier_filter: supplierFilterRaw.length ? supplierFilterRaw : null,
    codes_filter: codesFilterRaw.length ? codesFilterRaw : null,
    started_at_ms: Date.now(),
    started_at: nowIsoSeconds(),
  };

  const processOne = async ({ filePath, code, targetSuppliers }) => {
    const currentLabel =
      Array.isArray(targetSuppliers) && targetSuppliers.length === 1
        ? `${code} ${targetSuppliers[0]}`
        : code;
    stats.inflight += 1;
    const start = Date.now();
    let payload;
    try {
      payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
    } catch (err) {
      stats.errors += 1;
      return;
    } finally {
      // keep progress bar live
      if (ui === "pretty" && progress) {
        progress.render(
          buildProgressLine({
            done: stats.done,
            total: stats.total,
            inflight: stats.inflight,
            concurrency: stats.concurrency,
            foundComplete: stats.complete,
            foundIncomplete: stats.incomplete,
            errors: stats.errors,
            startedAtMs: stats.started_at_ms,
            current: currentLabel,
          }),
        );
      }
    }

    const details = payload?.tecdoc?.articleNumberDetails;
    const articles = asListOfObjects(details?.articles);
    const lookupArticleNo = String(details?.articleNo || payload.code || "").trim();
    const articleNoBySupplier = new Map();
    for (const a of articles) {
      const supplierName = String(a.supplierName || "").trim();
      if (!supplierName) continue;
      const articleNo = String(a.articleNo || "").trim();
      if (!articleNo) continue;
      if (!articleNoBySupplier.has(supplierName)) articleNoBySupplier.set(supplierName, articleNo);
    }
    const supplierNamesAll = Array.from(
      new Set(articles.map((a) => String(a.supplierName || "").trim()).filter(Boolean)),
    ).sort();

    const supplierNames =
      Array.isArray(targetSuppliers) && targetSuppliers.length
        ? targetSuppliers
        : supplierNamesAll.filter((s) => supplierMatchesFilter(s, supplierFilter));

    const existingCross = asListOfObjects(payload?.tecdoc?.crossReferencesBySupplier);
    const existingBySupplier = new Map(
      existingCross.map((e) => [String(e.supplierName || "").trim(), e]).filter(([k]) => k),
    );

    const crossReferencesBySupplier = [];
    const errorCounts = {};

    for (const supplierName of supplierNames) {
      const existingEntry = existingBySupplier.get(supplierName) || null;
      if (!shouldFetchSupplier({ existingEntry, force: args.values.force, retryIncomplete: args.values["retry-incomplete"] })) {
        crossReferencesBySupplier.push(existingEntry);
        continue;
      }

      const articleNoForSupplier = (articleNoBySupplier.get(supplierName) || lookupArticleNo || "").trim();
      if (!articleNoForSupplier) {
        const err = "missing_article_no";
        errorCounts[err] = (errorCounts[err] || 0) + 1;
        crossReferencesBySupplier.push({
          supplierName, // ALWAYS present
          supplierNameVariantsTried: [],
          error: err,
          response: null,
        });
        continue;
      }

      const variantsTried = [];
      const variants = Array.from(new Set([supplierName, normalizeSupplierName(supplierName)].filter(Boolean)));
      let chosen = null;
      let lastError = null;

      for (const variant of variants) {
        variantsTried.push(variant);
        const resp = await client.crossReferences(articleNoForSupplier, variant);
        if (!resp.ok) {
          lastError = resp.error || `HTTP ${resp.status || 0}`;
          errorCounts[lastError] = (errorCounts[lastError] || 0) + 1;
          if (resp.error === "maintenance") {
            chosen = resp.data || null;
            // No point trying other variants when the provider is throttling/maintenance.
            break;
          }
          // try next variant
          continue;
        }
        const respArticles = dedupeCrossRefs((resp.data || {}).articles);
        // A successful response (even with 0 cross refs) is a successful fetch.
        // If we previously had transient failures for other variants, clear them.
        lastError = null;
        if (resp.data && respArticles.length > 0) {
          chosen = { ...resp.data, articles: respArticles };
        } else {
          chosen = resp.data || null;
        }
        break;
      }

      crossReferencesBySupplier.push({
        supplierName, // ALWAYS present
        supplierNameVariantsTried: variantsTried,
        error: lastError,
        response: chosen,
      });
    }

    // Merge results with whatever was already present (keep insertion order).
    for (const entry of crossReferencesBySupplier) {
      if (!entry || !entry.supplierName) continue;
      existingBySupplier.set(String(entry.supplierName).trim(), entry);
    }
    const mergedCrossReferencesBySupplier = Array.from(existingBySupplier.values());

    const bySupplier = new Map(mergedCrossReferencesBySupplier.map((x) => [String(x.supplierName || "").trim(), x]));
    const articlesEnriched = articles.map((a) => {
      const supplierName = String(a.supplierName || "").trim();
      return {
        ...a,
        crossReferences: bySupplier.get(supplierName) || null, // includes supplierName
      };
    });

    payload.tecdoc = payload.tecdoc || {};
    payload.tecdoc.crossReferencesBySupplier = mergedCrossReferencesBySupplier;
    payload.tecdoc.articlesEnriched = articlesEnriched;
    payload.meta = payload.meta || {};
    payload.meta.xref_fetched_at = nowIsoSeconds();
    payload.meta.xref_duration_ms = Date.now() - start;
    payload.meta.xref_complete = !mergedCrossReferencesBySupplier.some((e) => e && e.error);
    payload.meta.xref_supplier_count = supplierNames.length;
    payload.meta.xref_supplier_count_total = supplierNamesAll.length;
    if (supplierFilterRaw.length) payload.meta.xref_supplier_filter = supplierFilterRaw;
    payload.meta.xref_error_counts = errorCounts;

    try {
      writeJsonAtomic(filePath, payload);
      if (payload.meta.xref_complete) stats.complete += 1;
      else stats.incomplete += 1;
    } catch (err) {
      stats.errors += 1;
    } finally {
      stats.inflight = Math.max(0, stats.inflight - 1);
      stats.done += 1;
      if (ui === "pretty" && progress) {
        progress.render(
          buildProgressLine({
            done: stats.done,
            total: stats.total,
            inflight: stats.inflight,
            concurrency: stats.concurrency,
            foundComplete: stats.complete,
            foundIncomplete: stats.incomplete,
            errors: stats.errors,
            startedAtMs: stats.started_at_ms,
            current: currentLabel,
          }),
        );
      }
    }
  };

  const worker = async () => {
    while (true) {
      const item = nextItem();
      if (!item) return;
      await processOne(item);
    }
  };

  const workers = Array.from({ length: concurrency }, () => worker());
  await Promise.all(workers);

  stats.finished_at = nowIsoSeconds();
  writeJsonAtomic(summaryPath, stats);
  if (ui === "pretty" && progress) {
    progress.end();
    progress.note(`Xref done. complete=${stats.complete} incomplete=${stats.incomplete} err=${stats.errors}`);
  }
}

main().catch((err) => {
  console.log(JSON.stringify({ ts: nowIsoSeconds(), level: "error", event: "fatal", error: String(err) }));
  process.exitCode = 1;
});
