# Anholt Ferry – Availability Checker

Denne bot overvåger automatisk, om der er ledige billetter til de afgange, du selv har konfigureret.

Når der dukker ledige pladser op, modtager du en **push-notifikation på din telefon** via appen [ntfy](https://ntfy.sh/).

Botten kører automatisk hvert 10. minut i GitHub Actions — din computer behøver ikke at være tændt.

---

## Hvad gør botten?

- Læser din liste af overvågninger fra `watches.json`
- Tjekker kun de overvågninger, du har markeret som aktive (`"enabled": true`)
- Sender én notifikation per overvågning, når ledige pladser opdages
- Sender én notifikation, hvis pladserne forsvinder igen (så flaget nulstilles)
- Sender **ikke** gentagne beskeder for samme status
- Gennemfører **ikke** et køb — botten reserverer ingenting
- Venter 5 sekunder mellem hver overvågning, så bookingplatformen ikke belastes

---

## Dine overvågninger – watches.json

Alle afgange du ønsker overvåget styres fra filen `watches.json` i dit repository. Du redigerer den direkte på GitHub — ingen teknisk opsætning nødvendig.

### Sådan ser filen ud

```json
[
  {
    "id": "anholt-grenaa-17maj",
    "from": "Anholt",
    "to": "Grenå",
    "date": "2026-05-17",
    "passengers": 6,
    "enabled": true
  },
  {
    "id": "grenaa-anholt-20maj",
    "from": "Grenå",
    "to": "Anholt",
    "date": "2026-05-20",
    "passengers": 4,
    "enabled": false
  }
]
```

### Hvad betyder felterne?

| Felt | Eksempel | Beskrivelse |
|---|---|---|
| `id` | `"anholt-grenaa-17maj"` | Unikt navn — bruges kun internt. Ingen mellemrum, kun bogstaver og bindestreger. |
| `from` | `"Anholt"` | Afgangssted. |
| `to` | `"Grenå"` | Ankomststed. |
| `date` | `"2026-05-17"` | Dato i format YYYY-MM-DD. |
| `passengers` | `6` | Antal passagerer der skal være plads til. |
| `enabled` | `true` | `true` = overvåg aktivt. `false` = spring over. |

---

### Sådan tilføjer du en ny overvågning

1. Gå til dit repository på GitHub.
2. Klik på filen `watches.json`.
3. Klik på **blyantsikonet** (Edit) øverst til højre.
4. Kopiér et eksisterende blok (fra `{` til `}` inkl. kommaet) og indsæt det nederst i listen.
5. Ret felterne til din nye afgang.
6. Sørg for at hvert element undtagen det **sidste** har et komma efter sin afsluttende `}`.
7. Klik **Commit changes** nederst.

**Eksempel – tilføj en ekstra afgang:**
```json
[
  {
    "id": "anholt-grenaa-17maj",
    "from": "Anholt",
    "to": "Grenå",
    "date": "2026-05-17",
    "passengers": 6,
    "enabled": true
  },
  {
    "id": "anholt-grenaa-24maj",
    "from": "Anholt",
    "to": "Grenå",
    "date": "2026-05-24",
    "passengers": 6,
    "enabled": true
  }
]
```

---

### Sådan deaktiverer du en overvågning midlertidigt

Ændr `"enabled": true` til `"enabled": false` for den pågældende overvågning. Botten springer den over ved næste kørsel.

---

### Sådan ændrer du antal passagerer eller dato

Ret blot felterne `"passengers"` eller `"date"` direkte i `watches.json` og gem filen. Botten bruger de nye værdier ved næste kørsel.

> **OBS:** Hvis du ændrer `"date"` eller `"passengers"` på en overvågning, beholder botten sit gamle notifikations-flag. Ønsker du at "nulstille" en overvågning (f.eks. for at modtage ny besked), så skift `"id"` til et nyt unikt navn.

---

## Opsætning (trin-for-trin)

### Trin 1 – Opret et ntfy-emne på din telefon

ntfy er en gratis notifikationstjeneste. Du behøver ikke oprette en konto.

1. Download appen **ntfy** på din telefon:
   - iPhone: [App Store](https://apps.apple.com/app/ntfy/id1625153022)
   - Android: [Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy)

2. Åbn appen og tryk **"+"** for at tilføje et nyt emne.

3. Skriv et unikt navn, f.eks. `anholt-faerge-annika-2026` — vær kreativ, fordi det er offentligt tilgængeligt for alle, der kender navnet.
   *(Tip: brug noget der ikke er til at gætte.)*

4. Tryk **Subscribe** — du modtager nu notifikationer til dette emne.

5. **Gem emnenavnet** — du skal bruge det i næste trin.

---

### Trin 2 – Tilføj emnenavnet som GitHub Secret

En GitHub Secret er en krypteret variabel, som kun botten kan læse.

1. Gå til dit GitHub-repository.
2. Klik på **Settings** (øverst, ved siden af Insights).
3. Klik i venstremenuen på **Secrets and variables** → **Actions**.
4. Klik på **New repository secret**.
5. Udfyld:
   - **Name:** `NTFY_TOPIC`
   - **Secret:** dit emnenavn fra Trin 1
6. Klik **Add secret**.

---

### Trin 3 – Aktivér GitHub Actions

1. Gå til fanen **Actions** i dit repository.
2. Klik **I understand my workflows, go ahead and enable them**, hvis GitHub spørger.
3. Workflowet **"Anholt Ferry – Availability Check"** starter automatisk inden for 10 minutter.

---

### Trin 4 – Test manuelt

1. Gå til **Actions** → **Anholt Ferry – Availability Check**.
2. Klik **Run workflow** → **Run workflow**.
3. Klik på kørslen når den dukker op og åbn steget **"Kør checker"** for at se loggen.

**Normal log ved ingen ledige pladser:**
```
── Overvågning: Anholt→Grenå 2026-05-17 (6 pers.) ──
Resultat: IKKE LEDIGT ✗
Stadig ikke ledigt — ingen handling
```

**Log når ledige pladser opdages:**
```
── Overvågning: Anholt→Grenå 2026-05-17 (6 pers.) ──
Resultat: LEDIGT ✓
Ny tilgængelighed — sender notifikation!
```

---

## Lokal test (valgfrit)

**Windows:**
```cmd
pip install -r requirements.txt
playwright install chromium
set NTFY_TOPIC=dit-emnenavn-her
python checker.py
```

**Mac/Linux:**
```bash
pip install -r requirements.txt
playwright install chromium
export NTFY_TOPIC=dit-emnenavn-her
python checker.py
```

Screenshots gemmes i `screenshots/` — åbn dem for at se, hvad botten så.

---

## Fejlretning

### Botten sender ingen notifikationer
- Tjek at `NTFY_TOPIC` er sat under GitHub Secrets.
- Kør workflowet manuelt med **"Vis udvidet debug-log"** aktiveret.
- Tjek screenshot-artifacts i den pågældende kørsel (klik på kørslen → Artifacts).

### Workflowet fejler
- Klik på den fejlede kørsel og åbn steget der er rødt.
- Screenshots gemmes som artifact selv ved fejl.

### Botten rapporterer "Kunne ikke afgøre tilgængelighed"
- Bookingsiden kan have ændret udseende eller være midlertidigt nede.
- Se screenshot-artifacts for at forstå hvad botten så.
- Tjek bookingsiden manuelt: [https://anholt-ferry.teambooking.dk/](https://anholt-ferry.teambooking.dk/)

---

## Tekniske detaljer

| Komponent | Valg | Begrundelse |
|---|---|---|
| Sprog | Python 3.11 | Stabil, let at læse |
| Browser-automation | Playwright (Chromium) | Robust, headless |
| HTTP-klient | httpx | Moderne, asynkron |
| Notifikationer | ntfy.sh | Gratis, ingen konto nødvendig |
| Overvågnings-config | watches.json | Redigerbar direkte på GitHub |
| State-persistens | GitHub Actions cache | Simpel, ingen database |
| Kørsel | GitHub Actions cron | Ingen server/PC nødvendig |

### State-logik per overvågning

| Situation | Handling |
|---|---|
| Ny tilgængelighed opdaget | Send "ledigt"-notifikation, sæt `notified=true` |
| Stadig ledigt (allerede notificeret) | Ingen handling — ingen spam |
| Pladser forsvundet | Send "ikke ledigt"-notifikation, nulstil `notified=false` |
| Stadig ikke ledigt | Ingen handling |

### Konfiguration (environment variables)

| Variabel | Standard | Beskrivelse |
|---|---|---|
| `NTFY_TOPIC` | *(kræves)* | Dit ntfy-emnenavn |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy-serveradresse |
| `WATCHES_FILE` | `watches.json` | Sti til overvågningslisten |
| `STATE_FILE` | `availability_state.json` | Sti til state-fil |
| `PAUSE_SECONDS` | `5` | Pause i sekunder mellem overvågninger |
