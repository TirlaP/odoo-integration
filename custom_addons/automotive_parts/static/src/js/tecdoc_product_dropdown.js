/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

const MIN_TECDOC_QUERY_LENGTH = 3;

function canSearchTecDoc(props, request) {
    return (
        props.resModel === "product.product" &&
        props.context?.automotive_tecdoc_dropdown &&
        request.trim().length >= MIN_TECDOC_QUERY_LENGTH
    );
}

function tecdocInsertIndex(options) {
    const createIndex = options.findIndex((option) =>
        option.classList?.includes("o_m2o_dropdown_option_create")
    );
    return createIndex >= 0 ? createIndex : options.length;
}

patch(Many2XAutocomplete.prototype, {
    async loadOptionsSource(request) {
        const options = await super.loadOptionsSource(...arguments);
        const query = request.trim();
        if (!canSearchTecDoc(this.props, query)) {
            return options;
        }

        const tecdocOption = {
            label: _t('Caută în TecDoc "%s"', query),
            classList: "o_m2o_dropdown_option o_automotive_tecdoc_dropdown_option",
            action: async (params) => {
                const product = await this.orm.call("tecdoc.sync.wizard", "sync_product_for_lookup", [], {
                    lookup_type: "article_no",
                    article_number: query,
                });
                await this.props.update(
                    [{ id: product.id, display_name: product.display_name }],
                    params
                );
            },
        };
        options.splice(tecdocInsertIndex(options), 0, tecdocOption);
        return options;
    },
});
