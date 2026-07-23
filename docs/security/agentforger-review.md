# Sicherheitskontrolle: „AgentForger" (CSRF gegen Agenten-/Automations-Backends)

Sicherheits-Review, ausgelöst durch den Bericht *„Manipulierter ChatGPT-Link
reicht aus, um einen autonomen KI-Agenten im Firmennetzwerk zu installieren"*
(the-decoder.de, 22.07.2026) über die von **Zenity Labs** entdeckte
**AgentForger**-Schwachstelle in OpenAIs ChatGPT Workspace-/Agent-Builder.

Dieses Dokument (1) fasst den Angriff zusammen, (2) leitet das übertragbare
Kontroll-Muster ab, (3) prüft LLM Gym gegen genau dieses Muster und (4)
dokumentiert die umgesetzte Gegenmaßnahme sowie offene Empfehlungen.

---

## 1. Kurzfassung (TL;DR)

* **Angriffsklasse:** Cross-Site Request Forgery (CSRF) gegen ein Backend, das
  privilegierte, *zustandsändernde* Aktionen ausführt — bei OpenAI das Anlegen
  eines autonomen Agenten, der Berechtigungen des Opfers erbt, Freigaben
  umgeht und sich per Zeitplan hält.
* **Für LLM Gym relevant, weil** die JSON-API dieselbe Struktur hat:
  zustandsändernde `POST`/`DELETE`-Endpunkte, mehrere davon **ohne JSON-Body
  (nur Query-Parameter)** — also ohne CORS-Preflight und damit klassisch
  CSRF-auslösbar. In der Standard-Installation (localhost, **ohne Auth**) kann
  jede vom Betreiber geöffnete Webseite die Gym-Automatik fernsteuern
  (Web-Collection anstoßen, Trainingspool an ein Git-Remote pushen =
  Datenabfluss, Modell deployen, Auto-Pipeline starten und die menschlichen
  Gates überspringen).
* **Umgesetzte Kontrolle:** `Origin`/`Referer`-Verifikation auf allen unsicheren
  Methoden (`llm_gym/csrf.py`), same-site-fähig ohne Konfiguration, mit
  Allowlist für Reverse-Proxy/Hostname-Setups. Standardmäßig **an**.
* **Bewusst offen gelassen** (siehe §6): SSRF-Härtung der einstellungsgetriebenen
  Ausgangsverbindungen (Judge-Endpoint, Collector-Websuche, `ollama pull`,
  Remote-Queue), Auth-Standard, und die Bestätigung menschlicher Gates.

---

## 2. Der Angriff: AgentForger (Zenity Labs)

Quelle: Zenity Labs, öffentlich gemacht am 22./23.07.2026; von Zenity als
*„tailored CSRF"* beschrieben. Zeitleiste: gemeldet über OpenAIs Bugcrowd-Programm
am **04.06.2026**, von OpenAI am Folgetag bestätigt, **behoben am 08.06.2026**.

**Mechanik, Schritt für Schritt:**

