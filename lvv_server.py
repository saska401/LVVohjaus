import requests
import datetime
import time
import threading
from flask import Flask, jsonify, request, send_from_directory
import os

# Uncomment on Raspberry Pi:
# import gpiozero
# LVV = gpiozero.LED(23)

app = Flask(__name__, static_folder=".")

# ── Shared state (thread-safe via lock) ──────────────────────────────────────
lock = threading.Lock()

state = {
    "paneelien_ylituotto": 1000,   # W  – muutettavissa HMI:stä
    "kokonaiskulutus":     17000,  # W  – muutettavissa HMI:stä
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


# ── Control logic (identical to original) ────────────────────────────────────
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
    pass          # Uncomment below on Raspberry Pi:
    # if on:
    #     LVV.on()
    # else:
    #     LVV.off()


# ── Background control loop ───────────────────────────────────────────────────
def control_loop():
    add_log("Ohjaussilmukka käynnistetty", "ok")
    price_interval = 60   # seconds between price fetches
    last_price_fetch = 0

    while True:
        now = time.time()

        # Fetch price periodically
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

        # Run control logic
        with lock:
            price     = state["current_price"]
            ylituotto = state["paneelien_ylituotto"]
            kulutus   = state["kokonaiskulutus"]
            lvv_now   = state["lvv_paalla"]

        if price is not None:
            uusi_tila = ohjaa_lvv(price, ylituotto, kulutus, lvv_now)
            if uusi_tila != lvv_now:
                set_gpio(uusi_tila)
                with lock:
                    state["lvv_paalla"] = uusi_tila
                add_log(
                    "LVV kytketty PÄÄLLE" if uusi_tila else "LVV kytketty POIS",
                    "ok" if uusi_tila else "warn"
                )

        time.sleep(10)


# ── REST API ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the HMI page."""
    return send_from_directory(".", "lvv_hmi.html")


@app.route("/api/state")
def api_state():
    """Return full system state as JSON."""
    with lock:
        return jsonify({
            "paneelien_ylituotto": state["paneelien_ylituotto"],
            "kokonaiskulutus":     state["kokonaiskulutus"],
            "current_price":       state["current_price"],
            "lvv_paalla":          state["lvv_paalla"],
            "last_price_update":   state["last_price_update"],
            "log":                 state["log"][:20],
        })


@app.route("/api/set", methods=["POST"])
def api_set():
    """Update writable variables from the HMI."""
    data = request.get_json(force=True)
    changed = []

    with lock:
        if "paneelien_ylituotto" in data:
            val = int(data["paneelien_ylituotto"])
            if 0 <= val <= 10000:
                state["paneelien_ylituotto"] = val
                changed.append(f"ylituotto={val} W")

        if "kokonaiskulutus" in data:
            val = int(data["kokonaiskulutus"])
            if 0 <= val <= 25000:
                state["kokonaiskulutus"] = val
                changed.append(f"kulutus={val} W")

    if changed:
        add_log("HMI muutti: " + ", ".join(changed), "info")
        return jsonify({"ok": True, "changed": changed})
    return jsonify({"ok": False, "error": "No valid fields"}), 400


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t = threading.Thread(target=control_loop, daemon=True)
    t.start()
    # Listen on all interfaces so other devices on the LAN can connect
    app.run(host="0.0.0.0", port=5000, debug=False)
