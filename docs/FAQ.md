# LLM Gym — FAQ

The same FAQ as the app's **FAQ & help** tab, kept here so it's readable
without running the app (and versioned alongside the code it explains).

## English

**What is this, in one sentence?**
A small workshop to teach a local AI model one specific job — by training a
tiny add-on (an adapter) instead of retraining the whole model.

**What is an adapter, and why not just train the model?**
The base model (here: Qwen2.5, run by Ollama) stays frozen. An adapter is a
small set of extra weights — often a few MB — trained on top of it. Training a
full model is slow, expensive, and gives you one big thing you can't undo. An
adapter trains in minutes, is easy to delete, and you can keep many adapters
for one base.

**Do I need an expensive GPU?**
No. A modern laptop is enough for the 3B base. The Dashboard measures your
machine and recommends a size: roughly 8 GB → 3B, 16 GB → 7B, 28 GB → 14B. No
GPU still works on CPU — just slowly.

**It says "simulate". How do I turn on real training?**
Simulate mode runs the whole flow with placeholder weights. To train for real,
install a backend (the Dashboard shows the exact command): Apple Silicon →
`pip install "mlx-lm>=0.18"`; NVIDIA/CPU →
`pip install torch transformers peft datasets trl`. Then restart. The first
real run downloads the base weights once.

**What do Platin / Gold / Silver / Bronze mean?**
Quality tiers from an explainable 0–100 score (relevance, source authority,
citation, completeness, verified, uniqueness): Platin ≥ 85, Gold 70–84, Silver
50–69, Bronze < 50 (discarded). Before training you pick the lowest tier
allowed in.

**What does the data collector do?**
It builds search queries from the adapter's brief, gathers candidates from
open sources (and optional Confluence/Jira/web), removes PII, and scores each
into a tier. It obeys robots.txt and stops at a budget. You are responsible
for the licence of anything you train on.

**Why only one training queue?**
One GPU, one job at a time — on purpose. Two trainings at once fight for
memory and crash. A second job waits. The cooldown also spaces runs out so a
model isn't retrained on nothing.

**What do pass rate, avg score, and "very good / good / weak" mean?**
After training, the adapter answers a fixed set of test questions ("acceptance
prompts") and a judge model grades each answer 0–100 and marks it passed or
not. Avg score is the average of those grades; pass rate is the % that passed
a stricter bar. The tier (very good / good / weak) is derived from both
together — a high avg score with a low pass rate usually means the answers are
on-topic but miss something the task specifically requires (e.g. a required
field or format), which is worth a look even at "good".

**What does "worse than base model" mean, and why does it matter more than a
good-looking score?**
Before grading the trained adapter, the gym also grades the SAME UNTRAINED
base model on the SAME questions — this is the fairness check. "Worse than
base model" means training made things worse: the plain, un-adapted model
would answer these questions better than the one you just spent GPU time
training. This can hide behind a perfectly reasonable-looking score (e.g.
"good · 88% · avg 74" looked fine until you saw the untrained base model
scored 89 on the identical test) — so always check for this warning before
approving, not just the score. It's a different, stricter check than "worse
than the live version" (which only compares against your own previously-
approved adapter, not the untrained model) — an adapter can pass one check and
fail the other.

**How do I use the finished adapter in my app?**
On the Adapters tab click "Show assign plan". It prints the exact steps to
fuse the adapter into the base, convert it, and register it with Ollama under
a name. Your app then changes by one line — it calls that model name.

**Something doesn't work — what do I check first?**
Open the app via the http:// URL, not the HTML file. Is the Ollama dot green?
A job stuck on "pending" usually means another is training or a cooldown is
active — that's normal.

---

## Deutsch

**Was ist das, in einem Satz?**
Eine kleine Werkstatt, die ein lokales KI-Modell für eine bestimmte Aufgabe
schärft — indem ein winziges Zusatzteil (ein Adapter) trainiert wird, statt
das ganze Modell neu zu trainieren.

**Was ist ein Adapter, und warum nicht das Modell trainieren?**
Das Basismodell (hier: Qwen2.5 über Ollama) bleibt eingefroren. Ein Adapter
sind ein paar wenige MB Zusatzgewichte, die obendrauf trainiert werden. Ein
Vollmodell zu trainieren ist langsam, teuer und unumkehrbar. Ein Adapter
trainiert in Minuten, ist leicht löschbar, und du kannst viele Adapter für
eine Basis behalten.

