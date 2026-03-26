(() => {
  // custom_addons/automotive_parts/static/src/tsx/invoice_ingest_react_page.tsx
  async function rpc(url, params) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        method: "call",
        params: params || {}
      })
    });
    const payload = await response.json();
    if (payload.error) {
      const message = payload.error.data && payload.error.data.message || payload.error.message || "RPC error";
      throw new Error(message);
    }
    return payload.result;
  }
  function toNumber(value) {
    const parsed = parseFloat(String(value));
    return Number.isFinite(parsed) ? parsed : 0;
  }
  function formatMoney(value, symbol, position) {
    const amount = Number(value || 0).toFixed(2);
    if (!symbol) return amount;
    return position === "before" ? `${symbol} ${amount}` : `${amount} ${symbol}`;
  }
  function badgeForStatus(status) {
    if (status === "matched") {
      return {
        cls: "border-emerald-200 bg-emerald-50 text-emerald-700",
        label: "Matched"
      };
    }
    if (status === "manual") {
      return {
        cls: "border-amber-200 bg-amber-50 text-amber-700",
        label: "Manual"
      };
    }
    return {
      cls: "border-rose-200 bg-rose-50 text-rose-700",
      label: "Not Found"
    };
  }
  function stateLabel(state) {
    const source = String(state || "").trim();
    if (!source) return "-";
    return source.split("_").map((part) => part ? part[0].toUpperCase() + part.slice(1) : part).join(" ");
  }
  function prettyJson(raw) {
    if (!raw) return "";
    try {
      return JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      return raw;
    }
  }
  function numberInput(value, onSave, widthClass) {
    return /* @__PURE__ */ React.createElement(
      "input",
      {
        className: `rounded-md border border-slate-300 bg-white px-2 py-1 text-right text-[13px] text-slate-900 shadow-sm outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-300/50 ${widthClass}`,
        defaultValue: Number(value || 0).toFixed(2),
        onBlur: (ev) => onSave(toNumber(ev.target.value))
      }
    );
  }
  function filterLabel(key) {
    if (key === "all") return "All";
    if (key === "matched") return "Matched";
    if (key === "manual") return "Manual";
    return "Not Found";
  }
  function App(props) {
    const jobId = props.jobId;
    const [job, setJob] = React.useState(null);
    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState("");
    const [busyLineId, setBusyLineId] = React.useState(null);
    const [query, setQuery] = React.useState("");
    const [matchFilter, setMatchFilter] = React.useState("all");
    const loadData = async () => {
      setLoading(true);
      setError("");
      try {
        const result = await rpc(
          "/automotive/invoice-ingest/react/data",
          { job_id: jobId }
        );
        if (!result.ok || !result.job) {
          throw new Error(result.error || "Could not load job");
        }
        setJob(result.job);
      } catch (err) {
        setError(err.message || String(err));
      } finally {
        setLoading(false);
      }
    };
    React.useEffect(() => {
      loadData();
    }, [jobId]);
    const patchLineLocal = (lineId, values) => {
      setJob((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          lines: prev.lines.map(
            (line) => line.id === lineId ? { ...line, ...values } : line
          )
        };
      });
    };
    const saveLine = async (lineId, values) => {
      setBusyLineId(lineId);
      setError("");
      try {
        const result = await rpc(
          "/automotive/invoice-ingest/react/line/update",
          { line_id: lineId, values }
        );
        if (!result.ok || !result.line) {
          throw new Error(result.error || "Could not save line");
        }
        patchLineLocal(lineId, result.line);
      } catch (err) {
        setError(err.message || String(err));
      } finally {
        setBusyLineId(null);
      }
    };
    const runLineAction = async (lineId, actionName) => {
      setBusyLineId(lineId);
      setError("");
      try {
        const result = await rpc(
          "/automotive/invoice-ingest/react/line/action",
          { line_id: lineId, action_name: actionName }
        );
        if (!result.ok || !result.line) {
          throw new Error(result.error || "Could not process action");
        }
        patchLineLocal(lineId, result.line);
      } catch (err) {
        setError(err.message || String(err));
      } finally {
        setBusyLineId(null);
      }
    };
    const lines = React.useMemo(() => {
      if (!job) return [];
      const q = query.trim().toLowerCase();
      return job.lines.filter((line) => {
        if (matchFilter !== "all" && line.match_status !== matchFilter) {
          return false;
        }
        if (!q) return true;
        return (line.product_code || "").toLowerCase().includes(q) || (line.product_code_raw || "").toLowerCase().includes(q) || (line.supplier_brand || "").toLowerCase().includes(q) || (line.product_description || "").toLowerCase().includes(q) || (line.product_display_name || "").toLowerCase().includes(q) || (line.matched_ean || "").toLowerCase().includes(q) || (line.matched_internal_code || "").toLowerCase().includes(q);
      });
    }, [job, query, matchFilter]);
    const totals = React.useMemo(() => {
      return lines.reduce(
        (acc, line) => {
          acc.subtotal += Number(line.subtotal || 0);
          acc.total += Number(line.subtotal_incl_vat || 0);
          return acc;
        },
        { subtotal: 0, total: 0 }
      );
    }, [lines]);
    const counts = React.useMemo(() => {
      const c = { matched: 0, manual: 0, not_found: 0 };
      if (!job) return c;
      for (const line of job.lines) {
        if (line.match_status === "matched") c.matched += 1;
        else if (line.match_status === "manual") c.manual += 1;
        else c.not_found += 1;
      }
      return c;
    }, [job]);
    if (loading) {
      return /* @__PURE__ */ React.createElement("div", { className: "min-h-screen bg-slate-100 p-10 text-slate-700" }, "Loading invoice review...");
    }
    if (!job) {
      return /* @__PURE__ */ React.createElement("div", { className: "min-h-screen bg-slate-100 p-10 text-rose-700" }, error || "No data");
    }
    return /* @__PURE__ */ React.createElement("div", { className: "min-h-screen bg-slate-100 text-slate-900", style: { fontFamily: "Manrope, ui-sans-serif, system-ui" } }, /* @__PURE__ */ React.createElement("div", { className: "mx-auto max-w-[2200px] px-6 py-6" }, /* @__PURE__ */ React.createElement("div", { className: "mb-4 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm" }, /* @__PURE__ */ React.createElement("div", { className: "flex flex-wrap items-start justify-between gap-4" }, /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("p", { className: "text-[11px] uppercase tracking-[0.18em] text-slate-500" }, "Invoice Ingest Workbench"), /* @__PURE__ */ React.createElement("h1", { className: "mt-1 text-[30px] font-semibold tracking-tight text-slate-900" }, job.invoice_number || job.name), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-sm text-slate-600" }, "Invoice supplier: ", job.partner_name || "Not resolved yet", " \xB7 ", stateLabel(job.state))), /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50",
        onClick: () => window.history.back()
      },
      "Back to Odoo"
    ), /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        className: "rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-700",
        onClick: loadData
      },
      "Refresh"
    ))), /* @__PURE__ */ React.createElement("div", { className: "mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8" }, [
      ["Invoice Date", job.invoice_date || "-"],
      ["VAT Rate", `${Number(job.vat_rate || 0).toFixed(2)}%`],
      ["Invoice Total", formatMoney(job.amount_total || 0, job.currency_symbol, job.currency_position)],
      ["AI Confidence", `${Number(job.ai_confidence || 0).toFixed(2)}%`],
      ["Matched", String(counts.matched)],
      ["Manual", String(counts.manual)],
      ["Not Found", String(counts.not_found)],
      ["Attachment", job.attachment_name || "-"]
    ].map(([label, value]) => /* @__PURE__ */ React.createElement("div", { key: label, className: "rounded-xl border border-slate-200 bg-slate-50 px-3 py-2" }, /* @__PURE__ */ React.createElement("p", { className: "text-[11px] font-medium uppercase tracking-[0.12em] text-slate-500" }, label), /* @__PURE__ */ React.createElement("p", { className: "mt-1 break-words text-sm font-semibold text-slate-900" }, value))))), error ? /* @__PURE__ */ React.createElement("div", { className: "mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700" }, error) : null, /* @__PURE__ */ React.createElement("div", { className: "mb-3 flex flex-wrap items-center gap-2" }, /* @__PURE__ */ React.createElement(
      "input",
      {
        type: "text",
        className: "w-full max-w-md rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-500 focus:ring-2 focus:ring-slate-300/50",
        placeholder: "Search code, brand, description, EAN, cod intern, matched product...",
        value: query,
        onChange: (ev) => setQuery(ev.target.value)
      }
    ), ["all", "matched", "manual", "not_found"].map((key) => /* @__PURE__ */ React.createElement(
      "button",
      {
        key,
        type: "button",
        onClick: () => setMatchFilter(key),
        className: `rounded-lg border px-3 py-2 text-xs font-semibold uppercase tracking-[0.1em] ${matchFilter === key ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`
      },
      filterLabel(key)
    ))), /* @__PURE__ */ React.createElement("div", { className: "overflow-auto rounded-2xl border border-slate-200 bg-white shadow-sm" }, /* @__PURE__ */ React.createElement("table", { className: "w-full min-w-[2500px] table-auto text-sm" }, /* @__PURE__ */ React.createElement("thead", { className: "sticky top-0 z-10 bg-slate-50" }, /* @__PURE__ */ React.createElement("tr", null, [
      "Code",
      "Brand",
      "Description",
      "Quantity",
      "PU fara TVA",
      "PU cu TVA",
      "EAN",
      "Cod Intern",
      "Matched Product",
      "Match Status",
      "Actions"
    ].map((label) => /* @__PURE__ */ React.createElement("th", { key: label, className: "border-b border-slate-200 px-3 py-3 text-left align-top text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-600" }, /* @__PURE__ */ React.createElement("span", { className: "inline-block max-w-full whitespace-normal leading-4" }, label))))), /* @__PURE__ */ React.createElement("tbody", null, lines.map((line) => {
      const status = badgeForStatus(line.match_status);
      const rawDiffers = Boolean(line.product_code_raw && line.product_code_raw !== line.product_code);
      return /* @__PURE__ */ React.createElement("tr", { key: line.id, className: "border-b border-slate-200 align-top hover:bg-slate-50/80" }, /* @__PURE__ */ React.createElement("td", { className: "max-w-[260px] px-3 py-3 text-slate-900" }, /* @__PURE__ */ React.createElement("div", { className: "whitespace-normal break-words font-semibold leading-5" }, line.product_code || "-"), rawDiffers ? /* @__PURE__ */ React.createElement("div", { className: "mt-1 whitespace-normal break-words text-xs leading-4 text-slate-500" }, "Raw: ", line.product_code_raw) : null), /* @__PURE__ */ React.createElement("td", { className: "max-w-[140px] px-3 py-3 text-slate-700" }, /* @__PURE__ */ React.createElement("span", { className: "rounded-md border border-slate-200 bg-slate-100 px-2 py-1 text-xs font-semibold uppercase tracking-[0.08em]" }, line.supplier_brand || "-")), /* @__PURE__ */ React.createElement("td", { className: "max-w-[360px] px-3 py-3 text-slate-800" }, /* @__PURE__ */ React.createElement("span", { className: "whitespace-normal break-words leading-5" }, line.product_description)), /* @__PURE__ */ React.createElement("td", { className: "px-3 py-3" }, numberInput(line.quantity, (v) => saveLine(line.id, { quantity: v }), "w-20")), /* @__PURE__ */ React.createElement("td", { className: "px-3 py-3" }, numberInput(line.unit_price, (v) => saveLine(line.id, { unit_price: v }), "w-24")), /* @__PURE__ */ React.createElement("td", { className: "px-3 py-3 text-right tabular-nums text-slate-700" }, Number(line.unit_price_incl_vat || 0).toFixed(2)), /* @__PURE__ */ React.createElement("td", { className: "px-3 py-3 text-slate-700" }, line.matched_ean || "-"), /* @__PURE__ */ React.createElement("td", { className: "px-3 py-3 text-slate-700" }, line.matched_internal_code || "-"), /* @__PURE__ */ React.createElement("td", { className: "max-w-[320px] px-3 py-3 text-slate-800" }, /* @__PURE__ */ React.createElement("span", { className: "whitespace-normal break-words leading-5" }, line.product_display_name || "-")), /* @__PURE__ */ React.createElement("td", { className: "px-3 py-3" }, /* @__PURE__ */ React.createElement("span", { className: `inline-flex rounded-full border px-2 py-1 text-xs font-semibold ${status.cls}` }, status.label)), /* @__PURE__ */ React.createElement("td", { className: "px-3 py-3" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(
        "button",
        {
          type: "button",
          disabled: busyLineId === line.id,
          className: "rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60",
          onClick: () => runLineAction(line.id, "try_match")
        },
        "Match"
      ), /* @__PURE__ */ React.createElement(
        "button",
        {
          type: "button",
          disabled: busyLineId === line.id,
          className: "rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60",
          onClick: () => runLineAction(line.id, "clear_match")
        },
        "Clear"
      ))));
    })))), /* @__PURE__ */ React.createElement("div", { className: "mt-4 flex items-center justify-end gap-10 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm" }, /* @__PURE__ */ React.createElement("div", { className: "text-sm text-slate-600" }, "Total fara TVA: ", /* @__PURE__ */ React.createElement("span", { className: "font-semibold text-slate-900" }, formatMoney(totals.subtotal, job.currency_symbol, job.currency_position))), /* @__PURE__ */ React.createElement("div", { className: "text-sm text-slate-600" }, "Total cu TVA: ", /* @__PURE__ */ React.createElement("span", { className: "font-semibold text-slate-900" }, formatMoney(totals.total, job.currency_symbol, job.currency_position)))), job.error || job.payload_json || job.external_id ? /* @__PURE__ */ React.createElement("div", { className: "mt-5 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" }, /* @__PURE__ */ React.createElement("h2", { className: "text-sm font-semibold uppercase tracking-[0.14em] text-slate-600" }, "Diagnostics"), job.external_id ? /* @__PURE__ */ React.createElement("div", { className: "mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3" }, /* @__PURE__ */ React.createElement("p", { className: "text-xs font-medium uppercase tracking-[0.12em] text-slate-500" }, "External ID"), /* @__PURE__ */ React.createElement("p", { className: "mt-1 text-sm text-slate-800" }, job.external_id)) : null, job.error ? /* @__PURE__ */ React.createElement("div", { className: "mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3" }, /* @__PURE__ */ React.createElement("p", { className: "text-xs font-medium uppercase tracking-[0.12em] text-rose-700" }, "Error"), /* @__PURE__ */ React.createElement("pre", { className: "mt-2 whitespace-pre-wrap break-words text-xs text-rose-700" }, job.error)) : null, job.payload_json ? /* @__PURE__ */ React.createElement("div", { className: "mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3" }, /* @__PURE__ */ React.createElement("p", { className: "text-xs font-medium uppercase tracking-[0.12em] text-slate-500" }, "Payload JSON"), /* @__PURE__ */ React.createElement("pre", { className: "mt-2 max-h-[320px] overflow-auto whitespace-pre-wrap break-words text-xs text-slate-700" }, prettyJson(job.payload_json))) : null) : null));
  }
  var rootElement = document.getElementById("invoice-ingest-react-root");
  if (rootElement) {
    const jobId = Number(rootElement.dataset.jobId || 0);
    ReactDOM.createRoot(rootElement).render(/* @__PURE__ */ React.createElement(App, { jobId }));
  }
})();
