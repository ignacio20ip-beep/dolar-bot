import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# ===== Env =====
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
TEST_CHAT_ID = os.getenv("TEST_CHAT_ID", "").strip() or None
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip() or CHAT_ID

FORCE_SEND = os.getenv("FORCE_SEND", "false").lower() == "true"

# ===== URLs / State =====
COTIZACIONES_BASE_URL = "https://eldoradosa.com/cotizaciones/CotizacionesWebXX.htm"

STATE_DIR = Path(".bot_state")
STATE_FILE = STATE_DIR / "last_value.json"
HISTORY_FILE = STATE_DIR / "history.csv"


def notify_debug(msg: str) -> None:
    """
    Envía avisos al chat de prueba si existe, sino al admin.
    Nunca al chat de producción.
    """
    dest = TEST_CHAT_ID or ADMIN_CHAT_ID
    if not dest:
        return
    try:
        send_telegram(dest, msg)
    except Exception:
        pass


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


def save_last(compra_f: float, venta_f: float, hoy_ar: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "compra": compra_f,
        "venta": venta_f,
        "last_sent_date_ar": hoy_ar,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_weekend_ar() -> bool:
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    dow = datetime.now(timezone.utc).astimezone(tz_ar).weekday()  # 0=Lun ... 6=Dom
    return dow >= 5


def today_ar_iso() -> str:
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    return datetime.now(timezone.utc).astimezone(tz_ar).date().isoformat()


def append_history_once_per_day(date_ar: str, compra_f: float, venta_f: float) -> bool:
    """
    Guarda una fila por día (si ya existe ese date_ar, no duplica).
    Devuelve True si escribió, False si ya existía.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("date_ar,compra,venta\n", encoding="utf-8")

    lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()

    for line in lines[1:]:
        if line.startswith(date_ar + ","):
            return False

    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{date_ar},{compra_f:.2f},{venta_f:.2f}\n")
    return True


def read_history_rows():
    if not HISTORY_FILE.exists():
        return []
    rows = []
    for i, line in enumerate(HISTORY_FILE.read_text(encoding="utf-8").splitlines()):
        if i == 0 or not line.strip():
            continue
        date_ar, compra, venta = line.split(",")
        rows.append({"date_ar": date_ar, "compra": float(compra), "venta": float(venta)})
    return rows


def day_name_es(date_iso: str) -> str:
    d = datetime.fromisoformat(date_iso).date()
    names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    return names[d.weekday()]


def month_name_es(month: int) -> str:
    names = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ]
    return names[month - 1]


def arrow_for(diff: float) -> str:
    return "🔺" if diff > 0 else ("🔻" if diff < 0 else "⏺")


def delta_line(today: float, prev: float) -> str:
    diff = today - prev
    pct = (diff / prev) * 100.0 if prev else 0.0
    sign = "+" if diff > 0 else ""
    return f"{arrow_for(diff)} {sign}{diff:.2f} ({sign}{pct:.1f}%) vs última vez"


def weekly_summary_message(today_venta: float) -> str | None:
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    now_ar = datetime.now(timezone.utc).astimezone(tz_ar)

    if now_ar.weekday() != 4:  # Viernes
        return None

    rows = read_history_rows()
    if len(rows) < 2:
        return None

    last = rows[-5:] if len(rows) >= 5 else rows[:]
    ventas = [r["venta"] for r in last]
    dates = [r["date_ar"] for r in last]

    first = ventas[0]
    last_v = ventas[-1]
    diff = last_v - first
    pct = (diff / first) * 100 if first else 0.0
    sign = "+" if diff > 0 else ""
    arr = arrow_for(diff)

    min_idx = ventas.index(min(ventas))
    max_idx = ventas.index(max(ventas))
    vmin, dmin = ventas[min_idx], dates[min_idx]
    vmax, dmax = ventas[max_idx], dates[max_idx]
    prom = sum(ventas) / len(ventas)
    rango = vmax - vmin

    return (
        "📊 Semana (Venta)\n"
        f"Hoy: {today_venta:.2f}\n"
        f"Δ semanal: {arr} {sign}{diff:.2f} ({sign}{pct:.1f}%)\n\n"
        f"Min: {vmin:.2f} ({day_name_es(dmin)})\n"
        f"Max: {vmax:.2f} ({day_name_es(dmax)})\n"
        f"Prom: {prom:.2f}\n"
        f"Rango: {rango:.2f}"
    )


def monthly_summary_message() -> str | None:
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    now_ar = datetime.now(timezone.utc).astimezone(tz_ar)

    if now_ar.day != 1:
        return None

    year = now_ar.year
    month = now_ar.month - 1
    if month == 0:
        month = 12
        year -= 1

    rows = read_history_rows()
    if not rows:
        return None

    prefix = f"{year:04d}-{month:02d}-"
    month_rows = [r for r in rows if r["date_ar"].startswith(prefix)]
    if len(month_rows) < 2:
        return None

    ventas = [r["venta"] for r in month_rows]
    dates = [r["date_ar"] for r in month_rows]

    first = ventas[0]
    last_v = ventas[-1]
    diff = last_v - first
    pct = (diff / first) * 100 if first else 0.0
    sign = "+" if diff > 0 else ""
    arr = arrow_for(diff)

    min_idx = ventas.index(min(ventas))
    max_idx = ventas.index(max(ventas))
    vmin, dmin = ventas[min_idx], dates[min_idx]
    vmax, dmax = ventas[max_idx], dates[max_idx]
    prom = sum(ventas) / len(ventas)
    rango = vmax - vmin

    up = down = flat = 0
    for i in range(1, len(ventas)):
        if ventas[i] > ventas[i - 1]:
            up += 1
        elif ventas[i] < ventas[i - 1]:
            down += 1
        else:
            flat += 1

    return (
        f"🗓️ {month_name_es(month)} {year} (Venta)\n"
        f"Cierre: {last_v:.2f}\n"
        f"Δ mensual: {arr} {sign}{diff:.2f} ({sign}{pct:.1f}%)\n\n"
        f"Min: {vmin:.2f} ({dmin[8:10]}/{dmin[5:7]})\n"
        f"Max: {vmax:.2f} ({dmax[8:10]}/{dmax[5:7]})\n"
        f"Prom: {prom:.2f}\n"
        f"Días: 🔺{up} / 🔻{down} / ⏺{flat}\n"
        f"Rango: {rango:.2f}"
    )


def main():
    try:
        is_production_run = not FORCE_SEND
        tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
        ahora = datetime.now(timezone.utc).astimezone(tz_ar).strftime("%d/%m/%Y %H:%M")
        hoy_ar = datetime.now(timezone.utc).astimezone(tz_ar).date().isoformat()

        # Logs mínimos para debug
        print(f"FORCE_SEND={FORCE_SEND} is_production_run={is_production_run}")
        print(f"CHAT_ID={CHAT_ID} TEST_CHAT_ID={TEST_CHAT_ID} ADMIN_CHAT_ID={ADMIN_CHAT_ID}")

        if FORCE_SEND and not TEST_CHAT_ID:
            notify_debug("❌ FORCE_SEND=true pero TEST_CHAT_ID no está seteado. No puedo enviar test.")
            raise ValueError("FORCE_SEND=true pero TEST_CHAT_ID no está seteado.")

        compra_str, venta_str = fetch_usd_rates()
        compra_f = parse_price_to_float(compra_str)
        venta_f = parse_price_to_float(venta_str)

        prev = load_last()

        title = "🧪 [TEST] El Dorado – Dólar EE.UU" if FORCE_SEND else "💵 El Dorado – Dólar EE.UU"
        msg = (
            f"{title}\n"
            f"Compra: {compra_str}\n"
            f"Venta:  {venta_str}\n"
            "\n📈 Cambio (venta):\n"
        )

        if prev and "venta" in prev:
            msg += f"{delta_line(venta_f, float(prev['venta']))}\n"
        else:
            msg += "(sin dato previo)\n"

        msg += f"\nHora bot: {ahora}\nFuente: https://eldoradosa.com/"

        # Anti-spam diario solo en producción
        if is_production_run and prev and "last_sent_date_ar" in prev:
            if prev["last_sent_date_ar"] == hoy_ar:
                reason = f"🧯 Producción NO enviada: ya se envió hoy (AR={hoy_ar}). Anti-spam activado."
                print(reason)
                notify_debug(reason)
                return

        dest_chat_id = CHAT_ID if is_production_run else TEST_CHAT_ID
        print(f"Sending to chat_id={dest_chat_id}")

        send_telegram(dest_chat_id, msg)

        # Guardar último valor siempre (sirve para delta en test también)
        save_last(compra_f, venta_f, hoy_ar)

        # Histórico + resúmenes solo producción y no finde
        if is_production_run and (not is_weekend_ar()):
            append_history_once_per_day(hoy_ar, compra_f, venta_f)

        if is_production_run:
            w = weekly_summary_message(today_venta=venta_f)
            if w:
                send_telegram(CHAT_ID, w)

            m = monthly_summary_message()
            if m:
                send_telegram(CHAT_ID, m)

    except Exception as e:
        err = f"❌ Dolar Bot falló:\n{type(e).__name__}: {e}"
        try:
            send_telegram(ADMIN_CHAT_ID, err)
        except Exception:
            pass
        notify_debug(err)
        raise


if __name__ == "__main__":
    main()

