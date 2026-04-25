/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

const INVOICE_UPLOAD_MODEL = "invoice.ingest.upload.wizard";
const INVOICE_UPLOAD_ACTION = "action_import_document";

const isInvoiceUploadModel = (controller) => controller.props.resModel === INVOICE_UPLOAD_MODEL;
const isObjectButton = (clickParams) => clickParams?.type === "object";
const isImportDocumentAction = (clickParams) => clickParams?.name === INVOICE_UPLOAD_ACTION;
const isInvoiceIngestUploadAction = (controller, clickParams) => (
    isInvoiceUploadModel(controller) &&
    isObjectButton(clickParams) &&
    isImportDocumentAction(clickParams)
);

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        this._invoiceIngestSubmitLocked = false;
    },

    _unlockInvoiceIngestSubmit() {
        this._invoiceIngestSubmitLocked = false;
    },

    async _runInvoiceIngestUploadButton() {
        this._invoiceIngestSubmitLocked = true;
        try {
            const result = await super.beforeExecuteActionButton(...arguments);
            if (result === false) {
                this._unlockInvoiceIngestSubmit();
            }
            return result;
        } catch (error) {
            this._unlockInvoiceIngestSubmit();
            throw error;
        }
    },

    async beforeExecuteActionButton(clickParams) {
        if (this._invoiceIngestSubmitLocked && isInvoiceIngestUploadAction(this, clickParams)) {
            return false;
        }
        if (isInvoiceIngestUploadAction(this, clickParams)) {
            return this._runInvoiceIngestUploadButton(...arguments);
        }
        return super.beforeExecuteActionButton(...arguments);
    },

    async afterExecuteActionButton(clickParams) {
        try {
            return await super.afterExecuteActionButton(...arguments);
        } finally {
            if (isInvoiceIngestUploadAction(this, clickParams)) {
                this._unlockInvoiceIngestSubmit();
            }
        }
    },
});
