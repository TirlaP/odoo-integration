                        CERINȚE TEHNICE FUNCȚIONALE
2.1 Management comenzi și clienți
2.1.1 Modul Clienți
Sistemul trebuie să permită gestionarea completă a entității Client, incluzând următoarele
funcționalități:
Operațiuni permise:
   •   Adăugare client
   •   Editare client
   •   Dezactivare / ștergere logică client
   •   Vizualizare listă clienți
   •   Căutare și filtrare clienți
Câmpuri minime client:
   •   ID client (unic, generat automat)
   •   Tip client (persoană fizică / persoană juridică / mecanic)
   •   Nume / Denumire firmă
   •   CUI / CNP (unde este cazul)
   •   Adresă
   •   Telefon
   •   Email
   •   Sold curent (calculat automat)
   •   Status (activ / inactiv)
   •   Data creării
   •   Istoric modificări
Reguli:
   •   Ștergerea unui client nu este permisă dacă există comenzi asociate (doar dezactivare)
   •   Soldul clientului este calculat automat din:
           o    total comenzi
           o    plăți efectuate
           o    retururi
2.1.2 Modul Comenzi
Sistemul trebuie să permită gestionarea comenzilor interne și externe.
Operațiuni permise:
   •   Creare comandă
   •   Editare comandă (doar în stări permise)
   •   Anulare comandă
   •   Vizualizare detalii comandă
   •   Listare comenzi cu filtre multiple
Structură comandă:
   •   ID comandă
   •   Tip comandă (internă / externă)
   •   Client asociat
   •   Stare comandă:
           o    Draft
           o    În așteptare aprovizionare
           o    Parțial recepționată
           o    Complet recepționată
           o    Gata de pregătire
           o    Livrată
           o    Anulată
   •   Dată creare
   •   Dată livrare estimată
   •   Responsabil intern
   •   Observații
Structură poziție comandă:
   •   Produs
   •   Cantitate comandată
   •   Cantitate rezervată din stoc
   •   Cantitate recepționată
   •   Preț unitar
   •   TVA
   •   Stare poziție (necompletată / completată)
Reguli:
   •   O comandă poate fi modificată doar până în starea „Gata de pregătire”
   •   Starea comenzii este actualizată automat în funcție de stoc și recepții
   •   Toate modificările sunt înregistrate în Audit Log


2.2 Management produse și stocuri
2.2.1 Modul Produse
Operațiuni permise:
   •   Adăugare produs
   •   Editare produs
   •   Dezactivare produs
   •   Vizualizare produse
   •   Căutare după cod, denumire, compatibilitate TecDOC
Câmpuri produs:
   •   ID produs
   •   Cod intern
   •   Cod furnizor
   •   Cod de bare
   •   Denumire
   •   Descriere
   •   Compatibilități TecDOC
   •   Preț de achiziție
   •   Preț de vânzare
   •   TVA
   •   Furnizor principal
   •   Status (activ/inactiv)
De asemenea, trebuie inclusă integrarea cu imprimanta de etichete care să permit
urmatoarele:
   •   Generarea de etichete pentru fiecare produs direct din NIR sau stoc.
   •   Generarea de etichete per factură (adică per produs și cantitate conform facturii).


2.2.2 Modul Stocuri
Funcționalități:
   •   Evidență stoc în timp real
   •   Rezervare automată stoc pentru comenzi active
   •   Vizualizare stoc per:
           o     produs
           o     locație (opțional)
           o     stare (disponibil / rezervat)
Reguli:
   •   Stocul disponibil = stoc total – stoc rezervat
   •   Rezervarea se face automat la confirmarea comenzii
   •   Eliberarea stocului se face la anularea comenzii


2.2.3 Management ciclul de viață produs
Sistemul trebuie să urmărească următorul flux obligatoriu:
Furnizor → Recepție → Stoc → Comandă → Livrare → Retur
Fiecare etapă:
   •   este înregistrată
   •   are document asociat
   •   este trasabilă în Audit Log


