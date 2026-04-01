/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

function isInvoiceIngestUploadAction(controller, clickParams) {
    return (
        controller.props.resModel === "invoice.ingest.upload.wizard" &&
        clickParams?.type === "object" &&
        clickParams?.name === "action_import_document"
    );
}

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        this._invoiceIngestSubmitLocked = false;
    },

    async beforeExecuteActionButton(clickParams) {
        if (isInvoiceIngestUploadAction(this, clickParams)) {
            if (this._invoiceIngestSubmitLocked) {
                return false;
            }
            this._invoiceIngestSubmitLocked = true;
            try {
                const result = await super.beforeExecuteActionButton(...arguments);
                if (result === false) {
                    this._invoiceIngestSubmitLocked = false;
                }
                return result;
            } catch (error) {
                this._invoiceIngestSubmitLocked = false;
                throw error;
            }
        }
        return super.beforeExecuteActionButton(...arguments);
    },

    async afterExecuteActionButton(clickParams) {
        try {
            return await super.afterExecuteActionButton(...arguments);
        } finally {
            if (isInvoiceIngestUploadAction(this, clickParams)) {
                this._invoiceIngestSubmitLocked = false;
            }
        }
    },
});
