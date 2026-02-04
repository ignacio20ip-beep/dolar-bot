import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ========= CONFIG =========
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Pegá acá la Request URL exacta que viste en Network (la que devuelve el HTML que pegaste)
# Ejemplo: "https://eldoradosa.com/CotizacionesWeb.htm?_=1770159206"
COTIZACIONES_URL = "https://eldoradosa.com/cotizaciones/CotizacionesWeb.htm?_=1770162038946"
# ==========================


def norm(s: str) -> str:
    """Normaliza texto: minúsculas, sin espacios repetidos."""
    return " ".join(s.strip().lower().split())


def fetch_usd_rates():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(COTIZACIONES_URL, headers=headers, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    for row in soup.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        moneda = norm(cols[1].get_text(" ", strip=True))  # 2da columna es el nombre de la moneda
        # En tu HTML es "Dolar EEUU". Lo hacemos tolerante por si cambia a "Dólar EE.UU", etc.
        if ("dolar" in moneda) and ("eeuu" in moneda or "ee.uu" in moneda or "usd" in moneda):
            compra = cols[2].get_text(strip=True)
            venta = cols[3].get_text(strip=True)
            return compra, venta

    # Si no encontró, mostramos monedas disponibles (debug útil)
    monedas = []
    for row in soup.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) >= 2:
            monedas.append(norm(cols[1].get_text(" ", strip=True)))
    raise ValueError(f"No encontré la fila del dólar. Monedas vistas: {monedas}")


def send_telegram(msg: str):
    api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "disable_web_page_preview": True}
    resp = requests.post(api, json=payload, timeout=20)
    resp.raise_for_status()


def main():
    compra, venta = fetch_usd_rates()
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    ahora = datetime.now(timezone.utc).astimezone(tz_ar).strftime("%d/%m/%Y %H:%M")


    mensaje = (
        "💵 El Dorado – Dólar EE.UU\n"
        f"Compra: {compra}\n"
        f"Venta:  {venta}\n"
        f"Hora bot: {ahora}\n"
        "Fuente: https://eldoradosa.com/"
    )
    send_telegram(mensaje)


if __name__ == "__main__":
    main()