**Brauche ich eine teure GPU?**
Nein. Ein moderner Laptop reicht für die 3B-Basis. Das Dashboard misst deine
Maschine und empfiehlt eine Größe: grob 8 GB → 3B, 16 GB → 7B, 28 GB → 14B.
Ganz ohne GPU geht es auf der CPU — nur langsam.

**Da steht „simulate". Wie schalte ich echtes Training an?**
Der Simulate-Modus fährt den ganzen Ablauf mit Platzhalter-Gewichten. Für
echtes Training ein Backend installieren (das Dashboard zeigt den genauen
Befehl): Apple Silicon → `pip install "mlx-lm>=0.18"`; NVIDIA/CPU →
`pip install torch transformers peft datasets trl`. Dann neu starten. Der
erste echte Lauf lädt die Basisgewichte einmalig.

**Was bedeuten Platin / Gold / Silver / Bronze?**
Qualitätsstufen aus einem erklärbaren 0–100-Score (Relevanz, Quellenautorität,
Zitat, Vollständigkeit, verifiziert, Einzigartigkeit): Platin ≥ 85, Gold
70–84, Silver 50–69, Bronze < 50 (verworfen). Vor dem Training wählst du die
niedrigste erlaubte Stufe.

**Was macht der Daten-Collector?**
Er baut Suchanfragen aus dem Brief des Adapters, sammelt Kandidaten aus
offenen Quellen (und optional Confluence/Jira/Web), entfernt PII und stuft
jeden Treffer in ein Tier ein. Er beachtet robots.txt und stoppt bei einem
Budget. Für die Lizenz der Trainingsdaten bist du verantwortlich.

**Warum nur eine Trainings-Queue?**
Eine GPU, ein Job gleichzeitig — bewusst. Zwei Trainings auf einmal kämpfen um
Speicher und stürzen ab. Ein zweiter Job wartet. Der Cooldown verteilt Läufe
zudem zeitlich.

**Was bedeuten Pass-Rate, Avg-Score und „sehr gut / gut / schwach"?**
Nach dem Training beantwortet der Adapter einen festen Satz Testfragen
(„Acceptance Prompts"), und ein Richter-Modell bewertet jede Antwort 0–100
und markiert sie als bestanden oder nicht. Avg-Score ist der Durchschnitt
dieser Bewertungen; Pass-Rate ist der Anteil, der eine strengere Schwelle
besteht. Die Stufe (sehr gut / gut / schwach) ergibt sich aus beidem
zusammen — ein hoher Avg-Score bei niedriger Pass-Rate heißt meist: die
Antworten sind thematisch richtig, verfehlen aber etwas, das die Aufgabe
konkret verlangt (z. B. ein Pflichtfeld oder Format) — das lohnt einen Blick,
selbst bei „gut".

**Was bedeutet „schlechter als das Basismodell", und warum zählt das mehr als
ein guter Score?**
Bevor der trainierte Adapter bewertet wird, bewertet der Gym auch das GLEICHE
UNTRAINIERTE Basismodell auf denselben Fragen — das ist der Fairness-Check.
„Schlechter als das Basismodell" heißt: das Training hat es schlechter
gemacht — das reine, unangepasste Modell würde diese Fragen besser
beantworten als das gerade mit GPU-Zeit trainierte. Das kann sich hinter
einem völlig plausibel aussehenden Score verstecken (z. B. sah
„gut · 88% · avg 74" in Ordnung aus, bis sich zeigte, dass das untrainierte
Basismodell 89 auf demselben Test erreichte) — also vor jeder Freigabe immer
auf diese Warnung prüfen, nicht nur auf den Score. Das ist ein anderer,
strengerer Check als „schlechter als die Live-Version" (der nur gegen deinen
eigenen, bereits freigegebenen Adapter vergleicht, nicht gegen das
untrainierte Modell) — ein Adapter kann den einen Check bestehen und den
anderen nicht.

**Wie nutze ich den fertigen Adapter in meiner App?**
Im Adapter-Tab auf „Zuweisungs-Plan zeigen" klicken. Er druckt die genauen
Schritte, um den Adapter in die Basis zu fusionieren, zu konvertieren und
unter einem Namen in Ollama zu registrieren. Deine App ändert sich um eine
Zeile — sie ruft diesen Modellnamen.

**Etwas geht nicht — was prüfe ich zuerst?**
Die App über die http://-Adresse öffnen, nicht die HTML-Datei. Ist der
Ollama-Punkt grün? Ein Job auf „pending" heißt meist, dass ein anderer
trainiert oder ein Cooldown läuft — das ist normal.
