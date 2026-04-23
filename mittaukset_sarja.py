"""
mittaukset.py – Lukemat Solis-invertteriltä ja Carlo Gavazzi EM340 -mittarilta
RS485/USB-sovittimen kautta (Modbus RTU)

Asenna riippuvuudet:
    pip install pymodbus

Kaapelöinti RS485-väylällä:
    Molemmat laitteet samaan RS485-väylään (A+ ja B-).
    Jokaisella laitteella oma Modbus slave-osoite.
    Väylän päähän 120 Ω päätevastus.

Oletusasetukset:
    Solis:  slave-osoite 1,  9600 baud, 8N1
    EM340:  slave-osoite 2, 19200 baud, 8N1  ← tarkista mittarisi asetuksista!
    HUOM:   jos baudiraten täytyy olla sama kaikille laitteille, aseta
            molemmat samaan nopeuteen (esim. 9600) ennen käyttöä.
"""

from pymodbus.client import ModbusSerialClient
import struct

# ── Laiteasetukset – muuta tarvittaessa ──────────────────────────────────────

USB_PORT       = "/dev/ttyUSB0"   # ls /dev/ttyUSB* löytää oikean portin

SOLIS_ADDRESS  = 1                # Solis slave-osoite (tehdasasetus 1)
SOLIS_BAUD     = 9600             # Solis baudrate (tehdasasetus 9600)

EM340_ADDRESS  = 2                # EM340 slave-osoite (aseta mittarista)
EM340_BAUD     = 9600             # EM340 baudrate – aseta samaksi kuin Solis

# ── Modbus-rekisterit ─────────────────────────────────────────────────────────
#
# Solis (function code 0x04, Input Registers):
#   Rekisteri 3004 = Total DC output power, U32, yksikkö W
#   (Hi-word rekisterissä 3004, Lo-word rekisterissä 3005)
#
# Carlo Gavazzi EM340 (function code 0x04, Input Registers):
#   Rekisteri 40096 (0x2760) = Total system power (W), INT32, 0.1 W tarkkuus
#   Positiivinen = tuonti verkosta, negatiivinen = vienti verkkoon
#   (Hi-word rekisterissä 0x2760, Lo-word rekisterissä 0x2761)

SOLIS_POWER_REGISTER = 3004       # Total DC power (U32, W)
EM340_POWER_REGISTER = 0x2760    # Total system power (INT32, 0.1 W)


# ── Apufunktio: yhdistä kaksi 16-bit sanaa 32-bit luvuksi ────────────────────

def registers_to_u32(hi, lo):
    """Yhdistää kaksi rekisteriä etumerkittömäksi 32-bit kokonaisluvuksi."""
    return (hi << 16) | lo

def registers_to_s32(hi, lo):
    """Yhdistää kaksi rekisteriä etumerkilliseksi 32-bit kokonaisluvuksi."""
    raw = (hi << 16) | lo
    # Muunna etumerkilliseksi jos ylin bitti on 1
    return struct.unpack('>i', struct.pack('>I', raw))[0]


# ── Solis – paneelien tuotanto ────────────────────────────────────────────────

def tuotanto() -> float | None:
    """
    Lukee paneelien DC-tehon Solis-invertteriltä.
    Palauttaa tehon watteina tai None jos lukeminen epäonnistuu.
    """
    client = ModbusSerialClient(
        port=USB_PORT,
        baudrate=SOLIS_BAUD,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=3,
    )
    try:
        client.connect()
        result = client.read_input_registers(
            address=SOLIS_POWER_REGISTER,
            count=2,
            slave=SOLIS_ADDRESS,
        )
        if result.isError():
            print(f"[Solis] Modbus-virhe: {result}")
            return None

        teho_w = registers_to_u32(result.registers[0], result.registers[1])
        return float(teho_w)

    except Exception as e:
        print(f"[Solis] Poikkeus: {e}")
        return None
    finally:
        client.close()


# ── Carlo Gavazzi EM340 – talon kokonaiskulutus ───────────────────────────────

def kulutus() -> float | None:
    """
    Lukee talon kokonaistehon EM340-mittarilta.

    EM340 raportoi verkkoon menevän tehon:
      positiivinen = tuodaan verkosta (kulutus > tuotanto)
      negatiivinen = viedään verkkoon (tuotanto > kulutus)

    Tässä palautetaan absoluuttinen kulutus W, ei verkon suuntaa.
    Lasketaan: kulutus = tuotanto + verkkovirta  (ks. paneelien_ylituotto)

    HUOM: Jos EM340 on asennettu mittaamaan VAIN talon kulutusta
    (ei sisällä aurinkopaneeleja), palauta arvo suoraan.
    Jos se mittaa verkkoliittymää (grid tie point), käytä
    alla olevaa laskentaa lvv_server.py:ssä.
    """
    client = ModbusSerialClient(
        port=USB_PORT,
        baudrate=EM340_BAUD,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=3,
    )
    try:
        client.connect()
        result = client.read_input_registers(
            address=EM340_POWER_REGISTER,
            count=2,
            slave=EM340_ADDRESS,
        )
        if result.isError():
            print(f"[EM340] Modbus-virhe: {result}")
            return None

        # INT32, tarkkuus 0.1 W → jaetaan 10:llä saadaan W
        teho_raw = registers_to_s32(result.registers[0], result.registers[1])
        teho_w   = teho_raw / 10.0
        return teho_w

    except Exception as e:
        print(f"[EM340] Poikkeus: {e}")
        return None
    finally:
        client.close()


# ── Laskennat lvv_server.py:tä varten ────────────────────────────────────────

def paneelien_ylituotto(tuotanto_w: float, kulutus_w: float) -> float:
    """
    Laskee paneelien ylituoton.

    Jos EM340 mittaa verkkoliittymää (grid tie point):
        verkkovirta = kulutus()-funktio palauttaa tämän
        talon_kulutus = tuotanto_w - verkkovirta  (negatiivinen = vienti)
        ylituotto = max(0, -verkkovirta)  → vientivirta = ylituotto

    Jos EM340 mittaa vain talon kulutusta suoraan:
        ylituotto = max(0, tuotanto_w - kulutus_w)
    """
    return max(0.0, tuotanto_w - kulutus_w)


# ── Testiajo suoraan ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Luetaan mittauksia...\n")

    t = tuotanto()
    k = kulutus()

    print(f"Solis tuotanto:  {t} W")
    print(f"EM340 kulutus:   {k} W")

    if t is not None and k is not None:
        yli = paneelien_ylituotto(t, k)
        print(f"Paneelien ylituotto: {yli} W")
