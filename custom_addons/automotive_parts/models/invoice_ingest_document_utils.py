# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from xml.etree import ElementTree

from .invoice_ingest_code_utils import normalize_code_value


SUPPLIER_CREDIT_NOTE_TOKENS = (
    'CREDIT NOTE',
    'CREDITNOTE',
    'NOTA DE CREDITARE',
    'NOTA CREDITARE',
    'FACTURA STORNO',
    'STORNO',
    'REFUND',
    'RETUR',
)


def infer_document_move_type_from_xml(xml_payload):
    if not xml_payload:
        return False
    try:
        root = ElementTree.fromstring(xml_payload.encode('utf-8') if isinstance(xml_payload, str) else xml_payload)
    except Exception:
        return False
    local_name = root.tag.rsplit('}', 1)[-1]
    if local_name == 'CreditNote':
        return 'in_refund'
    if local_name == 'Invoice':
        return 'in_invoice'
    return False


def looks_like_supplier_credit_note_text(text):
    haystack = normalize_code_value(text or '').upper()
    if not haystack:
        return False
    return any(token in haystack for token in SUPPLIER_CREDIT_NOTE_TOKENS)


def detect_attachment_kind(binary, filename=None, mimetype=None):
    name = (filename or '').strip().lower()
    mime = (mimetype or '').strip().lower()
    if not binary:
        return ''
    if 'pdf' in mime or name.endswith('.pdf') or binary[:4] == b'%PDF':
        return 'pdf'
    if mime.startswith('image/') or name.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff')):
        return 'image'
    try:
        from PIL import Image

        with Image.open(BytesIO(binary)) as image:
            image.verify()
        return 'image'
    except Exception:
        return ''


def prepare_ocr_image_path(image):
    try:
        from PIL import Image, ImageOps
    except Exception:
        return ''
    try:
        if image.mode == 'P':
            image = image.convert('RGBA')
        if image.mode in {'RGBA', 'LA'}:
            background = Image.new('RGBA', image.size, 'white')
            background.paste(image, mask=image.getchannel('A'))
            image = background.convert('RGB')
        else:
            image = image.convert('RGB')
        if max(image.size or (0, 0)) < 2400:
            image = image.resize((max(image.width * 2, 1), max(image.height * 2, 1)), Image.LANCZOS)
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        tmp.close()
        image.save(tmp.name, format='PNG')
        return tmp.name
    except Exception:
        return ''


def ocr_image_path(image_path):
    if not image_path or not shutil.which('tesseract'):
        return ''
    try:
        result = subprocess.run(
            ['tesseract', image_path, 'stdout', '--psm', '6', '--dpi', '300'],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception:
        return ''
    if result.returncode != 0:
        return ''
    return (result.stdout or '').strip()
