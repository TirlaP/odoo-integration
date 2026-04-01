/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { FormController } from "@web/views/form/form_controller";

import { onWillUnmount, useEffect } from "@odoo/owl";

const ACTIVE_STATES = new Set(["pending", "running"]);
const POLL_INTERVAL_MS = 2000;

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        this.notification = useService("notification");
        this._invoiceIngestPollTimer = null;
        this._invoiceIngestPolling = false;
        this._closeInvoiceIngestNotification = null;

        useEffect(
            () => {
                this._syncInvoiceIngestPolling();
                return () => this._stopInvoiceIngestPolling();
            },
            () => [
                this.props.resModel,
                this.model.root?.resId || false,
                this.model.root?.data?.state || "",
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
        if (this._closeInvoiceIngestNotification) {
            this._closeInvoiceIngestNotification();
            this._closeInvoiceIngestNotification = null;
        }
        this._invoiceIngestPolling = false;
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
        }
    },
});
