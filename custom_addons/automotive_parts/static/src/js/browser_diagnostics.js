/** @odoo-module **/

const ENDPOINT = "/automotive/browser-diagnostics";
let ENABLED = true;
try {
    ENABLED = !["0", "false", "no", "off"].includes(
        (window.localStorage.getItem("automotive.http.trace") || "").trim().toLowerCase()
    );
} catch {
    ENABLED = true;
}

function emptyObject() {
    return {};
}

function serializeKnownError(error) {
    const serialized = {
        name: error.name || "Error",
        message: error.message || String(error),
        stack: error.stack || null,
    };
    return serialized;
}

function serializeError(error) {
    return error ? serializeKnownError(error) : emptyObject();
}

function fallbackText(value, fallback) {
    return value || fallback;
}

function nullableValue(value) {
    return value || null;
}

function trySendBeacon(body) {
    try {
        const blob = new Blob([body], { type: "application/json" });
        return Boolean(navigator.sendBeacon && navigator.sendBeacon(ENDPOINT, blob));
    } catch {
        return false;
    }
}

function sendFetch(body) {
    fetch(ENDPOINT, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body,
        keepalive: true,
        credentials: "same-origin",
    }).catch(() => {});
}

function enqueue(event) {
    if (!ENABLED) {
        return;
    }
    const body = JSON.stringify(event);
    if (!trySendBeacon(body)) {
        sendFetch(body);
    }
}

function report(kind, details) {
    enqueue({
        kind,
        href: window.location.href,
        referrer: document.referrer || null,
        title: document.title || null,
        user_agent: navigator.userAgent,
        timestamp: new Date().toISOString(),
        ...details,
    });
}

function reportResourceError(event, target) {
    report("resource_error", {
        tag_name: target.tagName,
        src: target.src || target.href || null,
        filename: event.filename || null,
    });
}

function reportWindowError(event) {
    report("window_error", {
        message: fallbackText(event.message, "Unknown window error"),
        filename: nullableValue(event.filename),
        lineno: nullableValue(event.lineno),
        colno: nullableValue(event.colno),
        error: serializeError(event.error),
    });
}

function isResourceErrorTarget(target) {
    return Boolean(target && target !== window && target.tagName);
}

window.addEventListener(
    "error",
    (event) => {
        const target = event.target;
        if (isResourceErrorTarget(target)) {
            reportResourceError(event, target);
            return;
        }
        reportWindowError(event);
    },
    true
);

window.addEventListener("unhandledrejection", (event) => {
    report("unhandled_rejection", {
        reason: serializeError(event.reason),
    });
});

if (window.odoo && window.odoo.__session_info__) {
    report("page_boot", {
        session_info: {
            is_admin: !!window.odoo.__session_info__.is_admin,
            is_system: !!window.odoo.__session_info__.is_system,
            uid: window.odoo.__session_info__.uid || null,
            is_public: !!window.odoo.__session_info__.is_public,
            is_internal_user: !!window.odoo.__session_info__.is_internal_user,
        },
    });
}
