# Anholt Ferry – Availability Checker

Denne bot overvåger automatisk, om der er ledige billetter til **6 personer** på ruten **Anholt → Grenå den 17. maj 2026**.

Når der dukker ledige pladser op, modtager du en **push-notifikation på din telefon** via appen [ntfy](https://ntfy.sh/).

Botten kører automatisk hvert 10. minut i GitHub Actions — din computer behøver ikke at være tændt.

---

## Hvad gør botten?

- Besøger Anholtfærgens bookingside
- Tjekker om 6 billetter er tilgængelige på ovenstående rute og dato
- Sender én besked, når ledige pladser opdages
- Sender én besked, hvis pladserne forsvinder igen (så du ved, det gik)
- Sender **ikke** gentagne beskeder for samme status
- Gennemfører **ikke** et køb — botten reserverer ingenting

---

## Opsætning (trin-for-trin)

### Trin 1 – Opret en gratis ntfy-konto og topic

ntfy er en gratis notifikationstjeneste. Du behøver ikke oprette en konto for at sende beskeder — du skal blot vælge et unikt emnenavn ("topic").

1. Download appen **ntfy** på din telefon:
   - iPhone: [App Store](https://apps.apple.com/app/ntfy/id1625153022)
   - Android: [Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy)

2. Åbn appen og tryk **"+"** for at tilføje et nyt emne.

3. Skriv et unikt navn, f.eks. `anholt-færge-annika-2026` — vær kreativ, fordi det er offentligt tilgængeligt for alle, der kender navnet.  
   *(Hint: brug noget der ikke er til at gætte.)*

4. Tryk **Subscribe** — du modtager nu notifikationer til dette emne.

5. **Gem emnenavnet** — du skal bruge det i næste trin.

---

### Trin 2 – Tilføj emnenavnet som GitHub Secret

En GitHub Secret er en krypteret variabel, som kun botten kan læse. Ingen andre kan se den.

1. Gå til dit GitHub-repository i en browser.
2. Klik på fanen **Settings** (tandhjulet øverst til højre på repository-siden).
3. I menuen til venstre: klik på **Secrets and variables** → **Actions**.
4. Klik på den grønne knap **New repository secret**.
5. Udfyld felterne:
   - **Name:** `NTFY_TOPIC`
   - **Secret:** dit emnenavn fra Trin 1 (f.eks. `anholt-færge-annika-2026`)
6. Klik **Add secret**.

Færdig! Botten kan nu sende notifikationer til din telefon.

---

### Trin 3 – Aktivér GitHub Actions

1. Gå til fanen **Actions** i dit repository.
2. Første gang du åbner Actions, spørger GitHub måske om du vil aktivere det — klik **I understand my workflows, go ahead and enable them**.
3. Du burde nu kunne se workflowet **"Anholt Ferry – Availability Check"** i listen.

GitHub starter automatisk det første tjek inden for 10 minutter.

---

### Trin 4 – Test manuelt

For at teste med det samme, uden at vente 10 minutter:

1. Gå til **Actions** → **Anholt Ferry – Availability Check**.
2. Klik på knappen **Run workflow** (øverst til højre).
3. Valgfrit: sæt flueben i **"Vis udvidet debug-log"** for at se detaljer.
4. Klik **Run workflow**.
5. Vent 1–3 minutter, og klik på det nye kørsel for at se loggen.

Hvis alt virker korrekt, vil du se loglinjer som:
```
Resultat: IKKE LEDIGT ✗
Stadig ikke ledigt — ingen handling
```

Hvis der er ledige billetter, vil du se:
```
Resultat: LEDIGT ✓
Ny tilgængelighed — sender notifikation!
```

---

## Lokal test (valgfrit)

Hvis du vil teste på din egen computer:

**Windows (kommandoprompt):**
```cmd
pip install -r requirements.txt
playwright install chromium
set NTFY_TOPIC=dit-emnenavn-her
python checker.py
```

**Mac/Linux (terminal):**
```bash
pip install -r requirements.txt
playwright install chromium
export NTFY_TOPIC=dit-emnenavn-her
python checker.py
```

Scriptet gemmer screenshots i mappen `screenshots/` — åbn dem for at se, hvad botten så på siden.

---

## Fejlretning

### Botten sender ingen notifikationer
- Tjek at `NTFY_TOPIC` er sat korrekt under GitHub Secrets (Trin 2).
- Kør workflowet manuelt med **debug-log** aktiveret og gennemgå loggen.
- Tjek screenshots-artifacts i GitHub Actions-kørslen (klik på kørslen → Artifacts).

### Workflowet fejler
- Klik på den fejlede kørsel under Actions for at se fejlbeskeden.
- Screenshots gemmes automatisk som artifact, selv ved fejl.

### Botten rapporterer "Kunne ikke afgøre tilgængelighed"
- Dette sker, hvis bookingsiden har ændret udseende eller er nede.
- Se screenshot-artefakter for at forstå hvad botten så.
- Åbn en issue eller tjek bookingsiden manuelt på [https://anholt-ferry.teambooking.dk/](https://anholt-ferry.teambooking.dk/).

---

## Tekniske detaljer

| Komponent | Valg | Begrundelse |
|---|---|---|
| Sprog | Python 3.11 | Stabil, let at læse |
| Browser-automation | Playwright (Chromium) | Robust, headless |
| HTTP-klient | httpx | Moderne, asynkron |
| Notifikationer | ntfy.sh | Gratis, ingen konto nødvendig |
| State-persistens | GitHub Actions cache | Simpel, ingen database |
| Kørsel | GitHub Actions cron | Ingen server/PC nødvendig |

### Hvordan botten finder tilgængelighed
1. **API-first:** Botten lytter til skjulte API-kald, som bookingsiden laver automatisk. Hvis den finder et, gemmes det og bruges direkte i fremtidige tjek (hurtigere og mere skånsomt for serveren).
2. **Fallback:** Playwright navigerer UI'en systematisk — dato, rute, afgangsrækker — og søger efter ledig/udsolgt-indikatorer.
3. **State-logik:** Én notifikation ved ny tilgængelighed. Notifikationsflag nulstilles kun, hvis status skifter til "ikke ledigt" og derefter ledigt igen.

---

## Konfiguration (environment variables)

| Variabel | Standard | Beskrivelse |
|---|---|---|
| `TARGET_DATE` | `2026-05-17` | Dato at tjekke (YYYY-MM-DD) |
| `FROM_STOP` | `Anholt` | Afgangssted |
| `TO_STOP` | `Grenå` | Ankomststed |
| `PASSENGERS` | `6` | Antal passagerer |
| `NTFY_TOPIC` | *(kræves)* | Dit ntfy-emnenavn |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy-serveradresse |
| `STATE_FILE` | `availability_state.json` | Sti til state-fil |
