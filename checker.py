#!/usr/bin/env python3
"""
Anholt Ferry Availability Checker
==================================
Læser overvågningslisten fra watches.json og tjekker tilgængelighed
for alle aktive afgange. Sender push-notifikation via ntfy.sh,
når ledige pladser opdages for en overvågning.

Konfiguration via environment variables:
  NTFY_TOPIC     – ntfy-topic (kræves for notifikationer)
  NTFY_SERVER    – ntfy-serveradresse (default: https://ntfy.sh)
  WATCHES_FILE   – sti til watches.json (default: watches.json)
  STATE_FILE     – sti til state-fil (default: availability_state.json)
  PAUSE_SECONDS  – pause i sekunder mellem overvågninger (default: 5)
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
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
NTFY_TOPIC      = os.getenv("NTFY_TOPIC", "")
NTFY_SERVER     = os.getenv("NTFY_SERVER", "https://ntfy.sh")
WATCHES_FILE    = Path(os.getenv("WATCHES_FILE", "watches.json"))
STATE_FILE      = Path(os.getenv("STATE_FILE", "availability_state.json"))
PAUSE_SECONDS   = int(os.getenv("PAUSE_SECONDS", "5"))
SCREENSHOTS_DIR = Path("screenshots")

BOOKING_URL = "https://anholt-ferry.teambooking.dk/timetable?lang=da"

# Maksimal samlet timeout per overvågning (5 minutter)
WATCH_TIMEOUT_SECONDS = 300


# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ─── Watch-dataklasse ─────────────────────────────────────────────────────────

@dataclass
class Watch:
    id: str
    from_stop: str
    to_stop: str
    date: str
    passengers: int
    enabled: bool

    def label(self) -> str:
        """Kort beskrivelse til log og notifikation."""
        return f"{self.from_stop}→{self.to_stop} {self.date} ({self.passengers} pers.)"

    def date_danish(self) -> str:
        """Dato formateret på dansk: '17. maj 2026'."""
        months = [
            "", "januar", "februar", "marts", "april", "maj", "juni",
            "juli", "august", "september", "oktober", "november", "december",
        ]
        d = date_type.fromisoformat(self.date)
        return f"{d.day}. {months[d.month]} {d.year}"


def load_watches() -> list[Watch]:
    """Indlæser watches.json og returnerer aktive overvågninger."""
    if not WATCHES_FILE.exists():
        log.error(f"Kan ikke finde {WATCHES_FILE} — opret filen med dine overvågninger")
        sys.exit(1)
    raw = json.loads(WATCHES_FILE.read_text(encoding="utf-8"))
    watches = [
        Watch(
            id=w["id"],
            from_stop=w["from"],
            to_stop=w["to"],
            date=w["date"],
            passengers=w["passengers"],
            enabled=w.get("enabled", True),
        )
        for w in raw
    ]
    active = [w for w in watches if w.enabled]
    log.info(f"Indlæst {len(watches)} overvågning(er), {len(active)} aktiv(e)")
    return active


# ─── State-håndtering ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """Indlæser gemt tilstand. State er struktureret pr. watch-id."""
    default = {"watches": {}, "discovered_api": None}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            # Gammelt enkelt-watch-format mangler 'watches'-nøglen — nulstil.
            if "watches" not in state:
                log.warning(
                    "State-fil er i gammelt format (enkelt-watch) — "
                    "nulstiller til nyt multi-watch format"
                )
                return default
            log.info(f"Gemt tilstand indlæst fra {STATE_FILE}")
            return state
        except Exception as exc:
            log.warning(f"Kunne ikke læse state-fil, starter frisk: {exc}")
    return default


def save_state(state: dict) -> None:
    """Gemmer tilstand til JSON-filen."""
    state["last_updated"] = datetime.utcnow().isoformat() + "Z"
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info(f"Tilstand gemt → {STATE_FILE}")


def watch_state(state: dict, watch_id: str) -> dict:
    """Henter eller opretter state-entry for en specifik overvågning."""
    if watch_id not in state["watches"]:
        state["watches"][watch_id] = {
            "available": False,
            "notified": False,
            "last_check": None,
            "last_error": None,
        }
    return state["watches"][watch_id]


# ─── Notifikationer ───────────────────────────────────────────────────────────

def send_ntfy(watch: Watch, available: bool) -> None:
    """Sender en push-notifikation via ntfy.sh for én overvågning."""
    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC er ikke sat — notifikation springes over")
        return

    if available:
        title    = f"Ledige billetter: {watch.from_stop} → {watch.to_stop}!"
        message  = (
            f"Der ser ud til at være ledige billetter på Anholtfærgen:\n"
            f"{watch.passengers} personer, {watch.from_stop} til {watch.to_stop}, "
            f"{watch.date_danish()}.\n"
            f"Tjek og book her: {BOOKING_URL}"
        )
        priority = "high"
        tags     = "white_check_mark,ship"
    else:
        title    = f"Anholtfærgen: Pladser væk – {watch.from_stop} → {watch.to_stop}"
        message  = (
            f"{watch.passengers} billetter {watch.from_stop}→{watch.to_stop} "
            f"den {watch.date_danish()} er ikke længere ledige."
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
        log.info(f"[{watch.id}] Notifikation sendt [{priority}]: {title}")
    except httpx.HTTPError as exc:
        log.error(f"[{watch.id}] Kunne ikke sende ntfy-notifikation: {exc}")


# ─── Screenshots ──────────────────────────────────────────────────────────────

async def screenshot(page: Page, watch_id: str, label: str) -> Path:
    """Gemmer et screenshot med watch-id, tidsstempel og label."""
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{ts}_{watch_id}_{label}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        log.info(f"[{watch_id}] Screenshot: {path}")
    except Exception as exc:
        log.warning(f"[{watch_id}] Kunne ikke gemme screenshot: {exc}")
    return path


# ─── Direkte API-tjek ─────────────────────────────────────────────────────────

def try_api_check(api_url: str, watch: Watch) -> Optional[bool]:
    """
    Forsøger at kalde teambooking-API'en direkte med en cachet URL.
    Returnerer True/False/None.
    """
    try:
        log.info(f"[{watch.id}] Direkte API-kald: {api_url}")
        resp = httpx.get(api_url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        return parse_api_for_availability(api_url, data, watch)
    except Exception as exc:
        log.warning(f"[{watch.id}] Direkte API-kald mislykkedes: {exc}")
        return None


# ─── Playwright-session ───────────────────────────────────────────────────────

async def check_watch_with_playwright(watch: Watch) -> tuple[Optional[bool], Optional[str]]:
    """
    Bruger Playwright til at navigere bookingsiden for én overvågning.
    Returnerer (available: bool|None, discovered_api_url: str|None).
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
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="da-DK",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        # Fang JSON-svar fra teambooking
        async def on_response(response: Response) -> None:
            if "teambooking.dk" not in response.url:
                return
            if response.status != 200:
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            try:
                body = await response.json()
                captured_responses.append({"url": response.url, "data": body})
                log.info(f"[{watch.id}] API-svar fanget: {response.url}")
            except Exception:
                pass

        page.on("response", on_response)

        try:
            log.info(f"[{watch.id}] Indlæser: {BOOKING_URL}")
            await page.goto(BOOKING_URL, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(3_000)
            await screenshot(page, watch.id, "01_indlæst")

            log.info(f"[{watch.id}] Navigerer til dato: {watch.date}")
            await navigate_to_date(page, watch)
            await page.wait_for_timeout(2_500)
            await screenshot(page, watch.id, "02_dato_valgt")

            log.info(f"[{watch.id}] Vælger rute: {watch.from_stop} → {watch.to_stop}")
            await select_route_direction(page, watch)
            await page.wait_for_timeout(2_000)
            await screenshot(page, watch.id, "03_rute_valgt")

            discovered_api = _find_best_api_url(captured_responses)

            log.info(f"[{watch.id}] Analyserer tilgængelighed...")
            available = await detect_availability(page, captured_responses, watch)
            return available, discovered_api

        except PlaywrightTimeout as exc:
            log.error(f"[{watch.id}] Timeout: {exc}")
            await screenshot(page, watch.id, "fejl_timeout")
            return None, None
        except Exception as exc:
            log.error(f"[{watch.id}] Uventet fejl: {exc}", exc_info=True)
            await screenshot(page, watch.id, "fejl_uventet")
            return None, None
        finally:
            await browser.close()


def _find_best_api_url(responses: list) -> Optional[str]:
    """Finder bedste API-URL til direkte genbrug i fremtidige kørsler."""
    keywords = ["timetable", "departure", "afgang", "sejlplan", "availability"]
    for item in responses:
        if any(k in item["url"].lower() for k in keywords):
            return item["url"]
    for item in responses:
        if "api.teambooking.dk" in item["url"]:
            return item["url"]
    return None


# ─── Datonavigation ───────────────────────────────────────────────────────────

async def navigate_to_date(page: Page, watch: Watch) -> None:
    """
    Navigerer timetable-siden til den ønskede dato.
    Prøver: URL-parameter → date-input → kalender-knapnavigation.
    """
    target_date  = watch.date
    date_nodash  = target_date.replace("-", "")

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
            d = date_type.fromisoformat(target_date)
            months_da = [
                "", "januar", "februar", "marts", "april", "maj", "juni",
                "juli", "august", "september", "oktober", "november", "december",
            ]
            if str(d.day) in page_text and (
                months_da[d.month] in page_text.lower()
                or f"{d.month:02d}" in page_text
            ):
                log.info(f"[{watch.id}] URL-dato-navigation lykkedes: {url_candidate}")
                return
        except Exception as exc:
            log.debug(f"[{watch.id}] URL-strategi mislykkedes: {exc}")

    log.info(f"[{watch.id}] Prøver UI-datonavigation...")

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
                log.info(f"[{watch.id}] Dato sat via '{selector}'")
                return
        except Exception:
            pass

    await navigate_calendar_buttons(page, watch)


async def navigate_calendar_buttons(page: Page, watch: Watch) -> None:
    """Klikker på 'næste'-knapper for at nå frem til målдатoen (maks 60 klik)."""
    target     = date_type.fromisoformat(watch.date)
    today      = date_type.today()
    days_ahead = (target - today).days

    if days_ahead <= 0:
        return

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
            log.warning(f"[{watch.id}] Kunne ikke finde 'næste'-knap")
            break

    log.info(f"[{watch.id}] Kalendernavigation: {clicks} klik fremad")


# ─── Rutevalg ─────────────────────────────────────────────────────────────────

async def select_route_direction(page: Page, watch: Watch) -> None:
    """Forsøger at vælge rute-retning, hvis siden har en retningsvælger."""
    from_stop = watch.from_stop
    to_stop   = watch.to_stop

    for selector in [
        f"text={from_stop} til {to_stop}",
        f"text={from_stop} - {to_stop}",
        f"text={from_stop} → {to_stop}",
        f'label:has-text("{from_stop}")',
        f'button:has-text("{from_stop}")',
    ]:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2_000):
                await el.click()
                await page.wait_for_timeout(1_000)
                log.info(f"[{watch.id}] Rute valgt via: '{selector}'")
                return
        except Exception:
            pass

    try:
        for sel_el in await page.locator("select").all():
            for opt in await sel_el.locator("option").all():
                text = (await opt.text_content() or "").strip()
                if from_stop in text and (to_stop in text or "Gren" in text):
                    val = await opt.get_attribute("value") or ""
                    await sel_el.select_option(value=val)
                    await page.wait_for_timeout(1_000)
                    log.info(f"[{watch.id}] Rute valgt fra dropdown: '{text}'")
                    return
    except Exception as exc:
        log.debug(f"[{watch.id}] Dropdown-rutevalg mislykkedes: {exc}")

    log.info(f"[{watch.id}] Ingen rutevalg-element fundet — antager standardvisning")


