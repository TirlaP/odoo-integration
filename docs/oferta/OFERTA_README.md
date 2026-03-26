# Oferta (generare PDF)

Fisiere:
- `docs/oferta/Oferta_Sistem_Gestiune_Piese_Auto_Odoo.md` — versiune editabila (text).
- `docs/oferta/oferta_data.json` — datele variabile (beneficiar, preț, termene etc.).
- `scripts/generate_offer_pdf.py` — generator PDF (ReportLab).
- `docs/oferta/Oferta_Sistem_Gestiune_Piese_Auto_Odoo.pdf` — PDF generat.

Regenerare PDF:
```bash
python3 scripts/generate_offer_pdf.py
```

Ce să editezi înainte să trimiți oferta:
Ce sa editezi inainte sa trimiti oferta:
- `docs/oferta/oferta_data.json`: nume prestator/beneficiar, nr. oferta, date de contact, pret, transe, suport lunar.
- (Optional) `docs/oferta/Oferta_Sistem_Gestiune_Piese_Auto_Odoo.md`: daca vrei sa ajustezi textul “editabil”.
