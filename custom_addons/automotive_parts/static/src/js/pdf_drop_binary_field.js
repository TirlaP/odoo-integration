/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { getDataURLFromFile } from "@web/core/utils/urls";
import { checkFileSize } from "@web/core/utils/files";
import { useService } from "@web/core/utils/hooks";
import { FileUploader } from "@web/views/fields/file_handler";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component, useState } from "@odoo/owl";

function parseAcceptedExtensions(rawExtensions) {
    return (rawExtensions || "*")
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean);
}

function acceptsAnyFile(accepted) {
    return !accepted.length || accepted.includes("*");
}

function fileNameMatchesExtension(fileName, extension) {
    return extension.startsWith(".") && fileName.endsWith(extension);
}

export class PdfDropBinaryField extends Component {
    static template = "automotive_parts.PdfDropBinaryField";
    static components = { FileUploader };
    static props = {
        ...standardFieldProps,
        acceptedFileExtensions: { type: String, optional: true },
        fileNameField: { type: String, optional: true },
    };
    static defaultProps = {
        acceptedFileExtensions: ".pdf",
    };

    setup() {
        this.notification = useService("notification");
        this.state = useState({ isDragging: false });
    }

    get fileName() {
        const binaryValue = this.props.record.data[this.props.name];
        const fallback = binaryValue && typeof binaryValue === "string" ? _t("uploaded_file.pdf") : "";
        return this.props.record.data[this.props.fileNameField] || fallback;
    }

    async updateValue(data, name) {
        const { fileNameField, record } = this.props;
        const changes = { [this.props.name]: data || false };
        if (fileNameField in record.fields) {
            changes[fileNameField] = name || "";
        }
        await this.props.record.update(changes);
    }

    isAcceptedFile(file) {
        const accepted = parseAcceptedExtensions(this.props.acceptedFileExtensions);
        if (acceptsAnyFile(accepted)) {
            return true;
        }
        const fileName = (file.name || "").toLowerCase();
        return accepted.some((ext) => fileNameMatchesExtension(fileName, ext));
    }

    async processFile(file) {
        if (!this.isAcceptedFile(file)) {
            this.notification.add(_t("Only PDF files are accepted."), { type: "warning" });
            return;
        }
        if (!checkFileSize(file.size, this.notification)) {
            return;
        }
        const dataUrl = await getDataURLFromFile(file);
        const base64Data = (dataUrl || "").split(",")[1];
        await this.updateValue(base64Data, file.name);
    }

    async onUploaded(file) {
        await this.updateValue(file.data, file.name);
    }

    onDragEnter() {
        this.state.isDragging = true;
    }

    onDragLeave() {
        this.state.isDragging = false;
    }

    async onDrop(ev) {
        this.state.isDragging = false;
        const file = ev.dataTransfer?.files?.[0];
        if (!file) {
            return;
        }
        await this.processFile(file);
    }

    async onClear() {
        await this.updateValue(false, "");
    }
}

export const pdfDropBinaryField = {
    component: PdfDropBinaryField,
    displayName: _t("PDF Dropzone"),
    supportedOptions: [
        {
            label: _t("Accepted file extensions"),
            name: "accepted_file_extensions",
            type: "string",
        },
    ],
    supportedTypes: ["binary"],
    extractProps: ({ attrs, options }) => ({
        acceptedFileExtensions: options.accepted_file_extensions,
        fileNameField: attrs.filename,
    }),
};

registry.category("fields").add("pdf_drop_binary", pdfDropBinaryField);
