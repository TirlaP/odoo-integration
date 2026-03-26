#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "docs" / "oferta" / "oferta_data.json"
OUT_PDF = ROOT / "docs" / "oferta" / "Oferta_Sistem_Gestiune_Piese_Auto_Odoo.pdf"


def _register_unicode_font() -> str:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            font_name = "OfferFont"
            try:
                pdfmetrics.registerFont(TTFont(font_name, path))
                return font_name
            except Exception:
                continue
    return "Helvetica"


_DIACRITICS_MAP = str.maketrans(
    {
        "ă": "a",
        "â": "a",
        "î": "i",
        "ș": "s",
        "ş": "s",
        "ț": "t",
        "ţ": "t",
        "Ă": "A",
        "Â": "A",
        "Î": "I",
        "Ș": "S",
        "Ş": "S",
        "Ț": "T",
        "Ţ": "T",
    }
)


def _strip_ro_diacritics(text: str) -> str:
    return text.translate(_DIACRITICS_MAP)


def _money(amount: float) -> str:
    if float(amount).is_integer():
        return f"{int(amount):,}".replace(",", " ")
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


@dataclass(frozen=True)
class OfferData:
    raw: dict[str, Any]

    @property
    def strip_diacritics(self) -> bool:
        return bool(self.raw.get("render", {}).get("strip_diacritics", False))

    def t(self, text: str) -> str:
        return _strip_ro_diacritics(text) if self.strip_diacritics else text

    @property
    def title(self) -> str:
        return self.t(str(self.raw["document"]["title"]))

    @property
    def subtitle(self) -> str:
        return self.t(str(self.raw["document"]["subtitle"]))

    @property
    def offer_number(self) -> str:
        return self.t(str(self.raw["document"]["offer_number"]))

    @property
    def date(self) -> str:
        return self.t(str(self.raw["document"]["date"]))

    @property
    def validity_days(self) -> int:
        return int(self.raw["document"]["validity_days"])

    @property
    def platform(self) -> str:
        return self.t(str(self.raw["scope"]["platform"]))

    @property
    def currency(self) -> str:
        return self.t(str(self.raw["commercial"]["currency"]))

    @property
    def total_price(self) -> float:
        return float(self.raw["commercial"]["total_price"])

    @property
    def vat_note(self) -> str:
        return self.t(str(self.raw["commercial"]["vat_note"]))

    @property
    def payment_schedule(self) -> list[dict[str, Any]]:
        return list(self.raw["commercial"]["payment_schedule"])

    @property
    def optional_monthly_support(self) -> dict[str, Any] | None:
        support = self.raw["commercial"].get("optional_monthly_support")
        if not support or support.get("enabled") is not True:
            return None
        return dict(support)

    @property
    def provider_lines(self) -> list[str]:
        p = self.raw["parties"]["provider"]
        return [
            self.t(str(p["name"])),
            self.t(f"Identificare: {p['reg']}"),
            self.t(f"Adresa: {p['address']}"),
            self.t(f"Email: {p['email']}"),
            self.t(f"Telefon: {p['phone']}"),
        ]

    @property
    def beneficiary_lines(self) -> list[str]:
        b = self.raw["parties"]["beneficiary"]
        return [
            self.t(str(b["name"])),
            self.t(f"Identificare: {b['reg']}"),
            self.t(f"Adresa: {b['address']}"),
            self.t(f"Contact: {b['contact']}"),
        ]

    @property
    def in_scope(self) -> list[str]:
        return [self.t(str(x)) for x in self.raw["scope"]["in_scope"]]

    @property
    def out_of_scope(self) -> list[str]:
        return [self.t(str(x)) for x in self.raw["scope"]["out_of_scope"]]

    @property
    def assumptions(self) -> list[str]:
        return [self.t(str(x)) for x in self.raw.get("assumptions", [])]


def _bullets(items: list[str], style: ParagraphStyle) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(text, style), leftIndent=14, value="•") for text in items],
        bulletType="bullet",
        leftIndent=18,
        bulletFontName=style.fontName,
        bulletFontSize=style.fontSize,
    )