# ─── Tilgængelighedsanalyse ───────────────────────────────────────────────────

async def detect_availability(
    page: Page, captured: list, watch: Watch
) -> Optional[bool]:
    """
    Analyserer siden og fangede API-svar for at afgøre tilgængelighed.
    Returnerer True / False / None.
    """
    # Tjek API-svar først
    for item in captured:
        result = parse_api_for_availability(item["url"], item["data"], watch)
        if result is not None:
            log.info(f"[{watch.id}] Tilgængelighed fra API: {result}")
            return result

    page_text = await page.evaluate("() => document.body.innerText")
    log.info(
        f"[{watch.id}] Sidetekst (første 3000 tegn):\n"
        f"{'─'*60}\n{page_text[:3000]}\n{'─'*60}"
    )

    # Søg efter kapacitetstal: "X ledige"
    for count_str in re.findall(r"(\d+)\s*ledige", page_text, re.IGNORECASE):
        count = int(count_str)
        log.info(f"[{watch.id}] Ledige pladser fundet: {count}")
        if count >= watch.passengers:
            return True
        if count == 0:
            return False

    text_lower = page_text.lower()
    found_unavail = [
        s for s in ["udsolgt", "fuld", "ingen ledige", "lukket for booking", "sold out"]
        if s in text_lower
    ]
    found_avail = [
        s for s in ["ledige pladser", "ledige billetter", "bestil nu", "vælg afgang"]
        if s in text_lower
    ]
    log.info(f"[{watch.id}] Udsolgt-signaler: {found_unavail}")
    log.info(f"[{watch.id}] Ledig-signaler:   {found_avail}")

    departure_result = await scan_departure_elements(page, watch)
    if departure_result is not None:
        return departure_result

    if found_unavail and not found_avail:
        return False
    if found_avail and not found_unavail and watch.from_stop in page_text:
        return True

    log.warning(
        f"[{watch.id}] Kunne ikke afgøre tilgængelighed — se screenshot"
    )
    return None