1. **Präparierter Link.** OpenAIs Agent Builder akzeptierte zwei URL-Parameter:
   `template_name` (wählt eine Startvorlage, z. B. „chief-of-staff") und
   `initial_assistant_prompt` (liefert die Instruktionen). Ein Angreifer
   codiert damit einen kompletten Agenten in eine URL.
2. **Ein Klick durch das Opfer.** Öffnet ein eingeloggter Mitarbeiter den Link,
   wird der Agent **im Namen des Opfers** angelegt — der Browser trägt die
   Session/Identität bei (das CSRF-Prinzip). Es genügt ein einziger Phish.
3. **Erbt Rechte, umgeht Freigaben.** Der Agent läuft **innerhalb der
   Vertrauensgrenze** der Organisation, nutzt die vom Opfer bereits
   autorisierten **Connectors** (Outlook, Slack, Teams, Kalender, File-Stores),
   und die sonst nötigen **Bestätigungen für sensible Aktionen sind
   deaktiviert**.
4. **Persistenz per Zeitplan.** Über **Scheduled Tasks** hält sich der Agent am
   Leben und zieht neue Aufträge nach: E-Mails mit Betreff beginnend mit
   `TASK` werden autonom abgearbeitet (im PoC im Minutentakt), Ergebnisse an den
   Angreifer zurückgemailt.
5. **Wirkung.** Der Angreifer hat einen **autonomen Insider**: Recon
   (Personen/Projekte aus Outlook/Slack/Teams kartieren), Suche nach Passwörtern
   und API-Keys in Chats, Identitätsmissbrauch, internes Phishing über den
   echten Teams-Account des Opfers, BEC (Business E-Mail Compromise).

**OpenAIs Fix:** der missbrauchbare URL-Parameter wurde entfernt — die
präparierte Anfrage wird nicht mehr als legitime Nutzeraktion behandelt.

---

## 3. Das übertragbare Kontroll-Muster

AgentForger ist kein Modell-/Prompt-Injection-Problem, sondern ein Problem der
**Web-/API-Schicht**. Die generalisierbaren Kontrollen:

| # | Prinzip | Konkrete Kontrolle |
|---|---------|--------------------|
| K1 | **Zustandsändernde Requests müssen nachweislich same-site sein** | `Origin`/`Referer` auf unsicheren Methoden verifizieren; SameSite-Cookies bzw. CSRF-Token; keine sensiblen Aktionen per reinem GET/Query-Parameter |
| K2 | **Menschliche Freigaben dürfen nicht per Parameter abschaltbar sein** | Approval-Gates server-seitig erzwingen, nicht über clientseitige/aufrufbare Flags |
| K3 | **Least Privilege für Connectors** | Aktionen an explizite, pro-Aktion-Autorisierung binden statt an geerbte Ambient-Rechte |
| K4 | **Persistenz-/Scheduler-Fläche kontrollieren** | Wer darf wiederkehrende/automatische Jobs anlegen? Nur authentifiziert und same-site |
| K5 | **Ausgehende Verbindungen (SSRF) absichern** | Ziel-URLs validieren/allowlisten; interne Netze/Metadaten-Endpunkte sperren |
| K6 | **Fail-safe Default & Auth** | Exponierte Instanzen standardmäßig authentifiziert; halbkonfigurierte Zustände schließen, nicht öffnen |

---

## 4. Übertragung auf LLM Gym — Angriffsflächen-Analyse

LLM Gym ist eine FastAPI-App (`llm_gym/app.py`) mit UI + JSON-API, einer
Trainings-Queue und einer Auto-Pipeline. Sie ist **kein** ChatGPT, aber sie
teilt die entscheidende Struktur: privilegierte Automatik hinter
zustandsändernden HTTP-Endpunkten.

### 4.1 CSRF-Fläche (K1) — Hauptbefund

Mehrere zustandsändernde Endpunkte nehmen **nur Query-Parameter, keinen
JSON-Body**. Solche Requests lösen im Browser **keinen CORS-Preflight** aus und
sind damit cross-site auslösbar (`<form>`/`fetch`/`<img>` mit Nebenwirkung):

| Endpunkt (`app.py`) | Wirkung bei Missbrauch |
|---------------------|------------------------|
| `POST /api/adapters/{name}/collect` (Z. 472) | Startet **ausgehende Web-Collection** (Ressourcen + SSRF-nah) |
| `POST /api/pool/{name}/push` (Z. 629) | **Pusht den Trainingspool an das konfigurierte Git-Remote → Datenabfluss** |
| `POST /api/pool/{name}/pull` (Z. 639) | Überschreibt Pool-Daten aus dem Remote |
| `POST /api/adapters/{name}/deploy` \| `/assign` (Z. 440 / 432) | Baut/registriert ein Ollama-Modell |
| `POST /api/adapters/{name}/pipeline/start` (Z. 566) | Startet die **Auto-Pipeline** (collect→train→deploy) |
| `POST /api/adapters/{name}/train` \| `/train-rlhf` (Z. 362 / 408) | Reiht Trainingsjobs ein |
| `POST /api/adapters/{name}/rollback` \| `/distill` (Z. 583 / 531) | Versions-Rollback / Gold-Promotion |
| `POST /api/pool/{name}/build` (Z. 624) | Baut `merged.jsonl` neu |
| `DELETE /api/adapters/{name}` (Z. 263) | Löscht Adapter/Artefakte |

Endpunkte **mit** JSON-Body (`POST /api/settings`, `.../pipeline/gate`,
`.../append`, `.../gold`, `.../feedback`, `ollama/pull`) erzwingen
`content-type: application/json` und damit einen Preflight, den der Browser
mangels CORS-Header standardmäßig blockt — sie sind gegen den reinen
CSRF-Vektor also *relativ* geschützt, aber nicht die obige Liste.

**Analogie zu AgentForger:** Der `collect`-/`pipeline/start`-Pfad ist das
LLM-Gym-Pendant zum „ein Link startet einen autonomen Agenten" — ein Klick
stößt die datensammelnde/trainierende/deployende Automatik an.

### 4.2 Menschliche Gates (K2)

Die Auto-Pipeline wirbt mit „zwei menschlichen Gates". Die Freigabe ist aber
schlicht ein API-Aufruf: `POST /api/adapters/{name}/pipeline/gate` mit
`{"promote": true}` (`app.py` Z. 577) bzw. die Promote-Policy in `POST
/api/settings` (`pipeline_policy`, `config.py` Z. 271). Der Gate-Endpunkt
verlangt einen JSON-Body (preflight-geschützt), ist aber weiterhin
same-origin/authentifizierungs-abhängig — die CSRF-Kontrolle aus §5 schließt
auch diesen Pfad mit ab.

### 4.3 SSRF / einstellungsgetriebene Ausgangsverbindungen (K5)

Mehrere Ziele sind **frei konfigurierbar** und werden serverseitig angesteuert:

* **Judge-Endpoint** — `judge.public_base_url` „any OpenAI-compatible endpoint"
  (`config.py` Z. 133) + API-Key aus der Umgebung.
* **Collector-Websuche** — `collector.searx_url` / `web_search` (`config.py`
  Z. 123–124), plus die abgerufenen Webseiten selbst.
* **`ollama_host`**, **Remote-Queue** `remote_queue.url` (`config.py` Z. 147),
  **`ollama pull`** beliebiger Modelle (`app.py` Z. 222).
* **Git-Remote** `git.remote` (Push/Pull).

Wer die Settings ändern kann, lenkt diese Verbindungen um (z. B. auf interne
Dienste / Cloud-Metadaten-Endpunkte). Da `POST /api/settings` preflight- und
(neu) CSRF-geschützt ist, ist der Remote-Umlenkweg für einen reinen
Web-Angreifer erschwert, aber die Ziel-URLs selbst sind **nicht allowlistet**.

### 4.4 Persistenz-/Scheduler-Fläche (K4)

* **`catalog_sync`** ist per Default **an** und startet einen Hintergrund-Loop,
  der regelmäßig einen „live shop catalog" zieht und neue Produkte in Pools
  einspeist (`app.py` Z. 75–99, `config.py` Z. 228–238) — automatischer,
  wiederkehrender Fetch + Daten-Ingest.
* **`continuous_enabled`** re-startet Pipelines eigenständig nach Cooldown
  (`config.py` Z. 278–281).

Das ist die legitime Automatik des Tools — aber genau die Art „läuft von selbst,
zieht Daten nach", die der AgentForger-Report als Persistenz-Baustein
hervorhebt. Sie muss hinter Auth + CSRF liegen (tut sie mit §5), und ihre
Fetch-Ziele fallen unter K5.

### 4.5 Auth-Default (K6)

`BasicAuthMiddleware` (`app.py` Z. 36–64) ist **nur aktiv, wenn** `LLMGYM_AUTH_USER`
*und* `LLMGYM_AUTH_PASS` gesetzt sind — Default lokal = **offen**. Positiv:
ein halbkonfigurierter Zustand (nur eine der beiden Variablen) schließt die
Instanz („fail closed", Z. 49–51) — das ist bereits K6-konform. Der offene
localhost-Default ist für ein lokales Tool vertretbar, macht aber die
CSRF-Kontrolle (§5) zur *primären* Verteidigung im Normalbetrieb.

### 4.6 Was bereits gut ist

* **Secrets:** Confluence/Jira-Tokens werden at-rest verschlüsselt (`crypto`,
  `app.py` Z. 240–247); Git-/Judge-/Websuche-Keys werden **zur Laufzeit aus der
  Umgebung/Vault** gelesen und **nie gespeichert** (`config.py`, `vault`).
* **PII:** Anonymisierung ist per Default **an** (`config.py` Z. 157–165).
* **Pfad-Sicherheit:** `valid_adapter_name` verhindert Path-Traversal
  (Smoke-Tests vorhanden).
* **Modell-Härtung:** `security.py` trainiert Injection-Resistenz **in** die
  Adapter und gated die Promotion — Defense-in-Depth auf **Modell**-Ebene.
  Wichtig: das ist eine *andere* Ebene als AgentForger; die neue CSRF-Kontrolle
  schließt die **Web-/API**-Ebene, die `security.py` bewusst nicht abdeckt.

---

## 5. Umgesetzte Kontrolle: CSRF-/Origin-Verifikation (K1)

Neu: `llm_gym/csrf.py` — eine `CsrfMiddleware`, die auf jeder unsicheren Methode
(`POST/PUT/PATCH/DELETE`) prüft, ob der Request **same-site** ist. Verdrahtet in
`app.py` (äußerste Middleware, läuft vor Auth und Handler).

**Entscheidungslogik (fail-safe, weitgehend selbstkonfigurierend):**

1. **Kein `Origin` und kein `Referer`** → erlaubt. Das sind Nicht-Browser-Clients
   (curl, Skripte, die CLI) — nicht das CSRF-Bedrohungsmodell; ein Browser hängt
   bei cross-site-Schreibzugriffen immer einen `Origin` an.
2. **`Origin: null`** (Sandbox-iframe, `data:`/`file:`) → blockiert.
3. **Quell-Host == `Host`-Header** → erlaubt. Ein gefälschter Request von
   `evil.example` trägt `Origin: https://evil.example`, aber `Host:` die eigene
   Adresse der Gym — die beiden matchen nie. Das deckt localhost **und**
   Host-erhaltende Reverse-Proxys **ohne Konfiguration** ab.
4. **Quell-Host in der Allowlist** (`settings.allowed_origins` /
   `LLMGYM_ALLOWED_ORIGINS`, plus Loopback + Bind-Host) → erlaubt.
5. Sonst → **403** mit klarer Meldung inkl. Hinweis auf die Allowlist.

Sichere Methoden (GET/HEAD/OPTIONS) werden nie geprüft — UI, Read-Pfade und der
CORS-Preflight bleiben unberührt. Da das Frontend ausschließlich relative,
same-origin-URLs nutzt (`app.js`), ändert sich für die UI nichts.

**Konfiguration**

| Setting / Env | Default | Zweck |
|---|---|---|
| `csrf_protect` | `true` | Kontrolle an/aus |
| `allowed_origins` (Liste) / `LLMGYM_ALLOWED_ORIGINS` (kommagetrennt) | leer | Zusätzliche vertrauenswürdige Browser-Origins (z. B. Proxy-Hostname), als Host oder volle Origin |

**Tests:** `scripts/smoke.py` (Abschnitte *„csrf: origin parsing + same-site
decision"* und *„csrf: middleware end-to-end"*) — Host-Parsing (inkl. IPv6,
Userinfo, `null`), Allowlist-Aufbau und End-to-End über den `TestClient`
(GET nie geblockt; cross-site-POST → 403; same-site-POST erreicht den Handler;
Client ohne Origin erreicht den Handler). Alle Smoke-Tests grün.

---

## 6. Priorisierte Empfehlungen (offen)

1. **Auth-Hinweis prominenter (K6, niedriger Aufwand).** Wenn `host` ≠
   `127.0.0.1`/loopback gebunden wird und keine Basic-Auth gesetzt ist, beim
   Start eine deutliche Warnung ausgeben (exponiert + offen).
2. **SSRF-Allowlist für Ausgangsziele (K5, mittel).** Für `judge.public_base_url`,
   `collector.searx_url`, `remote_queue.url`, `git.remote` und `ollama pull`:
   private/link-local Bereiche (`127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`,
   `169.254.0.0/16`, `::1`, Cloud-Metadaten `169.254.169.254`) standardmäßig
   sperren, Ausnahmen per Allowlist.
3. **Menschliche Gates server-seitig verankern (K2, mittel).** Sicherstellen,
   dass `pipeline_policy`/Promotion nie durch einen einzelnen aufrufbaren
   Parameter „scharf ohne Freigabe" wird; kritische Promotionen an einen
   bestätigten Gate-State koppeln.
4. **Deploy-/Push-Aktionen doppelt bestätigen (K1/K3, niedrig).** Für besonders
   sensible Endpunkte (`pool/push` = Exfiltrations-nah, `deploy`) zusätzlich zu
   CSRF eine explizite Bestätigung/Token in der UI verlangen.
5. **Security-Header (niedrig).** Restriktive `Content-Security-Policy` und
   `X-Content-Type-Options: nosniff` auf UI-Antworten, um XSS-getriebenes CSRF
   und Content-Sniffing weiter zu erschweren.

---

## 7. Quellen

* the-decoder.de — *Manipulierter ChatGPT-Link reicht aus, um einen autonomen
  KI-Agenten im Firmennetzwerk zu installieren* (22.07.2026)
* Zenity Labs — *AgentForger* (Bekanntgabe 22./23.07.2026; „tailored CSRF")
* Zenity Labs — *AgentFlayer: ChatGPT Connectors 0click Attack* (verwandte
  Connector-Angriffsserie)
* SecurityWeek — *OpenAI Fixes ChatGPT Agent Flaw That Could Let Attackers Forge
  an AI Insider*
* The Register — *One ChatGPT link could smuggle a rogue AI agent into your
  company* (23.07.2026)

> Hinweis zur Recherche: Der Originalartikel (the-decoder.de) und mehrere
> Sekundärquellen waren aus dieser Umgebung per Egress-Policy (HTTP 403) nicht
> direkt abrufbar. Die technischen Details stammen aus den zugänglichen
> Such-Zusammenfassungen der o. g. Quellen; zentrale Fakten (URL-Parameter
> `template_name`/`initial_assistant_prompt`, „tailored CSRF", Zeitleiste
> 04.–08.06.2026, Connector-/Scheduler-Missbrauch) sind quellenübergreifend
> konsistent.
