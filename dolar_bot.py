import os
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ========= CONFIG =========
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
# Optional: where to send error alerts. Defaults to CHAT_ID.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", CHAT_ID)

# Base URL(s). We'll add a cache-buster query param dynamically.
COTIZACIONES_BASE_URLS = [
    "https://eldoradosa.com/cotizaciones/CotizacionesWeb.htm",
    "https://eldoradosa.com/CotizacionesWeb.htm",  # fallback (just in case)
]
# ==========================


def log(msg: str) -> None:
    # GitHub Actions-friendly logs (single line)
    print(f"[dolar-bot] {msg}", flush=True)


def norm(s: str) -> str:
    """Normalize text: lowercase, trim, collapse whitespace."""
    return " ".join(s.strip().lower().split())


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    json=None,
    headers=None,
    timeout: int = 20,
    retries: int = 3,
    backoff: float = 1.8,
):
    """Simple retry wrapper with exponential backoff."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.request(method, url, json=json, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                sleep_s = (backoff ** (attempt - 1))
                log(f"{method} {url} failed (attempt {attempt}/{retries}): {exc}. Retrying in {sleep_s:.1f}s")
                time.sleep(sleep_s)
            else:
                log(f"{method} {url} failed (attempt {attempt}/{retries}): {exc}. No more retries.")
    raise last_exc  # type: ignore[misc]


def build_url(base: str) -> str:
    # cache-buster: milliseconds
    ts = int(time.time() * 1000)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}_={ts}"


def fetch_usd_rates(session: requests.Session):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        "Connection": "close",
    }

    last_error = None
    for base in COTIZACIONES_BASE_URLS:
        url = build_url(base)
        try:
            log(f"Fetching rates from {base}")
            r = request_with_retries(session, "GET", url, headers=headers, timeout=20, retries=3)
            soup = BeautifulSoup(r.text, "html.parser")

            for row in soup.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                moneda = norm(cols[1].get_text(" ", strip=True))  # 2nd column = currency name
                if ("dolar" in moneda) and ("eeuu" in moneda or "ee.uu" in moneda or "usd" in moneda):
                    compra = cols[2].get_text(strip=True)
                    venta = cols[3].get_text(strip=True)

                    # Basic sanity check (avoid sending garbage)
                    if not compra or not venta:
                        raise ValueError("Compra/venta vacías en la fila del dólar.")
                    log(f"Parsed USD rates OK: compra={compra} venta={venta}")
                    return compra, venta

            # If not found, collect currencies for debug
            monedas = []
            for row in soup.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) >= 2:
                    monedas.append(norm(cols[1].get_text(' ', strip=True)))

            raise ValueError(f"No encontré la fila del dólar. Monedas vistas: {monedas}")

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log(f"Failed with base {base}: {exc}")

    raise RuntimeError(f"No pude obtener cotización desde ningún endpoint. Último error: {last_error}")


def send_telegram(session: requests.Session, chat_id: str, msg: str):
    api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "disable_web_page_preview": True}
    request_with_retries(session, "POST", api, json=payload, timeout=20, retries=3)


def main():
    session = requests.Session()
    try:
        compra, venta = fetch_usd_rates(session)

        tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
        ahora = datetime.now(timezone.utc).astimezone(tz_ar).strftime("%d/%m/%Y %H:%M")

        mensaje = (
            "💵 El Dorado – Dólar EE.UU\n"
            f"Compra: {compra}\n"
            f"Venta:  {venta}\n"
            f"Hora bot: {ahora}\n"
            "Fuente: https://eldoradosa.com/"
        )

        send_telegram(session, CHAT_ID, mensaje)
        log("Message sent successfully.")

    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: {exc}")
        tb = traceback.format_exc()

        # Try to alert admin (best-effort)
        try:
            alert = (
                "🚨 dolar-bot falló\n"
                f"Error: {exc}\n"
                "\n"
                "Traceback (resumido):\n"
                + "\n".join(tb.splitlines()[-20:])
            )
            send_telegram(session, ADMIN_CHAT_ID, alert)
            log("Admin alert sent.")
        except Exception as exc2:  # noqa: BLE001
            log(f"Couldn't send admin alert: {exc2}")

        # Fail the workflow
        raise


if __name__ == "__main__":
    main()
    
