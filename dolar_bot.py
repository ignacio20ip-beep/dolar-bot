import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", CHAT_ID)

COTIZACIONES_BASE_URL = "https://eldoradosa.com/cotizaciones/CotizacionesWeb.htm"

STATE_DIR = Path(".bot_state")
STATE_FILE = STATE_DIR / "last_value.json"
FORCE_SEND = os.getenv("FORCE_SEND", "false").lower() == "true"


def parse_price_to_float(s: str) -> float:
    s = s.strip().replace("$", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def build_url() -> str:
    return f"{COTIZACIONES_BASE_URL}?_={int(time.time() * 1000)}"


def http_get_with_retries(url: str, timeout: int = 20, tries: int = 3) -> requests.Response:
    headers = {"User-Agent": "Mozilla/5.0"}
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep([1, 3, 7][min(i, 2)])
    raise last


def fetch_usd_rates():
    r = http_get_with_retries(build_url())
    soup = BeautifulSoup(r.text, "html.parser")

    for row in soup.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        moneda = " ".join(cols[1].get_text(" ", strip=True).lower().split())
        if ("dolar" in moneda) and ("eeuu" in moneda or "ee.uu" in moneda or "usd" in moneda):
            compra = cols[2].get_text(strip=True)
            venta = cols[3].get_text(strip=True)
            return compra, venta

    raise ValueError("No encontré la fila del dólar (cambió el HTML o el texto).")


def send_telegram(chat_id: str, msg: str):
    api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "disable_web_page_preview": True}

    last = None
    for i in range(3):
        try:
            resp = requests.post(api, json=payload, timeout=20)
            resp.raise_for_status()
            return
        except Exception as e:
            last = e
            time.sleep([1, 3, 7][min(i, 2)])
    raise last


def load_last():
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_last(compra_f: float, venta_f: float):
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    hoy_ar = datetime.now(timezone.utc).astimezone(tz_ar).date().isoformat()

    data = {
        "compra": compra_f,
        "venta": venta_f,
        "last_sent_date_ar": hoy_ar,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def delta_line(today: float, prev: float) -> str:
    diff = today - prev
    pct = (diff / prev) * 100.0 if prev else 0.0
    arrow = "🔺" if diff > 0 else ("🔻" if diff < 0 else "⏺")
    sign = "+" if diff > 0 else ""
    return f"{arrow} {sign}{diff:.2f} ({sign}{pct:.1f}%) vs última vez"


def main():
    try:
        compra_str, venta_str = fetch_usd_rates()
        compra_f = parse_price_to_float(compra_str)
        venta_f = parse_price_to_float(venta_str)

        prev = load_last()

        tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
        ahora = datetime.now(timezone.utc).astimezone(tz_ar).strftime("%d/%m/%Y %H:%M")

        msg = (
            "💵 El Dorado – Dólar EE.UU\n"
            f"Compra: {compra_str.strip()}\n"
            f"Venta:  {venta_str.strip()}\n"
            "\n📈 Cambio (venta):\n"
        )

        if prev and "venta" in prev:
            msg += f"{delta_line(venta_f, float(prev['venta']))}\n"
        else:
            msg += "(sin dato previo)\n"

        msg += f"\nHora bot: {ahora}\nFuente: https://eldoradosa.com/"

                # Evitar doble envío el mismo día (hora Argentina)
        if prev and "last_sent_date_ar" in prev:
            tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
            hoy_ar = datetime.now(timezone.utc).astimezone(tz_ar).date().isoformat()

            if prev["last_sent_date_ar"] == hoy_ar:
                # Ya se mandó hoy → salimos sin enviar nada
                return
        
        send_telegram(CHAT_ID, msg)
        save_last(compra_f, venta_f)

    except Exception as e:
        err = f"❌ Dolar Bot falló:\n{type(e).__name__}: {e}"
        try:
            send_telegram(ADMIN_CHAT_ID, err)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()

