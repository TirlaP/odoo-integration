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

function getDuplicateTargetId(value) {
    if (Array.isArray(value)) {
        return value[0];
    }
    if (value && typeof value === "object" && "resId" in value) {
        return value.resId;
    }
    return value || false;
}

function getNotificationContent(data) {
    const state = data?.state;
    const isRunning = state === "running";
    const stage = data?.async_progress_message || "";
    const progress = Math.round(data?.async_progress_percent || 0);
    return {
        title: isRunning ? _t("Import Running") : _t("Import Queued"),
        message: isRunning
            ? (stage
                ? `${stage} (${progress}%)`
                : _t("Import is processing in the background. This page updates automatically."))
            : (stage || _t("Import is queued in the background and will start automatically.")),
    };
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
        return [
            this.props.resModel,
            root?.resId || false,
            root?.data?.state || "",
            root?.data?.finished_at || "",
            root?.data?.async_progress_message || "",
            root?.data?.async_progress_percent || 0,
            this._invoiceDuplicateTargetId() || false,
            root?.data?.duplicate_warning_message || "",
        ];
    },

    _isInvoiceIngestRecord() {
        const root = this._invoiceIngestRoot();
        return this.props.resModel === INVOICE_INGEST_MODEL && Boolean(root?.resId);
    },

    _isInvoiceIngestProcessing() {
        return this._isInvoiceIngestRecord() && ACTIVE_STATES.has(this._invoiceIngestRoot()?.data?.state);
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
        const data = this._invoiceIngestRoot()?.data || {};
        const { title, message } = getNotificationContent(data);
        const notificationKey = [data.state || "", data.async_progress_message || "", data.async_progress_percent || 0].join(":");
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

    _stopInvoiceIngestPolling() {
        if (this._invoiceIngestPollTimer) {
            browser.clearInterval(this._invoiceIngestPollTimer);
            this._invoiceIngestPollTimer = null;
        }
        if (this._invoiceIngestInitialPollTimer) {
            browser.clearTimeout(this._invoiceIngestInitialPollTimer);
            this._invoiceIngestInitialPollTimer = null;
        }
        if (this._closeInvoiceIngestNotification) {
            this._closeInvoiceIngestNotification();
            this._closeInvoiceIngestNotification = null;
        }
        this._invoiceIngestNotificationKey = null;
        this._invoiceIngestPolling = false;
    },

    _invoiceDuplicateTargetId() {
        return getDuplicateTargetId(this._invoiceIngestRoot()?.data?.duplicate_of_job_id);
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
        return [
            "invoice_ingest_duplicate",
            root.resId,
            duplicateId,
            root?.data?.finished_at || "",
        ].join(":");
    },

    _invoiceDuplicateWarningContext() {
        const root = this._invoiceIngestRoot();
        if (!this._isInvoiceIngestRecord() || ACTIVE_STATES.has(root?.data?.state)) {
            return null;
        }
        const duplicateId = this._invoiceDuplicateTargetId();
        const message = root?.data?.duplicate_warning_message;
        const noticeKey = this._invoiceDuplicateNoticeKey();
        if (!duplicateId || !message || !noticeKey) {
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

    async _pollInvoiceIngestRecord() {
        if (this._shouldSkipInvoiceIngestPoll()) {
            return;
        }
        this._invoiceIngestPolling = true;
        try {
            const root = this._invoiceIngestRoot();
            if (!(await root.isDirty())) {
                await root.load();
            }
        } catch (error) {
            this._stopInvoiceIngestPolling();
            throw error;
        } finally {
            this._invoiceIngestPolling = false;
            if (!this._isInvoiceIngestProcessing()) {
                this._stopInvoiceIngestPolling();
            }
            this._maybeShowInvoiceDuplicateWarning();
        }
    },
});
