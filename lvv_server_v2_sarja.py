import datetime
import time
import threading
import requests
from flask import Flask, jsonify, request, send_from_directory
from mittaukset import tuotanto, kulutus, paneelien_ylituotto

# Uncomment on Raspberry Pi:
# import gpiozero
# LVV = gpiozero.LED(23)

app = Flask(__name__, static_folder=".")

# ── Shared state ──────────────────────────────────────────────────────────────
lock = threading.Lock()

state = {
    "paneelien_ylituotto": 0,
    "kokonaiskulutus":     0,
    "tuotanto":            0,
    "current_price":       None,
    "lvv_paalla":          False,
    "last_price_update":   None,
    "last_measurement":    None,
    "log":                 [],
}


def add_log(msg: str, level: str = "info"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    entry = {"ts": ts, "msg": msg, "level": level}
    with lock:
        state["log"].insert(0, entry)
        if len(state["log"]) > 50:
            state["log"].pop()
    print(f"[{ts}] {msg}")


# ── Price fetcher ─────────────────────────────────────────────────────────────
def hae_nykyinen_sahkonhinta():
    url = "https://api.porssisahko.net/v2/latest-prices.json"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        now = datetime.datetime.now(datetime.timezone.utc)
        for p in data["prices"]:
            start = datetime.datetime.fromisoformat(p["startDate"].replace("Z", "+00:00"))
            end   = datetime.datetime.fromisoformat(p["endDate"].replace("Z", "+00:00"))
            if start <= now < end:
                return p["price"]
    except Exception as e:
        add_log(f"Virhe hinnan haussa: {e}", "error")
    return None


# ── Control logic ─────────────────────────────────────────────────────────────
def ohjaa_lvv(current_price, paneelien_ylituotto_w, kokonaiskulutus_w, lvv_paalla):
    if kokonaiskulutus_w >= 16000:
        return False
    if current_price > 15 and paneelien_ylituotto_w >= 2000:
        return False
    if lvv_paalla:
        if current_price < 10:
            return True
        if paneelien_ylituotto_w >= 500:
            return True
        return False
    else:
        if current_price < 10:
            return True
        if paneelien_ylituotto_w >= 3000 and current_price <= 15:
            return True
        return False


def set_gpio(on: bool):
    pass  # Uncomment on Raspberry Pi:
    # if on: LVV.on()
    # else:  LVV.off()


# ── Background loop ───────────────────────────────────────────────────────────
def control_loop():
    add_log("Ohjaussilmukka käynnistetty", "ok")
    price_interval       = 60   # hintapäivitys sekunteina
    measurement_interval = 10   # mittausväli sekunteina
    last_price_fetch     = 0
    last_measurement     = 0

    while True:
        now = time.time()

        # Hae sähkön hinta minuutin välein
        if now - last_price_fetch >= price_interval:
            price = hae_nykyinen_sahkonhinta()
            if price is not None:
                with lock:
                    old = state["current_price"]
                    state["current_price"] = price
                    state["last_price_update"] = datetime.datetime.now().strftime("%H:%M:%S")
                if old != price:
                    add_log(f"Sähkön hinta: {price:.2f} snt/kWh", "ok")
            last_price_fetch = now

        # Lue mittaukset 10 sekunnin välein
        if now - last_measurement >= measurement_interval:
            t = tuotanto()   # W Solis-invertteriltä
            k = kulutus()    # W Carlo Gavazzi EM340:ltä

            if t is not None and k is not None:
                yli = paneelien_ylituotto(t, k)
                with lock:
                    state["tuotanto"]            = round(t)
                    state["kokonaiskulutus"]      = round(k)
                    state["paneelien_ylituotto"]  = round(yli)
                    state["last_measurement"]     = datetime.datetime.now().strftime("%H:%M:%S")
            else:
                add_log("Mittausvirhe – tila jätetään ennalleen", "warn")

            last_measurement = now

        # Aja ohjauslogiikka joka kierros
        with lock:
            price  = state["current_price"]
            yli    = state["paneelien_ylituotto"]
            kul    = state["kokonaiskulutus"]
            lvv_nyt = state["lvv_paalla"]

        if price is not None:
            uusi_tila = ohjaa_lvv(price, yli, kul, lvv_nyt)
            if uusi_tila != lvv_nyt:
                set_gpio(uusi_tila)
                with lock:
                    state["lvv_paalla"] = uusi_tila
                add_log(
                    "LVV kytketty PÄÄLLE" if uusi_tila else "LVV kytketty POIS",
                    "ok" if uusi_tila else "warn"
                )

        time.sleep(5)


# ── REST API ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "lvv_hmi.html")


@app.route("/api/state")
def api_state():
    with lock:
        return jsonify({
            "paneelien_ylituotto": state["paneelien_ylituotto"],
            "kokonaiskulutus":     state["kokonaiskulutus"],
            "tuotanto":            state["tuotanto"],
            "current_price":       state["current_price"],
            "lvv_paalla":          state["lvv_paalla"],
            "last_price_update":   state["last_price_update"],
            "last_measurement":    state["last_measurement"],
            "log":                 state["log"][:20],
        })


# Mittausarvot tulevat nyt automaattisesti laitteista,
# joten /api/set ei enää tarvita manuaaliseen syöttöön.
# Pidetään se silti kynnysarvojen säätämistä varten tulevaisuudessa.

@app.route("/api/set", methods=["POST"])
def api_set():
    return jsonify({"ok": False, "error": "Mittausarvot tulevat laitteista automaattisesti"}), 400


# ── Käynnistys ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t = threading.Thread(target=control_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