async def scan_departure_elements(page: Page, watch: Watch) -> Optional[bool]:
    """Scanner afgangsrækker på siden for status-indikatorer."""
    for sel in [
        "[class*='departure']", "[class*='sailing']", "[class*='afgang']",
        "[class*='trip-row']", "[class*='timetable-row']", "tr", "[role='row']",
    ]:
        try:
            elements = await page.locator(sel).all()
            if not elements:
                continue
            for el in elements[:30]:
                text = (await el.text_content() or "").strip()
                if not text:
                    continue
                if watch.from_stop not in text and watch.to_stop not in text:
                    continue
                log.info(f"[{watch.id}] Afgangs-element: {text[:150]!r}")
                text_lower = text.lower()
                if any(p in text_lower for p in ["udsolgt", "fuld", "ingen", "lukket"]):
                    return False
                seat_match = re.search(r"(\d+)\s*ledige", text_lower)
                if seat_match:
                    seats = int(seat_match.group(1))
                    log.info(f"[{watch.id}] Pladser: {seats}")
                    return seats >= watch.passengers
                if any(p in text_lower for p in ["ledig", "vælg", "bestil"]):
                    return True
        except Exception as exc:
            log.debug(f"[{watch.id}] Selector '{sel}' fejlede: {exc}")
    return None