class OfferDoc(BaseDocTemplate):
    def __init__(self, filename: str, *, data: OfferData, **kwargs: Any):
        super().__init__(filename, **kwargs)
        self.data = data

    def afterFlowable(self, flowable: Any) -> None:
        if isinstance(flowable, Paragraph):
            style_name = getattr(flowable.style, "name", "")
            text = flowable.getPlainText()
            if style_name == "H2":
                self.notify("TOCEntry", (0, text, self.page))
            elif style_name == "H3":
                self.notify("TOCEntry", (1, text, self.page))


def build_pdf(data: OfferData, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    font = _register_unicode_font()
    styles = getSampleStyleSheet()

    base = ParagraphStyle(
        "Base",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=10.5,
        leading=14,
        spaceAfter=6,
        textColor=colors.HexColor("#111827"),
    )
    small = ParagraphStyle("Small", parent=base, fontSize=9.5, leading=12, textColor=colors.HexColor("#111827"))
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName=font, fontSize=20, leading=24, spaceAfter=8)
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName=font,
        fontSize=13.5,
        leading=16.5,
        spaceBefore=10,
        spaceAfter=6,
        textColor=colors.HexColor("#111827"),
    )
    h3 = ParagraphStyle(
        "H3",
        parent=styles["Heading3"],
        fontName=font,
        fontSize=11.5,
        leading=14,
        spaceBefore=8,
        spaceAfter=4,
        textColor=colors.HexColor("#111827"),
    )
    muted = ParagraphStyle("Muted", parent=base, textColor=colors.HexColor("#4B5563"))

    page_w, page_h = A4
    left = 2.0 * cm
    right = 2.0 * cm
    top = 2.2 * cm
    bottom = 1.8 * cm

    def on_cover(canvas, doc_obj):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#111827"))
        canvas.rect(0, page_h - 3.2 * cm, page_w, 3.2 * cm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont(font, 10)
        canvas.drawString(left, page_h - 1.2 * cm, data.t("OFERTA TEHNICA SI COMERCIALA"))
        canvas.setFont(font, 9)
        canvas.setFillColor(colors.HexColor("#E5E7EB"))
        canvas.drawRightString(page_w - right, page_h - 1.2 * cm, f"{data.offer_number} | {data.date}")
        canvas.restoreState()

    def on_page(canvas, doc_obj):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#E5E7EB"))
        canvas.setLineWidth(0.8)
        canvas.line(left, page_h - top + 0.6 * cm, page_w - right, page_h - top + 0.6 * cm)

        canvas.setFont(font, 9)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(left, page_h - top + 0.85 * cm, f"{data.offer_number} | {data.date}")
        canvas.drawRightString(page_w - right, page_h - top + 0.85 * cm, data.t("Oferta tehnica si comerciala"))

        canvas.setFont(font, 9)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawRightString(page_w - right, bottom - 0.9 * cm, str(doc_obj.page))
        canvas.restoreState()

    frame = Frame(left, bottom, page_w - left - right, page_h - top - bottom, id="content")
    doc = OfferDoc(
        str(out_path),
        data=data,
        pagesize=A4,
        leftMargin=left,
        rightMargin=right,
        topMargin=top,
        bottomMargin=bottom,
        title=data.title,
        author=data.t(str(data.raw["parties"]["provider"]["name"])),
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[frame], onPage=on_cover),
            PageTemplate(id="main", frames=[frame], onPage=on_page),
        ]
    )

    story: list[Any] = []

    # Cover content (use cover template)
    story.append(Paragraph(data.title, ParagraphStyle("CoverTitle", parent=h1, textColor=colors.HexColor("#111827"), spaceBefore=1.4 * cm)))
    story.append(Paragraph(data.subtitle, ParagraphStyle("CoverSub", parent=base, fontSize=12, leading=16, textColor=colors.HexColor("#111827"))))
    story.append(Spacer(1, 0.6 * cm))

    meta = Table(
        [
            [Paragraph(data.t("<b>Nr. oferta</b>"), small), Paragraph(data.offer_number, small)],
            [Paragraph(data.t("<b>Data</b>"), small), Paragraph(data.date, small)],
            [Paragraph(data.t("<b>Valabilitate</b>"), small), Paragraph(data.t(f"{data.validity_days} zile"), small)],
            [Paragraph(data.t("<b>Pret total implementare</b>"), small), Paragraph(data.t(f"{_money(data.total_price)} {data.currency}"), small)],
        ],
        colWidths=[5.2 * cm, 11.2 * cm],
    )
    meta.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(meta)
    story.append(Spacer(1, 0.7 * cm))

    parties = Table(
        [
            [Paragraph(data.t("<b>Prestator</b>"), small), Paragraph(data.t("<b>Beneficiar</b>"), small)],
            [Paragraph("<br/>".join(data.provider_lines), small), Paragraph("<br/>".join(data.beneficiary_lines), small)],
        ],
        colWidths=[8.2 * cm, 8.2 * cm],
    )
    parties.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F9FAFB")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(parties)
    story.append(PageBreak())

    # Switch to main template after cover
    story.append(Paragraph("<para></para>", base))

    # TOC
    story.append(Paragraph(data.t("Cuprins"), h2))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("TOC0", parent=base, leftIndent=0, firstLineIndent=0, spaceBefore=2, spaceAfter=2),
        ParagraphStyle("TOC1", parent=base, leftIndent=14, firstLineIndent=0, fontSize=10, textColor=colors.HexColor("#374151")),
    ]
    story.append(toc)
    story.append(PageBreak())

    # 1. Summary
    story.append(Paragraph(data.t("1. Rezumat"), h2))
    story.append(
        Paragraph(
            data.t(
                "Prezenta oferta acopera implementarea completa a unui sistem ERP pentru distributia de piese auto, "
                "conform cerintelor functionale (sectiunile 2.1–2.11). Obiectivul este livrarea unui flux operational "
                "complet, trasabil si auditat: <b>Furnizor → Receptie (NIR) → Stoc → Comanda → Livrare → Retur</b>, "
                "cu integrare TecDoc, ANAF e-Factura, integrare contabilitate SAGA si portal pentru mecanici."
            ),
            base,
        )
    )
    story.append(Spacer(1, 6))
    highlights = Table(
        [
            [Paragraph(data.t("<b>Rezultatul livrarii</b>"), base)],
            [
                _bullets(
                    [
                        data.t("Stoc in timp real + rezervari automate pentru comenzi active"),
                        data.t("Receptii (NIR) cu scanare cod de bare si semnalizare diferente"),
                        data.t("Comenzi cu stari automate, trasabilitate si Audit Log complet"),
                        data.t("Integrari: TecDoc, ANAF e-Factura, SAGA, API furnizori (unde exista)"),
                        data.t("Portal mecanici + generare/arhivare documente"),
                    ],
                    base,
                )
            ],
        ],
        colWidths=[16.4 * cm],
    )
    highlights.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F9FAFB")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(highlights)

    # 2. Platform
    story.append(Paragraph(data.t("2. Platforma si arhitectura"), h2))
    story.append(Paragraph(data.t(f"<b>Platforma:</b> {data.platform}"), base))
    story.append(
        Paragraph(
            data.t(
                "Odoo ramane <b>sursa de adevar</b> pentru produse, parteneri, comenzi, receptii/livrari, stocuri, "
                "facturi si plati. Dezvoltarile custom vor folosi obiectele si fluxurile standard Odoo pentru stoc si "
                "contabilitate, evitand duplicarea logicii critice."
            ),
            base,
        )
    )

    # 3. Scope
    story.append(Paragraph(data.t("3. Domeniu de aplicare (scope)"), h2))
    story.append(Paragraph(data.t("3.1 Inclus"), h3))
    story.append(_bullets(data.in_scope, base))
    story.append(Paragraph(data.t("3.2 Exclus (se poate oferta separat)"), h3))
    story.append(_bullets(data.out_of_scope, base))

    # 4. Deliverables
    story.append(PageBreak())
    story.append(Paragraph(data.t("4. Livrabile (pe module)"), h2))
    deliverables: list[tuple[str, list[str]]] = [
        (
            data.t("4.1 Clienti (2.1.1)"),
            [
                data.t("CRUD clienti, tipuri (PF/PJ/mecanic), validari CUI/CNP, arhivare (dezactivare)."),
                data.t("Sold curent calculat automat conform regulilor agreate (comenzi/facturi/plati/retururi)."),
                data.t("Istoric modificari si audit complet."),
            ],
        ),
        (
            data.t("4.2 Comenzi (2.1.2)"),
            [
                data.t("Comenzi interne/externe, stari conforme, restrictii editare, anulare controlata."),
                data.t("Pozitii comanda cu cantitati: comandata/rezervata/receptionata/livrata + status pe linie."),
                data.t("Actualizare automata a starii in functie de stoc si receptii."),
            ],
        ),
        (
            data.t("4.3 Produse + Stocuri (2.2)"),
            [
                data.t("Evidenta stoc disponibil/rezervat; rezervare automata pentru comenzi active."),
                data.t("Trasabilitate pe flux: furnizor → receptie → stoc → comanda → livrare → retur."),
                data.t("Etichete: generare din NIR/stoc si din factura (per produs si cantitate)."),
            ],
        ),
        (
            data.t("4.4 Receptie (NIR) (2.3)"),
            [
                data.t("Creare receptie, scanare cod de bare, asociere automata cu produs existent."),
                data.t("Workflow asistat pentru creare produs nou daca nu exista."),
                data.t("Semnalizare diferente cantitative inainte de validare."),
                data.t("Legare factura furnizor (ANAF e-Factura sau manual) + deduplicare."),
            ],
        ),
        (
            data.t("4.5 TecDoc (2.4)"),
            [
                data.t("Stocare locala sau sincronizare periodica (conform deciziei) + proces update anual."),
                data.t("Asociere produs cu cod TecDoc, compatibilitati vehicule si metadate tehnice."),
                data.t("Marcaj explicit pentru produse fara TecDoc."),
            ],
        ),
        (
            data.t("4.6 Automatizari + notificari (2.5)"),
            [
                data.t("Corelare automata: produse comandate vs receptionate vs stoc disponibil."),
                data.t("Calcul automat status „gata de pregatire” si notificari in aplicatie (+ email optional)."),
            ],
        ),
        (
            data.t("4.7 Plati (2.6)"),
            [
                data.t("Inregistrare plati, alocare pe comanda/comenzi si (unde este necesar) pe pozitii."),
                data.t("Recalcul automat solduri; corelare plati–facturi–livrari."),
            ],
        ),
        (
            data.t("4.8 Portal mecanici (2.7)"),
            [
                data.t("Autentificare separata si acces strict la datele proprii."),
                data.t("Comenzi active, istoric, sold, plati; descarcare documente; cereri catre firma."),
            ],
        ),
        (
            data.t("4.9 Audit Log (2.8)"),
            [
                data.t("Jurnal complet: utilizator, actiune, entitate, ID, timp, valori vechi/noi."),
                data.t("Interfata de filtrare si raportare."),
            ],
        ),
        (
            data.t("4.10 Integrare SAGA (2.9)"),
            [
                data.t("Export automat receptii/livrari/retururi in formatul agreat cu contabilitatea."),
                data.t("Sync bidirectional: etapizat dupa validarea exportului si a procesului contabil."),
                data.t("Semnalizare discrepante intre stoc contabil si stoc operational."),
            ],
        ),
        (
            data.t("4.11 Documente + arhivare (2.10)"),
            [
                data.t("Generare automata documente (NIR, facturi, avize, chitante, documente interne)."),
                data.t("Arhivare electronica (atasamente + metadate + cautare)."),
            ],
        ),
        (
            data.t("4.12 Furnizori (2.11)"),
            [
                data.t("Conectori (unde exista API): stoc/pret/lead time + plasare comenzi catre furnizori."),
                data.t("Implementare incrementala per furnizor, in functie de acces si documentatie."),
            ],
        ),
    ]
    for title, items in deliverables:
        story.append(KeepTogether([Paragraph(title, h3), _bullets(items, base), Spacer(1, 4)]))

    # 5. Plan
    story.append(PageBreak())
    story.append(Paragraph(data.t("5. Plan de implementare (faze)"), h2))
    phases = [
        (data.t("Faza 1 — Core operational (MVP)"), data.t("Corectitudine stoc/rezervari, comenzi, receptii (NIR), audit, etichete (baza).")),
        (data.t("Faza 2 — Integrari"), data.t("TecDoc (local/sync), ANAF e-Factura (import + dedupe + mapare), conectori furnizori (primii furnizori).")),
        (data.t("Faza 3 — Financiar + Portal + SAGA"), data.t("Plati/alocari, portal mecanici, export SAGA complet; sync bidirectional etapizat.")),
        (data.t("Faza 4 — Go-live + stabilizare"), data.t("Migrare date agreata, training, UAT, monitorizare, bugfix.")),
    ]
    phase_table = Table([[data.t("Faza"), data.t("Descriere")]] + [[Paragraph(p[0], base), Paragraph(p[1], base)] for p in phases], colWidths=[6.2 * cm, 10.2 * cm])
    phase_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F9FAFB")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(phase_table)

    # 6. Timeline
    story.append(Spacer(1, 10))
    story.append(Paragraph(data.t("6. Termene (estimare)"), h2))
    story.append(
        Paragraph(
            data.t(
                "Estimare: <b>12–16 saptamani</b> (in functie de deciziile de integrare, disponibilitatea API-urilor "
                "furnizorilor si ritmul de feedback din UAT)."
            ),
            base,
        )
    )

    # 7. Commercial
    story.append(Paragraph(data.t("7. Conditii comerciale"), h2))
    story.append(Paragraph(data.t(f"<b>Pret total implementare:</b> {_money(data.total_price)} {data.currency}"), base))
    story.append(Paragraph(data.vat_note, muted))

    pay_rows: list[list[Any]] = [[data.t("Transa"), data.t("Prag / Livrabil"), data.t("Procent")]]
    for idx, p in enumerate(data.payment_schedule, start=1):
        pay_rows.append([str(idx), data.t(str(p["milestone"])), f'{int(p["percent"])}%'])
    pay_table = Table(pay_rows, colWidths=[1.3 * cm, 12.2 * cm, 3.1 * cm])
    pay_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F9FAFB")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(pay_table)

    support = data.optional_monthly_support
    if support:
        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                data.t(
                    f"<b>Optional:</b> {support['description']} — "
                    f"<b>{_money(float(support['price']))} {data.currency}/luna</b>."
                ),
                base,
            )
        )

    # 8. Assumptions / exclusions
    story.append(PageBreak())
    story.append(Paragraph(data.t("8. Presupuneri, dependente si excluderi"), h2))
    story.append(Paragraph(data.t("Presupuneri / dependente"), h3))
    story.append(_bullets(data.assumptions, base))
    story.append(Paragraph(data.t("Excluderi (rezumat)"), h3))
    story.append(
        _bullets(
            [
                data.t("Licente/subscrieri si credentiale oferite de terti (TecDoc/RapidAPI, ANAF, SAGA etc.)."),
                data.t("Hardware si infrastructura fizica (imprimanta etichete, scanere, print server)."),
                data.t("Limitari/indisponibilitati ale API-urilor furnizorilor (depinde de terti)."),
            ],
            base,
        )
    )

    # 9. Acceptance
    story.append(Paragraph(data.t("9. Acceptanta si garantie"), h2))
    story.append(
        Paragraph(
            data.t(
                "Livrabilele se considera acceptate dupa validarea in UAT (User Acceptance Testing) pe scenariile agreate. "
                "Garantie: 30 zile pentru defecte de implementare descoperite in utilizarea normala (nu include schimbari "
                "de cerinte sau modificari ale sistemelor terte)."
            ),
            base,
        )
    )

    # 10. Confidentiality
    story.append(Paragraph(data.t("10. Confidentialitate si proprietate intelectuala"), h2))
    story.append(
        Paragraph(
            data.t(
                "Partile vor trata confidential informatiile tehnice si comerciale. Codul custom livrat pentru beneficiar "
                "va fi pus la dispozitia beneficiarului conform contractului semnat. Se vor respecta licentele componentelor "
                "terte utilizate (Odoo, biblioteci, API-uri)."
            ),
            base,
        )
    )

    # 11. Signatures
    story.append(Paragraph(data.t("11. Semnaturi"), h2))
    sign = Table(
        [
            [data.t("Prestator"), data.t("Beneficiar")],
            [
                Paragraph(data.t("Nume, functie: ____________________<br/>Semnatura: ____________________<br/>Data: ____________________"), small),
                Paragraph(data.t("Nume, functie: ____________________<br/>Semnatura: ____________________<br/>Data: ____________________"), small),
            ],
        ],
        colWidths=[8.2 * cm, 8.2 * cm],
    )
    sign.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F9FAFB")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(sign)

    doc.multiBuild(story)


def main() -> int:
    if not DATA_PATH.exists():
        raise SystemExit(f"Missing data file: {DATA_PATH}")
    data = OfferData(json.loads(DATA_PATH.read_text(encoding="utf-8")))
    build_pdf(data, OUT_PDF)
    print(f"Wrote: {OUT_PDF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

