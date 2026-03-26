# OFERTĂ TEHNICĂ ȘI COMERCIALĂ
## Sistem ERP pentru distribuție piese auto (Odoo) — TecDoc, ANAF e-Factura, SAGA, Portal mecanici

**Nr. ofertă:** OF-2026-01-23-001  
**Data:** 23.01.2026  
**Valabilitate:** 30 zile  

### Părți
**Prestator:** [Numele tău / Firma ta]  
**Beneficiar:** [Nume companie client]  

---

## 1. Rezumat
Prezenta ofertă acoperă implementarea completă a unui sistem ERP pentru distribuția de piese auto, construit pe Odoo Community (v18.x), cu dezvoltări custom pentru cerințele specifice din documentul de cerințe (secțiunile 2.1–2.11).

Obiectivul este livrarea unui flux operațional complet, trasabil și auditat:
**Furnizor → Recepție (NIR) → Stoc → Comandă → Livrare → Retur**, cu integrare TecDoc, ANAF e-Factura, integrare contabilitate SAGA și portal pentru mecanici.

---

## 2. Arhitectură propusă (Odoo ca „motor ERP”)
- Odoo rămâne **sursa de adevăr** pentru: produse, parteneri, comenzi, recepții/livrări, stocuri, facturi și plăți.
- Se vor utiliza mecanismele standard Odoo pentru stoc și contabilitate (rezervări, mișcări stoc, validări documente) pentru a evita duplicarea logicii critice.
- Se livrează un modul custom (`automotive_parts`) care extinde modelele și adaugă automatizări/validări/UI conform cerințelor.

---

## 3. Domeniu de aplicare (scope)
### 3.1 Inclus
- 2.1 Management comenzi și clienți (Clienți + Comenzi + stări + restricții editare)
- 2.2 Management produse și stocuri (produse, rezervări, disponibil/rezervat, trasabilitate)
- 2.3 Recepție marfă (NIR) + scanare cod bare + diferențe + legare documente
- 2.4 Integrare TecDoc (date local/sincronizat, compatibilități, metadate tehnice)
- 2.5 Automatizări (status „gata”, notificări)
- 2.6 Management plăți + alocări + recalcul sold
- 2.7 Portal mecanici
- 2.8 Audit Log complet
- 2.9 Integrare contabilitate SAGA (etapizat; export complet în MVP, sync bidirecțional după validare)
- 2.10 Documente comerciale + arhivare electronică
- 2.11 Integrare stoc/preț furnizori + comenzi către furnizori (unde există API)

### 3.2 Exclus (se poate oferta separat)
- Licențe/subscrieri (TecDoc/RapidAPI, certificate ANAF, SAGA, etc.)
- Hardware (imprimantă etichete, scanere, print server)
- Migrare istoric complet (minim: import produse/parteneri/stoc inițial)

---

## 4. Livrabile (pe module)
### 4.1 Clienți (2.1.1)
- CRUD clienți, tipuri (PF/PJ/mecanic), CUI/CNP, audit, arhivare (dezactivare).
- Sold curent calculat automat conform regulilor agreate (facturi/plăți/retururi).

### 4.2 Comenzi (2.1.2)
- Comenzi interne/externe, stări conforme, restricții de editare, log complet.
- Poziții comandă cu cantități comandate/rezervate/recepționate și status pe linie.
- Actualizare automată stări în funcție de stoc + recepții.

### 4.3 Produse + Stocuri (2.2)
- Evidență stoc disponibil/rezervat, rezervare automată pentru comenzi.
- Trasabilitate completă pe flux (documente și evenimente).
- Căutare după cod/denumire și câmpuri TecDoc.
- Etichete: generare din NIR/stoc și din factură (cu cantități).

### 4.4 Recepție (NIR) (2.3)
- Creare recepție, scanare cod bare, asociere cu produs existent.
- Opțiune creare produs nou dacă nu există (workflow asistat).
- Diferențe cantitative semnalizate înainte de validare.
- Legare factură furnizor (ANAF e-Factura sau manual) + deduplicare.

### 4.5 TecDoc (2.4)
- Stocare locală/sincronizare periodică (conform deciziei) + proces de update anual.
- Asociere produs cu cod TecDoc, compatibilități vehicule, metadate tehnice.
- Marcaj explicit pentru produse fără TecDoc.

### 4.6 Flux inteligent + notificări (2.5)
- Automatizare „gata de pregătire” când toate pozițiile sunt disponibile.
- Notificări în aplicație + email (opțional).

