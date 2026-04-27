/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { redirect } from "@web/core/utils/urls";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

const SALE_ORDER_MODEL = "sale.order";
const CREATE_ORDER_PATH = "/odoo/creeaza-comanda";
const FALLBACK_PATH = "/odoo/importuri-facturi";

function isAutomotiveCreateOrderForm(controller) {
    return (
        controller.props.resModel === SALE_ORDER_MODEL &&
        browser.location.pathname === CREATE_ORDER_PATH &&
        controller.model.root?.isNew
    );
}

patch(FormController.prototype, {
    async discard() {
        if (!isAutomotiveCreateOrderForm(this)) {
            return super.discard(...arguments);
        }
        if (this.props.discardRecord) {
            this.props.discardRecord(this.model.root);
        } else {
            await this.model.root.discard();
        }
        redirect(FALLBACK_PATH);
    },
});
