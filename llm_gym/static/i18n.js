// Bilingual (DE/EN) support. The English text is the key; DE holds the German.
// applyI18n() swaps [data-i18n] textContent and [data-i18n-ph] placeholders, and
// shows the matching [data-lang] block (used for the FAQ). tr() does the same for
// strings built in JS. Toggling reloads so dynamic content re-renders in the new
// language (the active tab is preserved via the URL #hash).
"use strict";

const DE = {
  // nav / chrome
  "local adapter trainer": "lokaler Adapter-Trainer",
  "Dashboard": "Übersicht",
  "Adapters": "Adapter",
  "Training pool": "Trainingspool",
  "Auto-pipeline": "Auto-Pipeline",
  "Settings": "Einstellungen",
  "FAQ & help": "FAQ & Hilfe",
  "backend": "Backend",

  // dashboard
  "⚙ One step to real training": "⚙ Ein Schritt zum echten Training",
  "You are in simulate mode": "Du bist im Simulate-Modus",
  " — the full flow works, but no real weights are trained yet. Paste this once in the gym folder to enable real training on ":
    " — der ganze Ablauf funktioniert, aber es werden noch keine echten Gewichte trainiert. Führe das einmal im Gym-Ordner aus, um echtes Training zu aktivieren auf ",
  "this machine": "dieser Maschine",
  "Copy": "Kopieren",
  "Copied": "Kopiert",
  "Then restart with": "Dann neu starten mit",
  ". The": ". Der",
  "badge above turns into your backend. Full details in": "-Hinweis oben wird zu deinem Backend. Details unter",
  "Quick start": "Schnellstart",
  "Pull a base model": "Basismodell ziehen",
  "(below) — use the recommended size.": "(unten) — die empfohlene Größe nehmen.",
  "Define an adapter": "Adapter definieren",
  "on the": "im",
  "tab — say what it should do.": "-Tab — beschreiben, was er können soll.",
  "Fill its pool": "Pool füllen",
  "tab — examples, academic finds, gold.": "-Tab — Beispiele, akademische Funde, Gold.",
  "Train": "Trainieren",
  ", then": ", dann",
  "assign": "zuweisen",
  "it to your app. Stuck? See": "an deine App. Hängt's? Siehe",
  "FAQ": "FAQ",
  "System": "System",
  "What this machine can run, and which base model is recommended.":
    "Was diese Maschine fahren kann und welches Basismodell empfohlen wird.",
  "Hardware & backend": "Hardware & Backend",
  "Recommended base & options": "Empfohlene Basis & Optionen",
  "Model": "Modell", "Needs": "Braucht", "Runnable": "Lauffähig",
  "Ollama models": "Ollama-Modelle",
  "Pull": "Ziehen", "Refresh": "Aktualisieren",
  "Name": "Name", "Family": "Familie", "Size": "Größe",
  "Training queue": "Trainings-Queue",
  "One worker. One adapter at a time. Cooldown decides when a lane may run again.":
    "Ein Worker. Immer nur ein Adapter. Der Cooldown bestimmt, wann eine Lane wieder darf.",
  "Status": "Status", "Verdict / starts in": "Verdict / startet in",
  "Run log": "Lauf-Log",

  // adapters
  "Define what an adapter should do, bind it to a base model, point it at a pool, train it, then assign it.":
    "Definiere, was ein Adapter können soll, binde ihn an ein Basismodell, zeige ihn auf einen Pool, trainiere und weise ihn zu.",
  "Defined adapters": "Definierte Adapter",
  "Base": "Basis", "Rank": "Rang", "Trained": "Trainiert",
  "Name": "Name",
  "What is it for? (one line)": "Wofür ist er? (eine Zeile)",
  "Objective — what it should be able to do": "Ziel — was er können soll",
  "Capabilities (one per line)": "Fähigkeiten (eine pro Zeile)",
  "Acceptance prompts (one per line — used for smoke tests)":
    "Acceptance-Prompts (eine pro Zeile — für Smoke-Tests)",
  "Research brief — what it is about (drives the collector)":
    "Research-Brief — worum es geht (steuert den Collector)",
  "Subject": "Thema",
  "Scope (what's in / out)": "Scope (was rein / raus)",
  "Key sub-questions (one per line)": "Kern-Teilfragen (eine pro Zeile)",
  "Languages (comma)": "Sprachen (Komma)",
  "Recency (years, 0 = any)": "Aktualität (Jahre, 0 = beliebig)",
  "Confluence source (optional · token AES-256 encrypted)":
    "Confluence-Quelle (optional · Token AES-256-verschlüsselt)",
  "Search a Confluence instance for this adapter":
    "Eine Confluence-Instanz für diesen Adapter durchsuchen",
  "token set": "Token gesetzt",
  "Base URL (REST) — e.g. https://confluence.example.com/rest/api":
    "Basis-URL (REST) — z. B. https://confluence.example.com/rest/api",
  "Auth": "Auth",
  "Bearer (PAT, Server/DC)": "Bearer (PAT, Server/DC)",
  "Basic (email + token, Cloud)": "Basic (E-Mail + Token, Cloud)",
  "Email (Basic only)": "E-Mail (nur Basic)",
  "Space key (optional)": "Space-Key (optional)",
  "API token (stored AES-256 encrypted; leave blank to keep the saved one)":
    "API-Token (AES-256 verschlüsselt gespeichert; leer lassen = gespeicherten behalten)",
  "Jira source — incl. ITSM/JSM (optional · token AES-256 encrypted)":
    "Jira-Quelle — inkl. ITSM/JSM (optional · Token AES-256-verschlüsselt)",
  "Search a Jira instance for this adapter":
    "Eine Jira-Instanz für diesen Adapter durchsuchen",
  "Base URL (REST v2) — e.g. https://jira.example.com/rest/api/2":
    "Basis-URL (REST v2) — z. B. https://jira.example.com/rest/api/2",
  "Project key (optional, e.g. ITSM desk)": "Projekt-Key (optional, z. B. ITSM-Desk)",
  "Base model": "Basismodell", "Pool": "Pool",
  "Learning rate": "Lernrate", "Iterations": "Iterationen", "Dropout": "Dropout",
  "alpha = 2 × rank, scale = alpha / √rank (rsLoRA). The base you pick here is the base it will be served on — adapters can't move between bases.":
    "alpha = 2 × rank, scale = alpha / √rank (rsLoRA). Die hier gewählte Basis ist die, auf der er serviert wird — Adapter können die Basis nicht wechseln.",
  "Save definition": "Definition speichern",
  "Show assign plan": "Zuweisungs-Plan zeigen",
  "Assignment plan —": "Zuweisungs-Plan —",
  "Turn the trained adapter into a model your app can call via Ollama.":
    "Mach aus dem trainierten Adapter ein Modell, das deine App über Ollama aufruft.",
  "Run ollama create (needs GGUF)": "ollama create ausführen (braucht GGUF)",

  // pool
  "Curated JSONL data per pool. Fill it well, and the adapter trains well.":
    "Kuratierte JSONL-Daten pro Pool. Gut gefüllt = guter Adapter.",
  "Stats": "Statistik", "Build merged": "Merged bauen", "Push to Git": "Zu Git pushen",
  "Search the pool": "Pool durchsuchen", "Search": "Suchen",
  "Add an example": "Beispiel hinzufügen",
  "One JSON object per line (chat format)": "Ein JSON-Objekt pro Zeile (Chat-Format)",
  "Split": "Split", "Append": "Hinzufügen",
  "Find data — academic sources": "Daten finden — akademische Quellen",
  "Search university / research output (OpenAlex, arXiv, Semantic Scholar). Results are":
    "Universitäts-/Forschungsoutput durchsuchen (OpenAlex, arXiv, Semantic Scholar). Treffer sind",
  "candidates": "Kandidaten",
  ": edit and verify before they become gold.": ": bearbeiten und verifizieren, bevor sie Gold werden.",
  "Gold-standard input": "Gold-Standard-Input",
  "Gold only when the solution was executed": "Gold nur, wenn die Lösung ausgeführt",
  "and": "und",
  "verified. Anything else is a candidate, not gold.":
    "verifiziert wurde. Alles andere ist Kandidat, nicht Gold.",
  "Prompt (the problem)": "Prompt (das Problem)",
  "Solution (verified-correct answer)": "Lösung (verifiziert korrekte Antwort)",
  "Executed OK": "Ausgeführt OK", "Verified": "Verifiziert",
  "Add as gold": "Als Gold hinzufügen",

  // pipeline
  "Pick an adapter and let the gym run the whole loop: collect data → analyse → (you pick the tier) → train → check → verify → cooldown → (you decide promote).":
    "Adapter wählen und das Gym den ganzen Loop fahren lassen: sammeln → analysieren → (du wählst das Tier) → trainieren → prüfen → verifizieren → Cooldown → (du entscheidest Promote).",
  "Run full pipeline": "Komplette Pipeline",
  "Recommend sources (AI)": "Quellen empfehlen (KI)",
  "Collect data only": "Nur Daten sammeln",
  "Analyse only": "Nur analysieren",
  "Verify trained adapter": "Trainierten Adapter prüfen",
  "Recommendation": "Empfehlung",
  "The brief is thin — answer these so the collector can target the right data:":
    "Der Brief ist dünn — beantworte das, damit der Collector die richtigen Daten trifft:",
  "Plan with these answers": "Mit diesen Antworten planen",
  "Save answers to the adapter's brief": "Antworten in den Brief des Adapters speichern",
  "Where to search": "Wo gesucht werden soll",
  "Suggested search queries": "Vorgeschlagene Suchanfragen",
  "Suggestions to sharpen the adapter": "Vorschläge, um den Adapter zu schärfen",
  "Apply suggestions to adapter": "Vorschläge in den Adapter übernehmen",
  "Collect with this plan": "Mit diesem Plan sammeln",
  "Pipeline": "Pipeline",
  "① Choose the data tier to train on": "① Datenstufe fürs Training wählen",
  "Only examples at this tier or better go into training. Higher tier = cleaner data, fewer examples.":
    "Nur Beispiele ab dieser Stufe gehen ins Training. Höhere Stufe = sauberere Daten, weniger Beispiele.",
  "Platin only": "Nur Platin", "Gold & up": "Gold & höher", "Silver & up": "Silver & höher",
  "Pool analysis": "Pool-Analyse",
  "Data quality tiers": "Datenqualitäts-Stufen",
  "Forecast (estimate)": "Prognose (Schätzung)",
  "Capability coverage": "Fähigkeits-Abdeckung",
  "Verification": "Verifikation",
  "Recommended cooldown": "Empfohlener Cooldown",
  "The adapter cannot be retrained before this expires — stored on the adapter and enforced by the queue.":
    "Der Adapter kann vor Ablauf nicht neu trainiert werden — am Adapter hinterlegt und von der Queue erzwungen.",
  "② Promote this result?": "② Dieses Ergebnis promoten?",
  "Promote marks this run as the adapter's champion. Discard keeps the previous one.":
    "Promote macht diesen Lauf zum Champion des Adapters. Discard behält den vorherigen.",
  "Promote": "Promoten", "Discard": "Verwerfen",
  "Pipeline log": "Pipeline-Log",

  // settings
  "Everything configurable. No secrets are stored here.":
    "Alles konfigurierbar. Hier werden keine Secrets gespeichert.",
  "Models & Ollama": "Modelle & Ollama",
  "Ollama host": "Ollama-Host",
  "Base model — small": "Basismodell — klein",
  "Base model — large": "Basismodell — groß",
  "Active base model (blank = use recommended)": "Aktives Basismodell (leer = empfohlenes)",
  "Training backend": "Trainings-Backend",
  "Cooldown & training": "Cooldown & Training",
  "Seed cooldown (min)": "Seed-Cooldown (min)",
  "Full cooldown (min)": "Voll-Cooldown (min)",
  "Max (min)": "Max (min)",
  "Default rank": "Standard-Rang", "Default iters": "Standard-Iter",
  "Git remote for the pool (optional)": "Git-Remote für den Pool (optional)",
  "Remote URL (blank = local only)": "Remote-URL (leer = nur lokal)",
  "Branch": "Branch",
  "Tokens are read from the environment at push time, never saved here.":
    "Tokens werden zur Push-Zeit aus der Umgebung gelesen, nie hier gespeichert.",
  "Data collector": "Daten-Collector",
  "Max pages / run": "Max. Seiten / Lauf",
  "Max seconds / run": "Max. Sekunden / Lauf",
  "Rate limit (s)": "Rate-Limit (s)",
  "Web search provider (optional)": "Web-Such-Provider (optional)",
  "none (keyless sources only)": "keiner (nur schlüssellose Quellen)",
  "SearXNG (self-hosted, keyless)": "SearXNG (selbstgehostet, schlüssellos)",
  "Brave (key)": "Brave (Key)", "Tavily (key)": "Tavily (Key)",
  "SearXNG URL (if used)": "SearXNG-URL (falls genutzt)",
  "Verification judge": "Verifizierungs-Judge",
  "Mode": "Modus",
  "local (Ollama, offline, free)": "lokal (Ollama, offline, kostenlos)",
  "public AI (OpenAI-compatible)": "Public AI (OpenAI-kompatibel)",
  "Local judge model (blank = large base)": "Lokales Judge-Modell (leer = große Basis)",
  "Eval model — answers acceptance prompts (blank = adapter/base)":
    "Eval-Modell — beantwortet die Acceptance-Prompts (leer = Adapter/Basis)",
  "Blank uses the assigned adapter, else its base model. Set one to force a specific Ollama model.":
    "Leer nutzt den zugewiesenen Adapter, sonst dessen Basismodell. Setze eines, um ein bestimmtes Ollama-Modell zu erzwingen.",
  "judge": "Judge",
  "answered by the assigned adapter": "beantwortet vom zugewiesenen Adapter",
  "answered by the base model (un-adapted baseline)": "beantwortet vom Basismodell (Baseline ohne Adapter)",
  "answered by the configured eval model": "beantwortet vom konfigurierten Eval-Modell",
  "answered by the local MLX adapter": "beantwortet vom lokalen MLX-Adapter",
  "no eval model available — score is not meaningful": "kein Eval-Modell verfügbar — Score nicht aussagekräftig",
  "Public endpoint (OpenAI-compatible base URL)": "Public-Endpoint (OpenAI-kompatible Basis-URL)",
  "Public model": "Public-Modell",
  "Save settings": "Einstellungen speichern",

  // dynamic (app.js)
  "yes": "ja", "no": "nein", "recommended": "empfohlen",
  "PASS": "PASS", "FAIL": "FAIL",
  "No jobs yet.": "Noch keine Jobs.",
  "No models pulled yet.": "Noch keine Modelle geladen.",
  "No adapters defined yet.": "Noch keine Adapter definiert.",
  "No matches.": "Keine Treffer.",
  "No results.": "Keine Ergebnisse.",
  "(define an adapter first)": "(zuerst einen Adapter definieren)",
  "(none yet)": "(noch keiner)",
  "log": "Log", "delete": "löschen", "rename": "umbenennen",
  "Rename adapter — this moves the spec, trained adapter, its pool and history.":
    "Adapter umbenennen — verschiebt Spec, trainierten Adapter, Pool und Verlauf.",
  "Rename failed.": "Umbenennen fehlgeschlagen.",
  "Collect": "Sammeln", "Analyse": "Analysieren", "Tier": "Tier", "Check": "Prüfen",
  "Verify": "Verifizieren", "Cooldown": "Cooldown", "Done": "Fertig",
  "not started": "nicht gestartet",
  "thinking… (local LLM, may take ~20s)": "denkt nach… (lokales LLM, ~20s)",
  "running…": "läuft…",
  "domain": "Domäne", "sources": "Quellen", "recommended tier": "empf. Tier",
  "expected": "erwartet", "val loss ~": "Val-Loss ~", "confidence": "Konfidenz",
  "usable @tier": "nutzbar @Tier",
  "recommended": "empfohlen", "locked until": "gesperrt bis",
  "pool": "Pool", "train": "train", "valid": "valid", "gold": "gold",
  "collected": "collected", "raw (uncurated)": "raw (unkuratiert)",
  "unique": "unique", "duplicates": "Dubletten",
  "total / unique": "gesamt / unique",
  "gold / train / collected": "gold / train / collected",
  "valid (holdout)": "valid (Holdout)",
  "with citation": "mit Zitat", "verified": "verifiziert", "avg relevance": "Ø Relevanz",
  "OS / arch": "OS / Arch", "GPU": "GPU", "Apple Silicon": "Apple Silicon",
  "CUDA": "CUDA", "Memory budget": "Speicher-Budget", "Backend": "Backend",
  "Ollama installed": "Ollama installiert",
  "stage": "Phase", "status": "Status",
  "pending": "wartet", "running": "läuft", "done": "fertig", "failed": "fehlgeschlagen",
  "cancelled": "abgebrochen", "soon": "gleich", "min": "min",
  "starts in ~": "startet in ~", "no log yet": "noch kein Log",
  "Live": "Live",
  "What the gym is doing right now, and where each adapter trains.":
    "Was das Gym gerade tut — und wo welcher Adapter trainiert.",
  "Where training runs": "Wo trainiert wird",
  "Training now": "Training jetzt",
  "Recent runs": "Letzte Läufe",
  "Name required.": "Name erforderlich.",
  "Short id: letters, digits, '.', '_', '-' — no spaces or umlauts (it becomes a folder name).":
    "Kurz-ID: Buchstaben, Ziffern, '.', '_', '-' — keine Leerzeichen oder Umlaute (wird ein Ordnername).",
  "Could not save the adapter — names may use letters, digits, '.', '_', '-' only.":
    "Adapter konnte nicht gespeichert werden — Namen dürfen nur Buchstaben, Ziffern, '.', '_', '-' enthalten.",
  "Result": "Ergebnis",
  "Verify": "Verifizieren",
  "Collecting & pipelines": "Sammeln & Pipelines",
  "When": "Wann",
  "No training running.": "Kein Training aktiv.",
  "Nothing collecting right now.": "Gerade wird nichts gesammelt.",
  "trains on": "trainiert auf",
  "waiting": "wartet", "collecting": "sammelt",
  "active": "aktiv", "queued": "in Warteschlange",
  "training (GPU queue)": "trainiert (GPU-Queue)",
  "waiting for approval": "wartet auf Freigabe",
  "waiting for GPU": "wartet auf GPU",
  "Waiting for the GPU": "Wartet auf die GPU",
  "another adapter is still training/verifying. Not stuck.":
    "ein anderer Adapter trainiert/verifiziert noch. Kein Hänger.",
  "training": "Training", "running acceptance prompts": "läuft Akzeptanz-Prompts",
  "judging": "bewertet", "head-to-head eval": "Head-to-Head-Vergleich",
  "security red-team": "Security-Redteam", "deploying": "deployt",
  "generating": "generiert", "grading": "bewertet", "remaining": "verbleibend",
  "waiting for your decision": "wartet auf deine Entscheidung",
  "Working — this can take a few minutes...": "Arbeitet — das kann ein paar Minuten dauern...",
  "Cannot promote — this adapter fails the security check":
    "Kann nicht freigegeben werden — dieser Adapter besteht die Sicherheitsprüfung nicht",
  "{reason}. A leaky adapter is never auto-shipped; discard and improve the data before trying again.":
    "{reason}. Ein unsicherer Adapter wird nie automatisch ausgeliefert — verwerfen und die Daten verbessern, bevor es erneut versucht wird.",
  "Cannot promote — this candidate loses on the frozen eval set":
    "Kann nicht freigegeben werden — dieser Kandidat verliert auf dem eingefrorenen Test-Set",
  "It fails to beat the untrained base model on held-out questions.":
    "Er schlägt das untrainierte Basismodell bei zurückgehaltenen Testfragen nicht.",
  "It loses to the currently live version on held-out questions.":
    "Er verliert bei zurückgehaltenen Testfragen gegen die aktuell live laufende Version.",
  "very good": "sehr gut", "good": "gut", "weak": "schwach",
  "Approve": "Freigabe", "Discard + collect more": "Verwerfen + neu sammeln",
  "approve": "freigeben", "discard": "verwerfen",
  "rollback": "zurückrollen",
  "base": "Basis",
  "base model": "Basismodell",
  "This training made the adapter WORSE, not better":
    "Dieses Training hat den Adapter SCHLECHTER gemacht, nicht besser",
  "The untrained base model already scores {base} on this exact test — this trained version only reaches {trained}. Discard it and collect more/better data.":
    "Das untrainierte Basismodell erreicht bei genau diesem Test schon {base} Punkte — diese trainierte Version nur {trained}. Verwerfen und mehr/bessere Daten sammeln.",
  "This training is worse than the version already live — do not promote it":
    "Dieses Training ist schlechter als die bereits live laufende Version — nicht freigeben",
  "pass": "bestanden",
  "Not sure what pass rate, avg score, or a ⚠ warning means? See":
    "Nicht sicher, was Pass-Rate, Avg-Score oder eine ⚠-Warnung bedeuten? Siehe",
  "Resource profile (keeps the machine usable)": "Ressourcen-Profil (Maschine bleibt nutzbar)",
  "work — most headroom for you": "work — maximaler Spielraum für dich",
  "balanced": "ausgewogen",
  "fast — when you're away": "fast — wenn du weg bist",
  "Promote / rollback policy": "Freigabe-/Rollback-Policy",
  "very good auto, rest confirm": "sehr gut automatisch, Rest bestätigen",
  "confirm everything": "alles bestätigen",
  "fully automatic": "voll automatisch",
  "Continuous mode (auto-retrain on new gold)": "Continuous-Modus (Auto-Retrain bei neuem Gold)",
  "Min new gold to retrain": "Min. neues Gold für Retrain",
  "Roll back to the previous version?": "Auf die vorherige Version zurückrollen?",
  "Rolled back to": "Zurückgerollt auf",
  "Nothing to roll back to.": "Keine frühere Version vorhanden.",
  "host": "Host", "device": "Gerät", "memory": "Speicher",
  "up": "an", "down": "aus",
};

const LANG_KEY = "llmgym_lang";
function curLang() { return localStorage.getItem(LANG_KEY) || "de"; }
function tr(s) { return curLang() === "de" ? (DE[s] || s) : s; }

function applyI18n(root) {
  root = root || document;
  const de = curLang() === "de";
  document.documentElement.lang = curLang();
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    const en = el.getAttribute("data-i18n");
    el.textContent = de ? (DE[en] || en) : en;
  });
  root.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    const en = el.getAttribute("data-i18n-ph");
    el.setAttribute("placeholder", de ? (DE[en] || en) : en);
  });
  document.querySelectorAll("[data-lang]").forEach((el) => {
    el.style.display = el.getAttribute("data-lang") === curLang() ? "" : "none";
  });
  const btn = document.getElementById("langToggle");
  if (btn) btn.textContent = de ? "EN" : "DE";
}

function toggleLang() {
  localStorage.setItem(LANG_KEY, curLang() === "de" ? "en" : "de");
  location.reload();   // dynamic content re-renders in the new language; #hash keeps the tab
}

window.tr = tr;
window.curLang = curLang;
window.applyI18n = applyI18n;
window.toggleLang = toggleLang;
document.addEventListener("DOMContentLoaded", () => applyI18n());
