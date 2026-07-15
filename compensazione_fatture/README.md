# Nexyzen — Invoice Offsetting (plugin Odoo 19)

**Nexyzen** (il servizio di *Camera di Compensazione*, www.nexyzen.com) compensa
i crediti ancora da incassare con le fatture non ancora pagate, senza costi
bancari. Questo modulo invia periodicamente a Nexyzen le fatture attive
(vendita) e passive (acquisto) non pagate, così che vengano compensate.

## Come funziona

1. **Impostazioni** (menu *Nexyzen → Impostazioni*): criteri di selezione
   (giorni alla scadenza, fascia di importo, clienti/fornitori), modalità
   **Automatico** o **Con approvazione manuale**, giorni di invio (lun–sab),
   e credenziali di connessione. Sempre modificabile.

2. **Ciclo giornaliero** (cron *Nexyzen: invio giornaliero fatture*): ogni
   giorno abilitato seleziona le fatture non pagate che rispettano i criteri e,
   in base alla modalità, le invia subito o prepara la lista da approvare. Le
   fatture non pagate vengono **riproposte a ogni ciclo** finché non risultano
   pagate (le già inviate non vengono escluse; Nexyzen deduplica i reinvii).

3. **Fatture in compensazione** (menu *Nexyzen → Fatture in compensazione*):
   lista **modificabile** con stato *Da inviare / Inviata / Errore*. In modalità
   manuale si corregge l'*Importo da compensare* (per compensazioni parziali),
   si rimuovono le righe indesiderate e si preme *Invia a Nexyzen*. L'importo
   modificato è quello effettivamente inviato.

## Multilingua

L'interfaccia è tradotta in **italiano, inglese, tedesco, sloveno, francese e
spagnolo** (`i18n/it.po`, `de.po`, `sl.po`, `fr.po`, `es.po`; sorgente inglese
in `i18n/compensazione_fatture.pot`). Segue automaticamente la lingua
dell'utente Odoo.

## Integrazione API

Usa il webservice REST di Nexyzen (`/connect` → JWT, poi `/send_manual`).
Vengono inviati i dati essenziali della fattura (op `ins_manuale`): tipo,
Partite IVA di creditore/debitore, data, numero, importo totale e residuo.
Documentazione: https://cameracompensazione.github.io/cc_webservice/

## Manuale utente

Manuali dettagliati con screenshot, in 6 lingue (cartella `docs/`):

| Lingua | File |
|---|---|
| 🇮🇹 Italiano | `Manuale_Utente_Nexyzen_IT.pdf` |
| 🇬🇧 English | `Manual_Nexyzen_EN.pdf` |
| 🇩🇪 Deutsch | `Handbuch_Nexyzen_DE.pdf` |
| 🇸🇮 Slovenščina | `Prirocnik_Nexyzen_SL.pdf` |
| 🇫🇷 Français | `Manuel_Nexyzen_FR.pdf` |
| 🇪🇸 Español | `Manual_Nexyzen_ES.pdf` |

Ogni manuale ha anche la versione `.html`. Screenshot in `docs/img/` (una
sottocartella per lingua: `it/`, `en/`, `de/`, `sl/`, `fr/`, `es/`).
Rigenerabili con `build_manual.py` (IT) e `build_manuals.py` (le altre).

## Endpoint e credenziali

URL di default del webservice: **`https://webapp.nexyzen.com/webservices/index.php`**
(produzione). Ogni cliente inserisce le proprie **credenziali** (codice affiliato +
token) fornite da Nexyzen — non sono precompilate.

Per i test in locale (Docker + webservice sull'host) impostare l'URL a
`http://host.docker.internal/cc_webapp/webservices/index.php`.

## Test

* `test_plugin_real.py` — invio reale di una vendita + un acquisto.
* `test_plugin_reqs.py` — riproposta periodica + importo modificabile.

```
docker exec -i odoo19_app odoo shell -d ccdemo < test_plugin_reqs.py
```
