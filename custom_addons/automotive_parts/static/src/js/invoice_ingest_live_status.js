/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { FormController } from "@web/views/form/form_controller";

import { onWillUnmount, useEffect } from "@odoo/owl";

const ACTIVE_STATES = new Set(["pending", "running"]);
const POLL_INTERVAL_MS = 500;
const INITIAL_POLL_DELAY_MS = 150;
const INVOICE_INGEST_MODEL = "invoice.ingest.job";

function getRootData(root) {
    return root?.data || {};
}

function isActiveState(state) {
    return ACTIVE_STATES.has(state);
}

function isInvoiceIngestRecord(resModel, root) {
    return resModel === INVOICE_INGEST_MODEL && Boolean(root?.resId);
}

function isInvoiceIngestProcessing(resModel, root) {
    return isInvoiceIngestRecord(resModel, root) && isActiveState(getRootData(root).state);
}

function isDuplicateObject(value) {
    return Boolean(value) && typeof value === "object" && "resId" in value;
}

function scalarOrFalse(value) {
    return value || false;
}

function getDuplicateTargetId(value) {
    if (Array.isArray(value)) {
        return value[0];
    }
    if (isDuplicateObject(value)) {
        return value.resId;
    }
    return scalarOrFalse(value);
}

function fallbackText(value) {
    return value || "";
}

function fallbackNumber(value) {
    return value || 0;
}

function fallbackFalse(value) {
    return value || false;
}

function getNotificationTitle(state) {
    return state === "running" ? _t("Import Running") : _t("Import Queued");
}

function getRunningMessage(stage, progress) {
    if (stage) {
        return `${stage} (${progress}%)`;
    }
    return _t("Import is processing in the background. This page updates automatically.");
}

function getQueuedMessage(stage) {
    return stage || _t("Import is queued in the background and will start automatically.");
}

function getNotificationMessage(state, stage, progress) {
    if (state === "running") {
        return getRunningMessage(stage, progress);
    }
    return getQueuedMessage(stage);
}

function getNotificationContent(data) {
    const state = data?.state;
    const stage = fallbackText(data?.async_progress_message);
    const progress = Math.round(fallbackNumber(data?.async_progress_percent));
    return {
        title: getNotificationTitle(state),
        message: getNotificationMessage(state, stage, progress),
    };
}

function getNotificationKey(data) {
    return [
        fallbackText(data.state),
        fallbackText(data.async_progress_message),
        fallbackNumber(data.async_progress_percent),
    ].join(":");
}

function getRootId(root) {
    return fallbackFalse(root?.resId);
}

function getEffectDeps(resModel, root, duplicateId) {
    const data = getRootData(root);
    return [
        resModel,
        getRootId(root),
        fallbackText(data.state),
        fallbackText(data.finished_at),
        fallbackText(data.async_progress_message),
        fallbackNumber(data.async_progress_percent),
        fallbackFalse(duplicateId),
        fallbackText(data.duplicate_warning_message),
    ];
}

function buildDuplicateNoticeKey(root, duplicateId) {
    return [
        "invoice_ingest_duplicate",
        root.resId,
        duplicateId,
        fallbackText(getRootData(root).finished_at),
    ].join(":");
}

function hasDuplicateWarningPayload(duplicateId, message, noticeKey) {
    return Boolean(duplicateId && message && noticeKey);
}

