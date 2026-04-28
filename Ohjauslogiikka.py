import requests
import datetime
import time
import gpiozero
import minimalmodbus

EM_340 = 2         # Modbus-laitteen osoite (0-247) – tarkista laitteestasi
LVV = gpiozero.LED(23)   # GPIO23 ohjaa LVV:tä

CG340 = minimalmodbus.Instrument('/dev/ttyUSB0', EM_340)  # portti ja osoite

CG340.serial.baudrate = 9600                                    #Luettavan laitteen tarvittavat sarjaporttiasetukset
CG340.serial.bytesize = 8
CG340.serial.parity   = minimalmodbus.serial.PARITY_NONE
CG340.serial.stopbits = 1
CG340.serial.timeout  = 0.5
CG340.mode = minimalmodbus.MODE_RTU

CG340.clear_buffers_before_each_transaction = True
CG340.close_port_after_each_call = True



# kokonaiskulutus = total house consumption
#kokonaiskulutus = kulutus

def kulutus():
    try:
        return CG340.read_register(0, 0, 3) * 10
    except Exception as e:
        print(f"Virhe kulutuksen luvussa: {e}")
        return None

def tuotanto():
    try:
        return CG340.read_register(1, 0, 3) * -1  #address, decimals, functioncode (40, 1 , 3, False)
    except Exception as e:
        print(f"Virhe ylituoton luvussa: {e}")
        return None




def hae_nykyinen_sahkonhinta():
    url = "https://api.porssisahko.net/v2/latest-prices.json"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        now = datetime.datetime.now(datetime.timezone.utc)

        for p in data["prices"]:
            start = datetime.datetime.fromisoformat(
                p["startDate"].replace("Z", "+00:00")
            )
            end = datetime.datetime.fromisoformat(
                p["endDate"].replace("Z", "+00:00")
            )

            if start <= now < end:
                return p["price"]

    except Exception as e:
        print(f"Virhe haettaessa sähkön hintaa: {e}")

    return None


def ohjaa_lvv(current_price, paneelien_ylituotto, kokonaiskulutus, lvv_paalla):
    # TURVAEHTO
    if kokonaiskulutus >= 16000:
        return False

    # Kallis sähkö + riittävä ylituotto -> myydään mieluummin verkkoon
    if current_price > 15 and paneelien_ylituotto >= 2000:
        return False

    # Jos LVV on jo päällä, käytetään matalampaa rajaa päällä pysymiseen
    if lvv_paalla:
        if current_price < 10:
            return True

        if paneelien_ylituotto >= 500:
            return True

        return False

    # Jos LVV on pois päältä, päällekytkentään vaaditaan tiukempi ehto
    else:
        if current_price < 10:
            return True

        if paneelien_ylituotto >= 3000 and current_price <= 15:
            return True

        return False


# Alkutieto LVV:n tilasta
lvv_paalla = False
LVV.off()

while True:
    

    #kokonaiskulutus = kulutus()
    kokonaiskulutus = kulutus()
    paneelien_ylituotto = tuotanto()
    current_price = hae_nykyinen_sahkonhinta()
    
    if kokonaiskulutus is None or paneelien_ylituotto is None:
        print("Modbus read failed, trying again in 5 seconds...")
        time.sleep(5)
        continue
    
    if current_price is not None:
        uusi_tila = ohjaa_lvv(
            current_price=current_price,
            paneelien_ylituotto=paneelien_ylituotto,
            kokonaiskulutus=kokonaiskulutus,
            lvv_paalla=lvv_paalla
        )

        # Ohjataan GPIO vain jos tila muuttuu
        if uusi_tila != lvv_paalla:
            if uusi_tila:
                LVV.on()
                print("LVV kytketty päälle")
            else:
                LVV.off()
                print("LVV kytketty pois päältä")

        lvv_paalla = uusi_tila

        print("----------------------------")
        print(f"Pörssisähkön hinta: {current_price} snt/kWh")
        print(f"Paneelien ylituotto: {paneelien_ylituotto} W")
        print(f"Kokonaiskulutus: {kokonaiskulutus}")
        print(f"LVV tila: {'PÄÄLLÄ' if lvv_paalla else 'POIS'}")

    else:
        print("Sähkön hintaa ei löytynyt, LVV:n tila jätetään ennalleen.")

    time.sleep(10)
