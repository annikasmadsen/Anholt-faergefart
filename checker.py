#!/usr/bin/env python3
"""
Anholt Ferry Availability Checker
==================================
Tjekker om der er ledige billetter til 6 personer på ruten
Anholt → Grenå den 17. maj 2026. Sender besked via ntfy.sh,
hvis der dukker ledige pladser op.

Konfiguration via environment variables:
  NTFY_TOPIC   – ntfy-topic (kræves for notifikationer)
  TARGET_DATE  – dato at tjekke, ISO-format (default: 2026-05-17)
  FROM_STOP    – afgangssted (default: Anholt)
  TO_STOP      – ankomststed (default: Grenå)
  PASSENGERS   – antal passagerer (default: 6)
  NTFY_SERVER  – ntfy-serveradresse (default: https://ntfy.sh)
  STATE_FILE   – sti til state-fil (default: availability_state.json)
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, date as date_type
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import (
    TimeoutError as PlaywrightTimeout,
    async_playwright,
    Page,
    Response,
)

# ─── Konfiguration ────────────────────────────────────────────────────────────
TARGET_DATE     = os.getenv("TARGET_DATE", "2026-05-17")
FROM_STOP       = os.getenv("FROM_STOP", "Anholt")
TO_STOP         = os.getenv("TO_STOP", "Grenå")
PASSENGERS      = int(os.getenv("PASSENGERS", "6"))
NTFY_TOPIC      = os.getenv("NTFY_TOPIC", "")
NTFY_SERVER     = os.getenv("NTFY_SERVER", "https://ntfy.sh")
STATE_FILE      = Path(os.getenv("STATE_FILE", "availability_state.json"))
SCREENSHOTS_DIR = Path("screenshots")

BOOKING_URL = "https://anholt-ferry.teambooking.dk/timetable?lang=da"

# Maksimal timeout for hele tjekket (5 minutter)
OVERALL_TIMEOUT_SECONDS = 300

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ─── State-håndtering ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """Indlæser den gemte tilstand fra JSON-filen."""
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            log.info(
                f"Gemt tilstand: available={state.get('available')}, "
                f"notified={state.get('notified')}"
            )
            return state
        except Exception as exc:
            log.warning(f"Kunne ikke læse state-fil, starter frisk: {exc}")
    return {
        "available": False,
        "notified": False,
        "last_check": None,
        "last_error": None,
        "discovered_api": None,  # Gemmes, hvis vi finder en direkte API
    }


def save_state(state: dict) -> None:
    """Gemmer den aktuelle tilstand til JSON-filen."""
    state["last_check"] = datetime.utcnow().isoformat() + "Z"
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info(f"Tilstand gemt → {STATE_FILE}")


# ─── Notifikationer ───────────────────────────────────────────────────────────

def send_ntfy(available: bool) -> None:
    """Sender en push-notifikation via ntfy.sh."""
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC er ikke sat — notifikation springes over")
        return

    if available:
        title    = "Ledige billetter på Anholtfærgen!"
        message  = (
            f"Der ser ud til at være ledige billetter på Anholtfærgen: "
            f"{PASSENGERS} personer, {FROM_STOP} til {TO_STOP}, "
            f"17. maj 2026. Tjek og book her: {BOOKING_URL}"
        )
        priority = "high"
        tags     = "white_check_mark,ship"
    else:
        title    = "Anholtfærgen: Pladser ikke længere ledige"
        message  = (
            f"{PASSENGERS} billetter {FROM_STOP}→{TO_STOP} den 17. maj 2026 "
            f"er ikke længere ledige ifølge den seneste kontrol."
        )
        priority = "low"
        tags     = "x"

    url = f"{NTFY_SERVER.rstrip('/')}/{NTFY_TOPIC}"
    try:
        resp = httpx.post(
            url,
            content=message.encode("utf-8"),
            headers={
                "Title":        title,
                "Priority":     priority,
                "Tags":         tags,
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=15,
        )
        resp.raise_for_status()
        log.info(f"Notifikation sendt til '{NTFY_TOPIC}': [{priority}] {title}")
    except httpx.HTTPError as exc:
        log.error(f"Kunne ikke sende ntfy-notifikation: {exc}")


# ─── Screenshots ──────────────────────────────────────────────────────────────

async def screenshot(page: Page, label: str) -> Path:
    """Gemmer et screenshot med tidsstempel og label."""
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{ts}_{label}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        log.info(f"Screenshot gemt: {path}")
    except Exception as exc:
        log.warning(f"Kunne ikke gemme screenshot '{label}': {exc}")
    return path


# ─── Direkte API-tjek (hurtigste metode, bruges hvis vi kender endpointet) ────

def try_api_check(api_url: str) -> Optional[bool]:
    """
    Forsøger at kalde teambooking-API'en direkte, hvis vi kender URL'en
    fra en tidligere Playwright-session.

    Returnerer True/False/None.
    """
    try:
        log.info(f"Forsøger direkte API-kald: {api_url}")
        resp = httpx.get(api_url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        log.debug(f"API-svar: {str(data)[:500]}")
        return parse_api_for_availability(api_url, data)
    except Exception as exc:
        log.warning(f"Direkte API-kald mislykkedes: {exc}")
        return None


# ─── Playwright-baseret tjek ──────────────────────────────────────────────────

async def check_with_playwright() -> tuple[Optional[bool], Optional[str]]:
    """
    Bruger Playwright til at navigere bookingsiden og tjekke tilgængelighed.

    Returnerer (available: bool|None, discovered_api_url: str|None).
    - available: True = ledigt, False = udsolgt, None = ukendt
    - discovered_api_url: URL til teambooking-API'en, hvis den blev fanget
    """
    captured_responses: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
            ],
        )
        context = await browser.new_context(
            # Pålidelig browser-user-agent, der ligner en rigtig bruger
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="da-DK",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        # ── Fang alle JSON-svar fra teambooking ──────────────────────────────
        async def on_response(response: Response) -> None:
            url = response.url
            if "teambooking.dk" not in url:
                return
            if response.status != 200:
                return
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                body = await response.json()
                captured_responses.append({"url": url, "data": body})
                log.info(f"API-svar fanget: {url}")
            except Exception:
                pass

        page.on("response", on_response)

        try:
            # ── Trin 1: Indlæs timetable-siden ───────────────────────────────
            log.info(f"Indlæser: {BOOKING_URL}")
            await page.goto(BOOKING_URL, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(3_000)

            log.info(f"Sidetitel: {await page.title()} | URL: {page.url}")
            await screenshot(page, "01_indlæst")

            # Log fangede API-kald
            _log_captured(captured_responses, "ved sideindlæsning")
            discovered_api = _find_best_api_url(captured_responses)

            # ── Trin 2: Naviger til måldatoen ────────────────────────────────
            log.info(f"Navigerer til dato: {TARGET_DATE}")
            await navigate_to_date(page, TARGET_DATE)
            await page.wait_for_timeout(2_500)
            await screenshot(page, "02_dato_valgt")

            _log_captured(captured_responses, "efter datonavigation")
            if not discovered_api:
                discovered_api = _find_best_api_url(captured_responses)

            # ── Trin 3: Vælg rute-retning, hvis muligt ───────────────────────
            log.info(f"Vælger rute: {FROM_STOP} → {TO_STOP}")
            await select_route_direction(page)
            await page.wait_for_timeout(2_000)
            await screenshot(page, "03_rute_valgt")

            _log_captured(captured_responses, "efter rutevalg")
            if not discovered_api:
                discovered_api = _find_best_api_url(captured_responses)

            # ── Trin 4: Analyser tilgængelighed ──────────────────────────────
            log.info("Analyserer tilgængelighed på siden...")
            available = await detect_availability(page, captured_responses)
            return available, discovered_api

        except PlaywrightTimeout as exc:
            log.error(f"Timeout under tjek: {exc}")
            await screenshot(page, "fejl_timeout")
            return None, None

        except Exception as exc:
            log.error(f"Uventet fejl: {exc}", exc_info=True)
            await screenshot(page, "fejl_uventet")
            return None, None

        finally:
            await browser.close()


def _log_captured(responses: list, context: str) -> None:
    """Logger fangede API-svar til fejlretning."""
    if not responses:
        return
    log.info(f"Fangede {len(responses)} API-svar {context}:")
    for item in responses:
        log.info(f"  → {item['url']}")
        log.debug(f"     Data: {str(item['data'])[:300]}")


def _find_best_api_url(responses: list) -> Optional[str]:
    """
    Finder den bedste API-URL til fremtidigt direkte brug.
    Prioriterer URLs der ligner timetable/departures-endpoints.
    """
    keywords = ["timetable", "departure", "afgang", "sejlplan", "availability"]
    for item in responses:
        url = item["url"].lower()
        if any(k in url for k in keywords):
            return item["url"]
    # Returnér den første teambooking-URL som fallback
    for item in responses:
        if "api.teambooking.dk" in item["url"]:
            return item["url"]
    return None


# ─── Datonavigation ───────────────────────────────────────────────────────────

async def navigate_to_date(page: Page, target_date: str) -> None:
    """
    Forsøger at navigere timetable-siden til den ønskede dato.
    Prøver i rækkefølge: URL-parameter → date-input → kalender-knapper.
    """
    # Strategi 1: URL-parametre (hurtigst og mest pålidelig)
    date_nodash = target_date.replace("-", "")
    for url_candidate in [
        f"{BOOKING_URL}&date={target_date}",
        f"{BOOKING_URL}&date={date_nodash}",
        f"{BOOKING_URL}&dag={target_date}",
        f"{BOOKING_URL}&selectedDate={target_date}",
    ]:
        try:
            await page.goto(url_candidate, wait_until="networkidle", timeout=30_000)
            await page.wait_for_timeout(1_500)
            page_text = await page.evaluate("() => document.body.innerText")
            # Tjek om datoen nu vises (17 + maj/may)
            if "17" in page_text and (
                "maj" in page_text.lower() or "may" in page_text.lower()
            ):
                log.info(f"URL-dato-navigation lykkedes: {url_candidate}")
                return
        except Exception as exc:
            log.debug(f"URL-strategi mislykkedes ({url_candidate}): {exc}")

    log.info("URL-navigation bekræftede ikke datoen, prøver UI-navigation...")

    # Strategi 2: date-input felt
    for selector in [
        'input[type="date"]',
        'input[placeholder*="dato" i]',
        'input[placeholder*="date" i]',
        'input[name*="date" i]',
        'input[name*="dato" i]',
    ]:
        try:
            inp = page.locator(selector).first
            if await inp.is_visible(timeout=2_000):
                await inp.fill(target_date)
                await inp.press("Tab")
                await page.wait_for_timeout(1_500)
                log.info(f"Dato sat via '{selector}'")
                return
        except Exception:
            pass

    # Strategi 3: Klik på "næste dag/uge" knapper for at nå datoen
    log.info("Forsøger kalender-knapnavigation...")
    await navigate_calendar_buttons(page, target_date)


async def navigate_calendar_buttons(page: Page, target_date: str) -> None:
    """
    Klikker på 'næste'-knapper for at nå frem til målдатoen.
    Maks 60 klik som sikkerhedsspærre.
    """
    target  = date_type.fromisoformat(target_date)
    today   = date_type.today()
    days_ahead = (target - today).days

    if days_ahead <= 0:
        log.info("Måldatoen er i dag eller fortiden — springer datonavigation over")
        return

    # Mulige selectors for "næste dag" eller "næste uge" knapper
    next_selectors = [
        'button[aria-label*="næste" i]',
        'button[aria-label*="next" i]',
        'button[title*="næste" i]',
        'button[title*="next" i]',
        '[class*="next-day"]',
        '[class*="nextDay"]',
        '[class*="forward"]',
        'button:has-text("›")',
        'button:has-text(">")',
        'button:has-text("→")',
    ]

    clicks = 0
    for _ in range(min(days_ahead, 60)):
        clicked = False
        for sel in next_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1_000):
                    await btn.click()
                    await page.wait_for_timeout(400)
                    clicks += 1
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            log.warning("Kunne ikke finde 'næste'-knap til kalendernavigation")
            break

    log.info(f"Kalendernavigation: klikket {clicks} gange fremad")


# ─── Rutevalg ─────────────────────────────────────────────────────────────────

async def select_route_direction(page: Page) -> None:
    """
    Forsøger at vælge rute-retningen Anholt → Grenå,
    hvis der findes en retnings-vælger på siden.
    """
    # Tekstbaserede selectors (mest robuste)
    for selector in [
        f"text={FROM_STOP} til {TO_STOP}",
        f"text={FROM_STOP} - {TO_STOP}",
        f"text={FROM_STOP} → {TO_STOP}",
        f"text={FROM_STOP} > {TO_STOP}",
        f'label:has-text("{FROM_STOP}")',
        f'button:has-text("{FROM_STOP}")',
    ]:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2_000):
                await el.click()
                await page.wait_for_timeout(1_000)
                log.info(f"Rute valgt via: '{selector}'")
                return
        except Exception:
            pass

    # Dropdown-selector med Anholt som option
    try:
        for sel_el in await page.locator("select").all():
            for opt in await sel_el.locator("option").all():
                text = (await opt.text_content() or "").strip()
                if FROM_STOP in text and (TO_STOP in text or "Gren" in text):
                    val = await opt.get_attribute("value") or ""
                    await sel_el.select_option(value=val)
                    await page.wait_for_timeout(1_000)
                    log.info(f"Rute valgt fra dropdown: '{text}'")
                    return
    except Exception as exc:
        log.debug(f"Dropdown-rutevalg mislykkedes: {exc}")

    log.info("Ingen rutevalg-element fundet — antager standardvisning er korrekt")


# ─── Tilgængelighedsanalyse ───────────────────────────────────────────────────

async def detect_availability(
    page: Page, captured: list
) -> Optional[bool]:
    """
    Analyserer siden og fangede API-svar for at afgøre,
    om 6 passagerbilletter er tilgængelige.

    Returnerer True (ledigt) / False (udsolgt) / None (ukendt).
    """
    # ── Tjek API-svar først (mest pålidelig kilde) ────────────────────────
    for item in captured:
        result = parse_api_for_availability(item["url"], item["data"])
        if result is not None:
            log.info(f"Tilgængelighed fra API: {result} ({item['url']})")
            return result

    # ── Analyser sidetekst ────────────────────────────────────────────────
    page_text = await page.evaluate("() => document.body.innerText")
    log.info(
        f"Sidetekst til analyse (første 3000 tegn):\n"
        f"{'─'*60}\n{page_text[:3000]}\n{'─'*60}"
    )

    # Søg efter kapacitetstal: "X ledige" eller "Ledige: X"
    seat_matches = re.findall(r"(\d+)\s*ledige", page_text, re.IGNORECASE)
    for count_str in seat_matches:
        count = int(count_str)
        log.info(f"Fundet antal ledige pladser: {count}")
        if count >= PASSENGERS:
            return True
        if count == 0:
            return False

    # Søg efter dansk-sprogede udsolgt/ledig signaler
    text_lower = page_text.lower()

    unavailable_signals = [
        "udsolgt", "fuld", "ingen ledige pladser",
        "ikke muligt at bestille", "lukket for booking",
        "sold out", "fully booked",
    ]
    available_signals = [
        "ledige pladser", "ledige billetter", "bestil nu",
        "vælg afgang", "book nu",
    ]

    found_unavail = [s for s in unavailable_signals if s in text_lower]
    found_avail   = [s for s in available_signals if s in text_lower]
    log.info(f"Udsolgt-signaler: {found_unavail}")
    log.info(f"Ledig-signaler:   {found_avail}")

    # ── Analyser specifikke afgangs-elementer ────────────────────────────
    departure_result = await scan_departure_elements(page)
    if departure_result is not None:
        return departure_result

    # ── Heuristik fallback ────────────────────────────────────────────────
    if found_unavail and not found_avail:
        return False
    if found_avail and not found_unavail:
        # Forsigtig: bekræft at det vedrører vores rute
        if FROM_STOP in page_text:
            return True

    log.warning(
        "Kunne ikke afgøre tilgængelighed entydigt — "
        "se screenshot og log for manuel kontrol"
    )
    return None


async def scan_departure_elements(page: Page) -> Optional[bool]:
    """
    Scanner afgangs-rækker/-kort på siden og leder efter
    status-indikatorer for ruten Anholt → Grenå.
    """
    selectors = [
        "[class*='departure']",
        "[class*='sailing']",
        "[class*='afgang']",
        "[class*='trip-row']",
        "[class*='timetable-row']",
        "tr",
        "[role='row']",
    ]

    for sel in selectors:
        try:
            elements = await page.locator(sel).all()
            if not elements:
                continue
            log.info(f"Fandt {len(elements)} elementer med '{sel}'")
            for el in elements[:30]:  # Tjek de første 30
                text = (await el.text_content() or "").strip()
                if not text:
                    continue
                # Er det relevant for vores rute?
                if FROM_STOP not in text and TO_STOP not in text:
                    continue
                log.info(f"Afgangs-element: {text[:150]!r}")
                text_lower = text.lower()
                # Udsolgt-check
                if any(
                    p in text_lower
                    for p in ["udsolgt", "fuld", "ingen", "lukket", "sold out"]
                ):
                    log.info("  → IKKE LEDIG")
                    return False
                # Ledig-check (med passagertal)
                seat_match = re.search(r"(\d+)\s*ledige", text_lower)
                if seat_match:
                    seats = int(seat_match.group(1))
                    log.info(f"  → {seats} ledige pladser")
                    return seats >= PASSENGERS
                if any(
                    p in text_lower
                    for p in ["ledig", "available", "vælg", "bestil"]
                ):
                    log.info("  → LEDIG (signal)")
                    return True
        except Exception as exc:
            log.debug(f"Selector '{sel}' fejlede: {exc}")

    return None


def parse_api_for_availability(url: str, data) -> Optional[bool]:
    """
    Forsøger at udlæse tilgængelighed fra et teambooking API-svar.
    Returnerer True/False/None.
    """
    if not isinstance(data, (dict, list)):
        return None

    # Rekursiv søgning efter kendte kapacitetsfelter
    def search(obj, depth: int = 0) -> Optional[bool]:
        if depth > 6:
            return None
        if isinstance(obj, dict):
            for key, val in obj.items():
                key_l = key.lower()
                # Kapacitets-/tilgængeligheds-nøgler
                if any(
                    k in key_l
                    for k in [
                        "available", "ledige", "capacity", "seats",
                        "passengers", "pax", "remaining", "free",
                    ]
                ):
                    if isinstance(val, int):
                        log.info(f"API-felt '{key}' = {val}")
                        return val >= PASSENGERS
                    if isinstance(val, bool):
                        return val
                # Udsolgt-felter
                if any(k in key_l for k in ["sold_out", "soldout", "full", "udsolgt"]):
                    if isinstance(val, bool):
                        return not val  # sold_out=True → False (ikke ledigt)
                result = search(val, depth + 1)
                if result is not None:
                    return result
        elif isinstance(obj, list):
            # Filtrer på dato og rute, hvis muligt
            for item in obj:
                if isinstance(item, dict):
                    # Check om dette element vedrører vores dato/rute
                    item_str = json.dumps(item, ensure_ascii=False).lower()
                    date_match = (
                        TARGET_DATE in item_str
                        or TARGET_DATE.replace("-", "") in item_str
                    )
                    route_match = (
                        FROM_STOP.lower() in item_str
                        or "anholt" in item_str
                    )
                    if date_match or route_match:
                        result = search(item, depth + 1)
                        if result is not None:
                            return result
            # Fallback: søg i alle elementer
            for item in obj:
                result = search(item, depth + 1)
                if result is not None:
                    return result
        return None

    return search(data)


# ─── Hoved-funktion ───────────────────────────────────────────────────────────

async def main() -> int:
    """
    Hovedfunktion. Returnerer exit-kode: 0 = success, 1 = fejl.
    """
    log.info("=" * 60)
    log.info("Anholt Ferry Availability Checker")
    log.info(f"  Rute:       {FROM_STOP} → {TO_STOP}")
    log.info(f"  Dato:       {TARGET_DATE}")
    log.info(f"  Passagerer: {PASSENGERS}")
    log.info(f"  Tidspunkt:  {datetime.utcnow().isoformat()}Z UTC")
    log.info("=" * 60)

    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC er ikke sat — notifikationer deaktiveret")

    state = load_state()

    # Brug cachet API-endpoint fra forrige kørsel, hvis tilgængeligt
    available: Optional[bool] = None
    discovered_api: Optional[str] = state.get("discovered_api")

    if discovered_api:
        log.info(f"Forsøger direkte API-tjek med cachet endpoint: {discovered_api}")
        available = try_api_check(discovered_api)
        if available is None:
            log.info("Direkte API-tjek mislykkedes — falder tilbage til Playwright")

    if available is None:
        log.info("Starter Playwright-session...")
        try:
            available, new_api = await asyncio.wait_for(
                check_with_playwright(),
                timeout=OVERALL_TIMEOUT_SECONDS,
            )
            if new_api and new_api != discovered_api:
                log.info(f"Ny API-URL opdaget og gemt: {new_api}")
                state["discovered_api"] = new_api
        except asyncio.TimeoutError:
            log.error(
                f"Samlet timeout efter {OVERALL_TIMEOUT_SECONDS}s — "
                "tjek mislykkedes"
            )
            state["last_error"] = f"timeout:{datetime.utcnow().isoformat()}Z"
            save_state(state)
            return 1

    # ── Evaluer resultatet ────────────────────────────────────────────────────
    if available is None:
        log.error(
            "Kunne ikke afgøre tilgængelighed — "
            "se screenshots/ mappen for fejlretning"
        )
        state["last_error"] = f"unknown:{datetime.utcnow().isoformat()}Z"
        save_state(state)
        return 1

    log.info(f"Resultat: {'LEDIGT ✓' if available else 'IKKE LEDIGT ✗'}")

    prev_available = state.get("available", False)
    was_notified   = state.get("notified", False)

    if available and not was_notified:
        # Ny tilgængelighed fundet — send notifikation
        log.info("Ny tilgængelighed — sender notifikation!")
        send_ntfy(available=True)
        state["available"] = True
        state["notified"]  = True

    elif not available and prev_available:
        # Tilgængelighed forsvundet — nulstil flag og send besked
        log.info("Tilgængelighed tabt — nulstiller notifikations-flag")
        send_ntfy(available=False)
        state["available"] = False
        state["notified"]  = False

    elif available and was_notified:
        # Stadig ledigt, men vi har allerede notificeret — gør ingenting
        log.info("Stadig ledigt — notifikation allerede sendt, ingen handling")

    else:
        # Stadig ikke ledigt — ingen handling
        log.info("Stadig ikke ledigt — ingen handling")
        state["available"] = False

    state["last_error"] = None
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
