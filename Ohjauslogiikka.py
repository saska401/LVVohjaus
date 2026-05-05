import requests
import datetime
import time
import gpiozero
import minimalmodbus

EM_340 = 1         # Modbus-laitteen osoite (0-247) – tarkista laitteestasi
LVV = gpiozero.LED(23)   # GPIO23 ohjaa LVV:tä

CG340 = minimalmodbus.Instrument('/dev/ttyUSB0', EM_340)  # portti ja osoite

CG340.serial.baudrate = 9600           #Luettavan laitteen tarvittavat sarjaporttiasetukset
CG340.serial.bytesize = 8
CG340.serial.parity   = minimalmodbus.serial.PARITY_NONE
CG340.serial.stopbits = 1
CG340.serial.timeout  = 0.5
CG340.mode = minimalmodbus.MODE_RTU

CG340.clear_buffers_before_each_transaction = True
CG340.close_port_after_each_call = True

def kulutus():          # positiiviset luvut kertovat paljonko verkosta ostetaan sähköä ja negatiiviset
    try:                # paljonko myydään verkkoon eli paneelien ylituotto on negatiivinen kulutus
        return CG340.read_register(40, 4, True, minimalmodbus.BYTEORDER_LITTLE_SWAP) #40001 address Carlo gavazzi em340 kulutusmittarilla kertoo systeemin w
    except Exception as e:                                                          
        print(f"Virhe kulutuksen luvussa: {e}")
        return None


#Pörssisähkön hinnan haku API:sta
def hae_nykyinen_sahkonhinta():                                                  
    url = "https://api.porssisahko.net/v2/latest-prices.json"
    
    #Käytetään try, jotta ohjaus ei kaadu, vaikka API:sta pyydettyä dataa ei saapunutkaan.
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


def ohjaa_lvv(current_price, kokonaiskulutus, lvv_paalla):    #OHJAUS LOGIIKKA
    # TURVAEHTO laskettu 25A pääsulakkeilla
    if kokonaiskulutus >= 15:
        return False

    # Kallis sähkö + riittävä ylituotto -> myydään mieluummin verkkoon
    if current_price > 15 and kokonaiskulutus <= -3:
        return False

    # Jos LVV on jo päällä, käytetään matalampaa rajaa päällä pysymiseen
    if lvv_paalla:
        if current_price < 10:
            return True

        if kokonaiskulutus <= 0.5:   
            return True

        return False

    # Jos LVV on pois päältä, päällekytkentään vaaditaan tiukempi ehto (hystereesi)
    else:
        if current_price < 10:
            return True

        if kokonaiskulutus <= -3 and current_price <= 15:
            return True

        return False


# Alkutieto LVV:n tilasta
lvv_paalla = False
LVV.off()

while True:
    

    
    kokonaiskulutus = kulutus()
    current_price = hae_nykyinen_sahkonhinta()
    
    if kokonaiskulutus is None:
        print("Modbus read failed, trying again in 5 seconds...")
        time.sleep(5)
        continue
    
    if current_price is not None:
        uusi_tila = ohjaa_lvv(
            current_price=current_price,
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
        # Tulostetaan tilannekatsaus konsoliin
        print("----------------------------")
        print(f"Pörssisähkön hinta: {current_price} snt/kWh")
        print(f"Kokonaiskulutus: {kokonaiskulutus}")
        print(f"LVV tila: {'PÄÄLLÄ' if lvv_paalla else 'POIS'}")

    else:
        print("Sähkön hintaa ei löytynyt, LVV:n tila jätetään ennalleen.")

    time.sleep(10)  #Ohjelman kierto 10s