def parse_api_for_availability(url: str, data, watch: Watch) -> Optional[bool]:
    """Forsøger at udlæse tilgængelighed fra et teambooking API-svar."""
    if not isinstance(data, (dict, list)):
        return None

    def search(obj, depth: int = 0) -> Optional[bool]:
        if depth > 6:
            return None
        if isinstance(obj, dict):
            for key, val in obj.items():
                key_l = key.lower()
                if any(k in key_l for k in [
                    "available", "ledige", "capacity", "seats",
                    "passengers", "pax", "remaining", "free",
                ]):
                    if isinstance(val, int):
                        log.info(f"[{watch.id}] API-felt '{key}' = {val}")
                        return val >= watch.passengers
                    if isinstance(val, bool):
                        return val
                if any(k in key_l for k in ["sold_out", "soldout", "full", "udsolgt"]):
                    if isinstance(val, bool):
                        return not val
                result = search(val, depth + 1)
                if result is not None:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    item_str = json.dumps(item, ensure_ascii=False).lower()
                    date_match = (
                        watch.date in item_str
                        or watch.date.replace("-", "") in item_str
                    )
                    route_match = watch.from_stop.lower() in item_str
                    if date_match or route_match:
                        result = search(item, depth + 1)
                        if result is not None:
                            return result
            for item in obj:
                result = search(item, depth + 1)
                if result is not None:
                    return result
        return None

    return search(data)