function buildDuplicateAction(duplicateId) {
    return {
        type: "ir.actions.act_window",
        name: _t("Importuri facturi"),
        res_model: INVOICE_INGEST_MODEL,
        res_id: duplicateId,
        views: [[false, "form"]],
        view_mode: "form",
        target: "current",
    };
}

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        this.notification = useService("notification");
        this.actionService = useService("action");
        this._invoiceIngestPollTimer = null;
        this._invoiceIngestInitialPollTimer = null;
        this._invoiceIngestPolling = false;
        this._closeInvoiceIngestNotification = null;
        this._invoiceIngestNotificationKey = null;

        useEffect(
            () => {
                this._syncInvoiceIngestPolling();
                this._maybeShowInvoiceDuplicateWarning();
                return () => this._stopInvoiceIngestPolling();
            },
            () => this._invoiceIngestEffectDeps()
        );

        onWillUnmount(() => this._stopInvoiceIngestPolling());
    },

    _invoiceIngestRoot() {
        return this.model.root;
    },

    _invoiceIngestEffectDeps() {
        const root = this._invoiceIngestRoot();
        return getEffectDeps(this.props.resModel, root, this._invoiceDuplicateTargetId());
    },

    _isInvoiceIngestRecord() {
        return isInvoiceIngestRecord(this.props.resModel, this._invoiceIngestRoot());
    },

    _isInvoiceIngestProcessing() {
        return isInvoiceIngestProcessing(this.props.resModel, this._invoiceIngestRoot());
    },

    _scheduleInvoiceIngestInitialPoll() {
        if (this._invoiceIngestInitialPollTimer) {
            return;
        }
        this._invoiceIngestInitialPollTimer = browser.setTimeout(() => {
            this._invoiceIngestInitialPollTimer = null;
            this._pollInvoiceIngestRecord();
        }, INITIAL_POLL_DELAY_MS);
    },

    _startInvoiceIngestPollLoop() {
        if (this._invoiceIngestPollTimer) {
            return;
        }
        this._invoiceIngestPollTimer = browser.setInterval(
            () => this._pollInvoiceIngestRecord(),
            POLL_INTERVAL_MS
        );
    },

    _syncInvoiceIngestPolling() {
        if (!this._isInvoiceIngestProcessing()) {
            this._stopInvoiceIngestPolling();
            return;
        }
        this._showInvoiceIngestNotification();
        this._scheduleInvoiceIngestInitialPoll();
        this._startInvoiceIngestPollLoop();
    },

    _showInvoiceIngestNotification() {
        const data = getRootData(this._invoiceIngestRoot());
        const { title, message } = getNotificationContent(data);
        const notificationKey = getNotificationKey(data);
        if (this._closeInvoiceIngestNotification && this._invoiceIngestNotificationKey === notificationKey) {
            return;
        }
        if (this._closeInvoiceIngestNotification) {
            this._closeInvoiceIngestNotification();
        }
        this._closeInvoiceIngestNotification = this.notification.add(message, {
            title,
            type: "info",
            sticky: true,
        });
        this._invoiceIngestNotificationKey = notificationKey;
    },

    _clearInvoiceIngestTimers() {
        if (this._invoiceIngestPollTimer) {
            browser.clearInterval(this._invoiceIngestPollTimer);
            this._invoiceIngestPollTimer = null;
        }
        if (this._invoiceIngestInitialPollTimer) {
            browser.clearTimeout(this._invoiceIngestInitialPollTimer);
            this._invoiceIngestInitialPollTimer = null;
        }
    },

    _closeInvoiceIngestStatusNotification() {
        if (this._closeInvoiceIngestNotification) {
            this._closeInvoiceIngestNotification();
            this._closeInvoiceIngestNotification = null;
        }
        this._invoiceIngestNotificationKey = null;
    },

    _stopInvoiceIngestPolling() {
        this._clearInvoiceIngestTimers();
        this._closeInvoiceIngestStatusNotification();
        this._invoiceIngestPolling = false;
    },

    _invoiceDuplicateTargetId() {
        return getDuplicateTargetId(getRootData(this._invoiceIngestRoot()).duplicate_of_job_id);
    },

    _invoiceDuplicateNoticeKey() {
        const root = this._invoiceIngestRoot();
        if (!this._isInvoiceIngestRecord()) {
            return false;
        }
        const duplicateId = this._invoiceDuplicateTargetId();
        if (!duplicateId) {
            return false;
        }
        return buildDuplicateNoticeKey(root, duplicateId);
    },

    _invoiceDuplicateWarningContext() {
        const root = this._invoiceIngestRoot();
        const data = getRootData(root);
        if (!this._isInvoiceIngestRecord()) {
            return null;
        }
        if (isActiveState(data.state)) {
            return null;
        }
        const duplicateId = this._invoiceDuplicateTargetId();
        const message = data.duplicate_warning_message;
        const noticeKey = this._invoiceDuplicateNoticeKey();
        if (!hasDuplicateWarningPayload(duplicateId, message, noticeKey)) {
            return null;
        }
        return { duplicateId, message, noticeKey };
    },

    _openInvoiceDuplicateOriginal(duplicateId) {
        return this.actionService.doAction(buildDuplicateAction(duplicateId));
    },

    _maybeShowInvoiceDuplicateWarning() {
        const context = this._invoiceDuplicateWarningContext();
        if (!context) {
            return;
        }
        if (browser.sessionStorage.getItem(context.noticeKey)) {
            return;
        }
        browser.sessionStorage.setItem(context.noticeKey, "1");
        this.notification.add(context.message, {
            title: _t("Duplicate Document"),
            type: "warning",
            sticky: true,
            buttons: [
                {
                    name: _t("Open Original"),
                    primary: true,
                    onClick: () => this._openInvoiceDuplicateOriginal(context.duplicateId),
                },
            ],
        });
    },

    _shouldSkipInvoiceIngestPoll() {
        if (!this._isInvoiceIngestProcessing()) {
            this._stopInvoiceIngestPolling();
            return true;
        }
        return this._invoiceIngestPolling;
    },

    async _refreshInvoiceIngestRecordIfClean() {
        const root = this._invoiceIngestRoot();
        if (!(await root.isDirty())) {
            await root.load();
        }
    },

    _finalizeInvoiceIngestPoll() {
        this._invoiceIngestPolling = false;
        if (!this._isInvoiceIngestProcessing()) {
            this._stopInvoiceIngestPolling();
        }
        this._maybeShowInvoiceDuplicateWarning();
    },

    async _pollInvoiceIngestRecord() {
        if (this._shouldSkipInvoiceIngestPoll()) {
            return;
        }
        this._invoiceIngestPolling = true;
        try {
            await this._refreshInvoiceIngestRecordIfClean();
        } catch (error) {
            this._stopInvoiceIngestPolling();
            throw error;
        } finally {
            this._finalizeInvoiceIngestPoll();
        }
    },
});
