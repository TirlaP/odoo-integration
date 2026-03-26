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

function serializeError(error) {
    if (!error) {
        return {};
    }
    const serialized = {
        name: error.name || "Error",
        message: error.message || String(error),
        stack: error.stack || null,
    };
    return serialized;
}

function enqueue(event) {
    if (!ENABLED) {
        return;
    }
    const body = JSON.stringify(event);
    try {
        const blob = new Blob([body], { type: "application/json" });
        if (navigator.sendBeacon) {
            const sent = navigator.sendBeacon(ENDPOINT, blob);
            if (sent) {
                return;
            }
        }
    } catch {
        // Fall through to fetch.
    }

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

window.addEventListener(
    "error",
    (event) => {
        const target = event.target;
        if (target && target !== window && target.tagName) {
            report("resource_error", {
                tag_name: target.tagName,
                src: target.src || target.href || null,
                filename: event.filename || null,
            });
            return;
        }

        report("window_error", {
            message: event.message || "Unknown window error",
            filename: event.filename || null,
            lineno: event.lineno || null,
            colno: event.colno || null,
            error: serializeError(event.error),
        });
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