2.3 Recepție marfă de la furnizori
Funcționalități:
   •   Creare recepție marfă
   •   Scanare cod de bare pentru identificare produs
   •   Asociere automată cu produs existent
   •   Creare produs nou dacă nu există
Integrare documente:
   •   Preluare facturi din ANAF e-Factura
   •   Mapare automată produse din factură cu produse din system (cu mențiunea de a nu se
       dubla facturile care intră ulterior din e-Factura)
   •   Dacă integrarea directă nu este posibilă:
              o   utilizare AI OCR pentru extragere date din PDF/imagini
Reguli:
   •   Recepția actualizează stocul automat
   •   Recepția este legată obligatoriu de un furnizor
   •   Diferențele cantitative sunt semnalizate utilizatorului


2.4 Integrare TecDOC
Cerințe tehnice:
   •   Baza TecDOC este stocată local sau sincronizată periodic
   •   Actualizare anuală controlată
   •   Produsele pot fi asociate cu:
              o   cod TecDOC
              o   compatibilități vehicule
              o   metadate tehnice
Reguli:
   •   TecDOC este sursă de adevăr pentru compatibilități
   •   Produsele pot exista fără TecDOC, dar sunt marcate explicit


2.5 Flux inteligent de procesare comenzi
Automatizări:
   •   Corelare automată între:
              o   produse comandate
              o   produse recepționate
              o   stoc disponibil
   •   Calcul automat al statusului „ready”
Notificări:
   •   Sistemul notifică utilizatorii când:
              o   toate pozițiile sunt disponibile
           o   comanda este gata de pregătire
   •   Notificări prin:
           o   aplicație
           o   email (opțional)


2.6 Management plăți
Funcționalități:
   •   Înregistrare plăți
   •   Alocare plată:
           o   per comandă/comenzi
           o   per poziție de comandă
   •   Vizualizare solduri în timp real
Reguli:
   •   O plată poate fi parțială
   •   Soldurile sunt recalculate automat
   •   Corelare obligatorie între:
           o   plăți
           o   facturi
           o   livrări


2.7 Portal mecanici
Drepturi mecanic:
   •   Autentificare separată
   •   Vizualizare doar datele proprii
Funcționalități:
   •   Vizualizare comenzi active
   •   Vizualizare sold și datorii
   •   Istoric comenzi și plăți
   •   Descărcare documente associate
   •   Posibilitatea inițierii unor cereri către firmă
2.8 Jurnal de activitate (Audit Log)
Cerințe:
   •   Înregistrare automată a tuturor acțiunilor
Structură log:
   •   ID log
   •   Utilizator
   •   Acțiune
   •   Entitate afectată
   •   ID entitate
   •   Dată și oră
   •   Valori vechi / noi (unde este cazul)


2.9 Integrare contabilitate – SAGA Cantitativ-Valoric sau alt program de contabilitate
care oferă posibilitatea preluării datelor de intrare și ieșire
Funcționalități:
   •   Export automat date către SAGA:
           o     recepții
           o     livrări
           o     retururi
   •   Sincronizare bidirecțională
Reguli:
   •   Stocul contabil = stoc operațional
   •   Orice diferență este semnalizată


2.10 Documente și operațiuni comerciale
Documente suportate:
   •   Avize de însoțire marfă
   •   Chitanțe
   •   Facturi
   •   Documente interne
   •   NIR
Reguli:
   •   Documentele sunt generate automat
   •   Asociate obligatoriu cu:
           o   comandă
           o   client
   •   Arhivare electronica
2..11 Vizualizarea stocului la furnizori și efectuarea comenzilor la furnizori (unde este
posibil)
Acest modul trebuie să permită, prin interconectarea cu API oferite de furnizori, posibilitatea
vizualizării stocului (împreună cu preț și cantitate) a produselor la aceștia, dar și efectuarea de
comenzi.
