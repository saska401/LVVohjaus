import requests
import datetime
import time
import threading
from flask import Flask, jsonify, request, send_from_directory
import gpiozero
import minimalmodbus

# ── Hardware setup ────────────────────────────────────────────────────────────
EM_340 = 2
LVV = gpiozero.LED(23)

CG340 = minimalmodbus.Instrument('/dev/ttyUSB0', EM_340)
CG340.serial.baudrate = 9600
CG340.serial.bytesize = 8
CG340.serial.parity   = minimalmodbus.serial.PARITY_NONE
CG340.serial.stopbits = 1
CG340.serial.timeout  = 0.5
CG340.mode = minimalmodbus.MODE_RTU
CG340.clear_buffers_before_each_transaction = True
CG340.close_port_after_each_call = True

app = Flask(__name__, static_folder=".")

# ── Shared state (thread-safe via lock) ──────────────────────────────────────
lock = threading.Lock()

state = {
    "paneelien_ylituotto": None,   # W – luetaan Modbusista
    "kokonaiskulutus":     None,   # W – luetaan Modbusista
    "current_price":       None,   # snt/kWh, haetaan automaattisesti
    "lvv_paalla":          False,
    "last_price_update":   None,
    "log":                 [],     # viimeiset 50 tapahtumalokiriviä
}


def add_log(msg: str, level: str = "info"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    entry = {"ts": ts, "msg": msg, "level": level}
    with lock:
        state["log"].insert(0, entry)
        if len(state["log"]) > 50:
            state["log"].pop()
    print(f"[{ts}] {msg}")


# ── Modbus readers ────────────────────────────────────────────────────────────
def kulutus():
    try:
        return CG340.read_register(0, 0, 4) * 10
    except Exception as e:
        add_log(f"Virhe kulutuksen luvussa: {e}", "error")
        return None


def tuotanto():
    try:
        return CG340.read_register(1, 0, 4) * -1
    except Exception as e:
        add_log(f"Virhe ylituoton luvussa: {e}", "error")
        return None


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
def ohjaa_lvv(current_price, paneelien_ylituotto, kokonaiskulutus, lvv_paalla):
    if kokonaiskulutus >= 16000:
        return False
    if current_price > 15 and paneelien_ylituotto >= 2000:
        return False
    if lvv_paalla:
        if current_price < 10:
            return True
        if paneelien_ylituotto >= 500:
            return True
        return False
    else:
        if current_price < 10:
            return True
        if paneelien_ylituotto >= 3000 and current_price <= 15:
            return True
        return False


# ── GPIO helper ───────────────────────────────────────────────────────────────
def set_gpio(on: bool):
    if on:
        LVV.on()
    else:
        LVV.off()


# ── Background control loop ───────────────────────────────────────────────────
def control_loop():
    add_log("Ohjaussilmukka käynnistetty", "ok")
    price_interval = 60
    last_price_fetch = 0
                                                                    
    while True:
        now_ts = time.time()

        # Fetch price periodically
        if now_ts - last_price_fetch >= price_interval:
            price = hae_nykyinen_sahkonhinta()
            if price is not None:
                with lock:
                    old = state["current_price"]
                    state["current_price"] = price
                    state["last_price_update"] = datetime.datetime.now().strftime("%H:%M:%S")
                if old != price:
                    add_log(f"Sähkön hinta: {price:.2f} snt/kWh", "ok")
            last_price_fetch = now_ts

        # Read Modbus
        uusi_kulutus   = kulutus()
        uusi_ylituotto = tuotanto()

        if uusi_kulutus is None or uusi_ylituotto is None:
            add_log("Modbus-luku epäonnistui, ohitetaan kierros", "error")
            time.sleep(10)
            continue

        with lock:
            state["kokonaiskulutus"]     = uusi_kulutus
            state["paneelien_ylituotto"] = uusi_ylituotto

        # Run control logic
        with lock:
            price     = state["current_price"]
            ylituotto = state["paneelien_ylituotto"]
            kulutus_  = state["kokonaiskulutus"]
            lvv_now   = state["lvv_paalla"]

        if price is not None:
            uusi_tila = ohjaa_lvv(price, ylituotto, kulutus_, lvv_now)
            if uusi_tila != lvv_now:
                set_gpio(uusi_tila)
                with lock:
                    state["lvv_paalla"] = uusi_tila
                add_log(
                    "LVV kytketty PÄÄLLE" if uusi_tila else "LVV kytketty POIS",
                    "ok" if uusi_tila else "warn"
                )
        else:
            add_log("Sähkön hintaa ei löytynyt, LVV:n tila jätetään ennalleen.", "warn")

        time.sleep(10)


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
            "current_price":       state["current_price"],
            "lvv_paalla":          state["lvv_paalla"],
            "last_price_update":   state["last_price_update"],
            "log":                 state["log"][:20],
        })


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    LVV.off()
    t = threading.Thread(target=control_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
