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

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        this.notification = useService("notification");
        this.actionService = useService("action");
        this._invoiceIngestPollTimer = null;
        this._invoiceIngestInitialPollTimer = null;
        this._invoiceIngestPolling = false;
        this._closeInvoiceIngestNotification = null;

        useEffect(
            () => {
                this._syncInvoiceIngestPolling();
                this._maybeShowInvoiceDuplicateWarning();
                return () => this._stopInvoiceIngestPolling();
            },
            () => [
                this.props.resModel,
                this.model.root?.resId || false,
                this.model.root?.data?.state || "",
                this.model.root?.data?.finished_at || "",
                this._invoiceDuplicateTargetId() || false,
                this.model.root?.data?.duplicate_warning_message || "",
            ]
        );

        onWillUnmount(() => this._stopInvoiceIngestPolling());
    },

    _isInvoiceIngestProcessing() {
        return (
            this.props.resModel === "invoice.ingest.job" &&
            Boolean(this.model.root?.resId) &&
            ACTIVE_STATES.has(this.model.root?.data?.state)
        );
    },

    _syncInvoiceIngestPolling() {
        if (this._isInvoiceIngestProcessing()) {
            this._showInvoiceIngestNotification();
            if (!this._invoiceIngestInitialPollTimer) {
                this._invoiceIngestInitialPollTimer = browser.setTimeout(() => {
                    this._invoiceIngestInitialPollTimer = null;
                    this._pollInvoiceIngestRecord();
                }, INITIAL_POLL_DELAY_MS);
            }
            if (!this._invoiceIngestPollTimer) {
                this._invoiceIngestPollTimer = browser.setInterval(
                    () => this._pollInvoiceIngestRecord(),
                    POLL_INTERVAL_MS
                );
            }
        } else {
            this._stopInvoiceIngestPolling();
        }
    },

    _showInvoiceIngestNotification() {
        const isRunning = this.model.root?.data?.state === "running";
        const title = isRunning ? _t("Background Import") : _t("Import Queued");
        const message = isRunning
            ? _t("Import is processing in the background. This page updates automatically.")
            : _t("Import is queued in the background and will start automatically.");
        if (this._closeInvoiceIngestNotification) {
            return;
        }
        this._closeInvoiceIngestNotification = this.notification.add(message, {
            title,
            type: "info",
            sticky: true,
        });
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
        this._invoiceIngestPolling = false;
    },

    _invoiceDuplicateTargetId() {
        const value = this.model.root?.data?.duplicate_of_job_id;
        if (Array.isArray(value)) {
            return value[0];
        }
        if (value && typeof value === "object" && "resId" in value) {
            return value.resId;
        }
        return value || false;
    },

    _invoiceDuplicateNoticeKey() {
        if (this.props.resModel !== "invoice.ingest.job" || !this.model.root?.resId) {
            return false;
        }
        const duplicateId = this._invoiceDuplicateTargetId();
        if (!duplicateId) {
            return false;
        }
        return [
            "invoice_ingest_duplicate",
            this.model.root.resId,
            duplicateId,
            this.model.root?.data?.finished_at || "",
        ].join(":");
    },

    _maybeShowInvoiceDuplicateWarning() {
        if (
            this.props.resModel !== "invoice.ingest.job" ||
            !this.model.root?.resId ||
            ACTIVE_STATES.has(this.model.root?.data?.state)
        ) {
            return;
        }
        const duplicateId = this._invoiceDuplicateTargetId();
        const message = this.model.root?.data?.duplicate_warning_message;
        const noticeKey = this._invoiceDuplicateNoticeKey();
        if (!duplicateId || !message || !noticeKey) {
            return;
        }
        if (browser.sessionStorage.getItem(noticeKey)) {
            return;
        }
        browser.sessionStorage.setItem(noticeKey, "1");
        this.notification.add(message, {
            title: _t("Duplicate Document"),
            type: "warning",
            sticky: true,
            buttons: [
                {
                    name: _t("Open Original"),
                    primary: true,
                    onClick: () =>
                        this.actionService.doAction({
                            type: "ir.actions.act_window",
                            name: _t("Importuri facturi"),
                            res_model: "invoice.ingest.job",
                            res_id: duplicateId,
                            views: [[false, "form"]],
                            view_mode: "form",
                            target: "current",
                        }),
                },
            ],
        });
    },

    async _pollInvoiceIngestRecord() {
        if (!this._isInvoiceIngestProcessing() || this._invoiceIngestPolling) {
            if (!this._isInvoiceIngestProcessing()) {
                this._stopInvoiceIngestPolling();
            }
            return;
        }
        this._invoiceIngestPolling = true;
        try {
            if (!(await this.model.root.isDirty())) {
                await this.model.root.load();
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
