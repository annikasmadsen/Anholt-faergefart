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
    "ntfy_topic": "anholt-annika-2026-xyz",
    "enabled": true
  },
  {
    "id": "grenaa-anholt-20maj",
    "from": "Grenå",
    "to": "Anholt",
    "date": "2026-05-20",
    "passengers": 4,
    "ntfy_topic": "anholt-mor-2026-abc",
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
| `time` | `"07:50"` | **Valgfrit.** Afgangstidspunkt (HH:MM). Brug det, når der er **flere afgange samme dag** på samme rute, så botten kun kigger på den rigtige afgang. Udelades feltet, tjekker botten alle afgange på dagen (som hidtil). |
| `passengers` | `6` | Antal passagerer der skal være plads til. |
| `ntfy_topic` | `"anholt-annika-2026-xyz"` | ntfy-emnenavnet som notifikationen sendes til. Hvert familiemedlem bruger sit eget emne. |
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
    "ntfy_topic": "anholt-annika-2026-xyz",
    "enabled": true
  },
  {
    "id": "anholt-grenaa-24maj",
    "from": "Anholt",
    "to": "Grenå",
    "date": "2026-05-24",
    "passengers": 6,
    "ntfy_topic": "anholt-mor-2026-abc",
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

### To afgange samme dag – vælg tidspunkt

Er der to afgange samme dag på samme rute, tilføjer du feltet `"time"` med afgangstidspunktet (HH:MM), så botten kun kigger på netop den afgang:

```json
{
  "id": "grenaa-anholt-6juli-formiddag",
  "from": "Grenå",
  "to": "Anholt",
  "date": "2026-07-06",
  "time": "07:50",
  "passengers": 1,
  "ntfy_topic": "anholt-faerge-julie",
  "enabled": true
}
```

Vil du overvåge **begge** afgange, opretter du to overvågninger med hver sit `"id"` og `"time"` (f.eks. `"07:50"` og `"14:00"`). Udelader du `"time"`, tjekker botten alle afgange på dagen som hidtil.

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

### Trin 2 – Tilføj emnenavnet i watches.json

Emnenavnet fra Trin 1 skrives direkte ind i `watches.json` som `ntfy_topic` på hver afgang du vil overvåge. Se eksemplet ovenfor under "Sådan ser filen ud".

Hvert familiemedlem bruger sit eget emnenavn — du angiver blot det rigtige emne på den afgang, der tilhører dem.

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
python checker.py
```

**Mac/Linux:**
```bash
pip install -r requirements.txt
playwright install chromium
python checker.py
```

Screenshots gemmes i `screenshots/` — åbn dem for at se, hvad botten så.

---

## Fejlretning

### Botten sender ingen notifikationer
- Tjek at `ntfy_topic` er udfyldt korrekt på den relevante afgang i `watches.json`.
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
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy-serveradresse |
| `WATCHES_FILE` | `watches.json` | Sti til overvågningslisten |
| `STATE_FILE` | `availability_state.json` | Sti til state-fil |
| `PAUSE_SECONDS` | `5` | Pause i sekunder mellem overvågninger |

ntfy-emnet konfigureres per afgang i `watches.json` via feltet `ntfy_topic`.