### 4.7 Plăți (2.6)
- Înregistrare plăți, alocare pe comandă/comenzi și (unde este necesar) pe poziții.
- Solduri recalculate automat; corelare plăți–facturi–livrări.

### 4.8 Portal mecanici (2.7)
- Autentificare separată (portal) și acces strict la datele proprii.
- Vizualizare comenzi active, istoric, sold, plăți; descărcare documente; cereri către firmă.

### 4.9 Audit Log (2.8)
- Jurnal complet pentru acțiuni: utilizator, acțiune, entitate, ID, timp, valori vechi/noi.
- Raportare și filtrare.

### 4.10 Integrare SAGA (2.9)
- Export automat: recepții/livrări/retururi în format agreat.
- Sincronizare bidirecțională: etapizat (după validarea exportului și a procesului contabil).
- Alertare discrepanțe între stoc contabil și stoc operațional.

### 4.11 Documente + arhivare (2.10)
- Generare automată documente (NIR, facturi, avize, chitanțe, documente interne).
- Arhivare electronică (atașamente + metadate + căutare).

### 4.12 Furnizori (2.11)
- Conectori (unde există API): stoc/preț/lead time + plasare comenzi.
- Implementare incrementală per furnizor (în funcție de acces și documentație).

---

## 5. Plan de implementare (faze)
### Faza 1 — Core operațional (MVP)
- Corectitudine stoc/rezervări, comenzi, recepții (NIR), audit, etichete (bază).

### Faza 2 — Integrări
- TecDoc (local/sync), ANAF e-Factura (import + dedupe + mapare), conectori furnizori (primii furnizori).

### Faza 3 — Financiar + Portal + SAGA
- Plăți/alocări, portal mecanici, export SAGA complet; sync bidirecțional etapizat.

### Faza 4 — Go-live + Stabilizare
- Migrare date agreată, training, UAT, monitorizare, bugfix.

---

## 6. Termene (estimare)
Estimare: **12–16 săptămâni** (în funcție de deciziile de integrare, disponibilitatea API-urilor furnizorilor și feedback-ul din UAT).

---

## 7. Condiții comerciale
### 7.1 Preț
**Preț total implementare:** 6.500 EUR (fără TVA)  
*Notă:* TVA se aplică conform legislației.

### 7.2 Tranșe de plată (recomandat)
1. 30% — Semnare + pornire proiect  
2. 40% — Livrare MVP (flux stoc/comenzi/NIR + audit)  
3. 30% — Go-live + stabilizare inițială  

### 7.3 Suport și mentenanță (opțional)
**250 EUR/lună** — Suport și mentenanță (best-effort, bugfix, mici ajustări, monitorizare).

---

## 8. Presupuneri, dependențe și excluderi
### 8.1 Presupuneri / dependențe
- Beneficiarul furnizează acces la datele necesare (listă produse, stocuri, furnizori, coduri, formate).
- Beneficiarul furnizează sau procură credențiale/licențe pentru TecDoc/RapidAPI și ANAF (certificat/credite).
- Pentru integrarea SAGA: se validează formatul de import/export acceptat în fluxul contabilului (XML/CSV etc.).
- Etichetele: se confirmă model imprimantă și limbaj (de ex. ZPL/EPL) înainte de implementare.

### 8.2 Excluderi (rezumat)
- Licențe/subscrieri și credențiale oferite de terți (TecDoc/RapidAPI, ANAF, SAGA etc.).
- Hardware și infrastructură fizică (imprimantă etichete, scanere, print server).
- Limitări/indisponibilități ale API-urilor furnizorilor (depinde de terți).

---

## 9. Acceptanță și garanție
Livrabilele se consideră acceptate după validarea în UAT (User Acceptance Testing) pe scenariile agreate.

**Garanție:** 30 zile pentru defecte de implementare descoperite în utilizarea normală (nu include schimbări de cerințe
sau modificări ale sistemelor terțe).

---

## 10. Confidențialitate și proprietate intelectuală
Părțile vor trata confidențial informațiile tehnice și comerciale. Codul custom livrat pentru beneficiar va fi pus la
dispoziția beneficiarului conform contractului semnat. Se vor respecta licențele componentelor terțe utilizate (Odoo,
biblioteci, API-uri).

---

## 11. Semnături
**Prestator:** ____________________  Data: __________  Semnătură: __________  
**Beneficiar:** ___________________  Data: __________  Semnătură: __________  
