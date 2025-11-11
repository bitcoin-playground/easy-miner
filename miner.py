import struct, random, time, hashlib, logging, os
from threading import Event
from binascii import hexlify, unhexlify
from utils import double_sha256
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env
load_dotenv()

log = logging.getLogger(__name__)

# Costanti per ottimizzazione e logging
BATCH = 1000
RATE_INT = 2

def _midstate(prefix: bytes) -> "hashlib._Hash":
    """
    Restituisce un contesto SHA-256 inizializzato con i primi 76 byte dell'header del blocco: 
    tutti i campi tranne il nonce.
    """
    h = hashlib.sha256()
    h.update(prefix)
    return h

# ---------------------------------------------------------------------------
# mining
# ---------------------------------------------------------------------------
def mine_block(header_hex: str, target_hex: str, nonce_mode: str = "incremental", stop_event: Event | None = None):
    """
    Esegue il proof-of-work cercando un nonce valido per l'header del blocco.
    """
    log.info("Avvio mining - modalità %s", nonce_mode)

    # ---- decodifica header (80 B) ----
    version   = unhexlify(header_hex[0:8])
    prev_hash = unhexlify(header_hex[8:72])
    merkle    = unhexlify(header_hex[72:136])
    ts_bytes  = unhexlify(header_hex[136:144])
    bits      = unhexlify(header_hex[144:152])

    base76 = version + prev_hash + merkle + ts_bytes + bits
    mid    = _midstate(base76)

    header     = bytearray(base76 + b"\x00\x00\x00\x00")
    nonce_view = memoryview(header)[76:]

    target_be = int(target_hex, 16).to_bytes(32, "big")

    if nonce_mode == "incremental":
        nonce = 0
    elif nonce_mode in ("random", "mixed"):
        nonce = random.randint(0, 0xFFFFFFFF)
    else:
        raise ValueError("Modalità di mining non valida.")

    attempts = 0
    start_t  = time.time()
    last_rate_t, last_rate_n = start_t, 0
    last_tsu = start_t

    sha2ctx = hashlib.sha256()          # contesto vuoto da copiare

    while True:
        # interruzione prima di ogni batch
        if stop_event is not None and stop_event.is_set():
            log.info("Mining interrotto: nuovo blocco rilevato")
            return None, None, None

        # ---- aggiornamento timestamp ----
        timestamp_update_interval = int(os.getenv('TIMESTAMP_UPDATE_INTERVAL', 30))
        if timestamp_update_interval and (time.time() - last_tsu) >= timestamp_update_interval:
            ts_bytes = struct.pack("<I", int(time.time()))
            header[68:72] = ts_bytes
            base76  = version + prev_hash + merkle + ts_bytes + bits
            mid     = _midstate(base76)
            header[:76] = base76
            last_tsu = time.time()
            log.debug("Timestamp header aggiornato: %d",
                      int.from_bytes(ts_bytes, "little"))

        # ---- batch di BATCH nonce ----
        for i in range(BATCH):
            # check rapido anche dentro il loop per ridurre la latenza
            if stop_event is not None and stop_event.is_set():
                log.info("Mining interrotto: nuovo blocco rilevato")
                return None, None, None

            n = (nonce + i) & 0xFFFFFFFF
            struct.pack_into("<I", header, 76, n)

            h1 = mid.copy(); h1.update(nonce_view)
            d1 = h1.digest()
            h2 = sha2ctx.copy(); h2.update(d1)
            digest = h2.digest()

            if digest[::-1] < target_be:
                total = time.time() - start_t
                hashrate = (attempts + i + 1) / total if total else 0
                log.info("Blocco trovato - nonce=%d tentativi=%d tempo=%.2fs hashrate medio=%.2f kH/s",
                         n, attempts + i + 1, total, hashrate/1000)
                log.info("Hash valido: %s", digest[::-1].hex())

                return hexlify(bytes(header)).decode(), n, hashrate

        # fine batch ---------------------------------------------------------
        attempts += BATCH
        nonce = (nonce + BATCH) & 0xFFFFFFFF

        # ---- log periodico ----
        now = time.time()

        # hashrate istantaneo: log ogni RATE_INT secondi
        if now - last_rate_t >= RATE_INT:
            hashrate = (attempts - last_rate_n) / (now - last_rate_t)
            last_rate_t, last_rate_n = now, attempts

            struct.pack_into("<I", header, 76, nonce)
            tmp = mid.copy(); tmp.update(nonce_view)
            dbg_hash = double_sha256(tmp.digest())

            log.info("Stato mining - hashrate=%.2f kH/s tentativi=%d nonce=%d hash=%s",
                hashrate/1000,    # hashrate istantaneo
                attempts,     # tentativi
                nonce,        # nonce
                dbg_hash[::-1].hex()  # hash di controllo
            )