# ─── Behandling af én overvågning ─────────────────────────────────────────────

async def process_watch(watch: Watch, state: dict) -> None:
    """
    Kører tilgængelighedstjek for én overvågning og opdaterer state/notifikationer.
    """
    log.info(f"── Overvågning: {watch.label()} ──")
    ws = watch_state(state, watch.id)

    # Brug cachet API-endpoint, hvis vi kender det
    available: Optional[bool] = None
    discovered_api = state.get("discovered_api")

    if discovered_api:
        available = try_api_check(discovered_api, watch)
        if available is None:
            log.info(f"[{watch.id}] API-tjek mislykkedes — starter Playwright")

    if available is None:
        try:
            available, new_api = await asyncio.wait_for(
                check_watch_with_playwright(watch),
                timeout=WATCH_TIMEOUT_SECONDS,
            )
            if new_api and new_api != discovered_api:
                log.info(f"[{watch.id}] Ny API-URL opdaget: {new_api}")
                state["discovered_api"] = new_api
        except asyncio.TimeoutError:
            log.error(
                f"[{watch.id}] Timeout efter {WATCH_TIMEOUT_SECONDS}s"
            )
            ws["last_error"] = f"timeout:{datetime.utcnow().isoformat()}Z"
            return

    ws["last_check"] = datetime.utcnow().isoformat() + "Z"

    if available is None:
        log.error(f"[{watch.id}] Kunne ikke afgøre tilgængelighed — se screenshot")
        ws["last_error"] = f"unknown:{datetime.utcnow().isoformat()}Z"
        return

    log.info(f"[{watch.id}] Resultat: {'LEDIGT ✓' if available else 'IKKE LEDIGT ✗'}")

    prev_available = ws.get("available", False)
    was_notified   = ws.get("notified", False)

    if available and not was_notified:
        log.info(f"[{watch.id}] Ny tilgængelighed — sender notifikation!")
        send_ntfy(watch, available=True)
        ws["available"] = True
        ws["notified"]  = True

    elif not available and prev_available:
        log.info(f"[{watch.id}] Tilgængelighed tabt — nulstiller notifikations-flag")
        send_ntfy(watch, available=False)
        ws["available"] = False
        ws["notified"]  = False

    elif available and was_notified:
        log.info(f"[{watch.id}] Stadig ledigt — notifikation allerede sendt")

    else:
        log.info(f"[{watch.id}] Stadig ikke ledigt — ingen handling")
        ws["available"] = False

    ws["last_error"] = None


# ─── Hoved-funktion ───────────────────────────────────────────────────────────

async def main() -> int:
    """Indlæser alle aktive overvågninger og kører dem én ad gangen."""
    log.info("=" * 60)
    log.info("Anholt Ferry Availability Checker")
    log.info(f"Tidspunkt: {datetime.utcnow().isoformat()}Z UTC")
    log.info(f"Watches:   {WATCHES_FILE}")
    log.info("=" * 60)

    if not NTFY_TOPIC:
        log.warning("NTFY_TOPIC er ikke sat — notifikationer deaktiveret")

    watches = load_watches()
    if not watches:
        log.warning("Ingen aktive overvågninger fundet i watches.json")
        return 0

    state = load_state()
    errors = 0

    for i, watch in enumerate(watches):
        await process_watch(watch, state)
        save_state(state)

        # Hensynsfuld pause mellem overvågninger, undtagen efter den sidste
        if i < len(watches) - 1:
            log.info(f"Venter {PAUSE_SECONDS}s før næste overvågning...")
            await asyncio.sleep(PAUSE_SECONDS)

    # Tæl fejl for exit-kode
    for watch in watches:
        ws = state["watches"].get(watch.id, {})
        if ws.get("last_error"):
            errors += 1

    log.info("=" * 60)
    log.info(
        f"Færdig. {len(watches)} overvågning(er) behandlet, {errors} fejl."
    )
    log.info("=" * 60)
    return 1 if errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anholt Ferry Availability Checker")
    parser.add_argument(
        "--loglevel",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log-niveau (default: INFO)",
    )
    args = parser.parse_args()
    logging.getLogger().setLevel(getattr(logging, args.loglevel))
    sys.exit(asyncio.run(main()))
