import requests
import datetime
import time
#import gpiozero

#LVV = gpiozero.LED(23)   # GPIO23 ohjaa LVV:tä
# What flows to/from the grid (positive = exporting, negative = importing)
#verkko = tuotanto - kulutus

# Paneelien_ylituotto = how much surplus solar you have available
# Only positive when panels produce more than the house needs
#paneelien_ylituotto = max(0, tuotanto - kulutus)

# kokonaiskulutus = total house consumption
#kokonaiskulutus = kulutus

#def kulutus():
    #kirjoita tähän koodi, joka hakee talon kokonaiskulutuksen (W)

#def tuotanto():
    #kirjoita tähän koodi, joka hakee paneelien tuotannon (W)

#def paneelien_ylituotto(tuotanto, kulutus):
    #return max(0, tuotanto - kulutus)


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
#LVV.off()

while True:
    # TESTIMUUTTUJAT
    paneelien_ylituotto = 400   # W
    kokonaiskulutus = 14000          # tarkista myöhemmin oikea yksikkö mittauksesta

    #kokonaiskulutus = kulutus()
    current_price = hae_nykyinen_sahkonhinta()

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
                #LVV.on()
                print("LVV kytketty päälle")
            else:
                #LVV.off()
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