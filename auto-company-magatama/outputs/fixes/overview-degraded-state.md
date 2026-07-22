# Fix-Vorschlag: DB-Ausfall darf nicht wie „0 Fixes" aussehen

**Problem:** In `packages/core/src/routes/overview.ts` verschlucken
`loadResolverRows()` und `loadRecentFindings()` DB-Fehler und liefern leere
Daten → das Dashboard zeigt **0**, ununterscheidbar von echtem „keine Fixes".
Genau das hat den Datenverlust-Schreck ausgelöst.

**Ziel:** Bei DB-Fehler ein `degraded`-Signal ans UI geben („Live-Daten
nicht verfügbar"), statt irreführender Nullen. Zahlen bleiben unberührt.

## Patch (Vorschlag, gegen `packages/core/src/routes/overview.ts`)

```diff
 async function loadResolverRows(): Promise<ResolverRow[]> {
-  const result = await query<ResolverRow>(
-    `WITH classified AS ( ... )`,
-    [],
-  ).catch(() => ({ rows: [] as ResolverRow[] }));
-  return result.rows;
+  // Fehler NICHT verschlucken — Aufrufer soll degraded erkennen koennen.
+  const result = await query<ResolverRow>(
+    `WITH classified AS ( ... )`,
+    [],
+  );
+  return result.rows;
 }

 async function buildResolverStatsPayload(): Promise<Record<string, unknown>> {
-  const rows = await loadResolverRows();
-  const byResolver = buildResolverMap(rows);
-  return {
-    totalVerifiedFixes: sumResolverField(rows, 'total'),
-    totalAuditArtifacts: sumResolverField(rows, 'audit_artifacts'),
-    resolvers: { ... },
-  };
+  let rows: ResolverRow[] = [];
+  let degraded = false;
+  try {
+    rows = await loadResolverRows();
+  } catch (err) {
+    degraded = true;            // DB nicht erreichbar / Query-Fehler
+    req.log?.error({ err }, 'resolver-stats query failed');
+  }
+  const byResolver = buildResolverMap(rows);
+  return {
+    degraded,                   // <-- UI kann jetzt "Live-Daten offline" zeigen
+    totalVerifiedFixes: sumResolverField(rows, 'total'),
+    totalAuditArtifacts: sumResolverField(rows, 'audit_artifacts'),
+    resolvers: { ... },
+  };
 }
```

Und in `loadRecentFindings()` analog: den `catch` behalten, aber ein
`findingsDegraded`-Flag mitliefern statt still `[]`.

## Cache-Hinweis (bereits korrekt, nur dokumentieren)

`buildCachedResolverStatsPayload` cached schon **nur bei `hasEvidence`
(>0)** — d. h. eine 0/degraded-Antwort wird nicht gecached, echte Zahlen
kommen sofort nach DB-Recovery zurück. Das ist gut so; mit dem `degraded`-Flag
sollte der Cache zusätzlich nur bei `!degraded` schreiben.

## Frontend (Dashboard)

Wenn `degraded === true`: Karten NICHT als „0" rendern, sondern als
„⚠ Live-Daten offline — letzter bekannter Stand: …" (optional: letzten
gecachten Wert mit Zeitstempel zeigen). Analog zum bestehenden Self-Heal-
Alarm-Pattern.

## Als Ticket (in die 1.0-Punch-List, EPIC 0)

> **T0.6** — DB-Fehler in `overview.ts` (resolver-stats + recentFindings)
> als `degraded`-Status ans UI propagieren statt still `0/[]`. Frontend zeigt
> „Live-Daten offline" statt Nullen. Verhindert Fehlalarm „Datenverlust".
> Akzeptanz: bei gestoppter DB zeigt das Dashboard einen Offline-Zustand,
> keine 0-Zähler; nach DB-Start sofort echte Zahlen.
