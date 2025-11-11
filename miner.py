import struct, random, time, hashlib, logging, os
from threading import Event
from binascii import hexlify, unhexlify
from dotenv import load_dotenv
import numpy as np

# INSTALLARE: pip install numba
try:
    from numba import njit, prange
    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False
    logging.warning("Numba non disponibile - usando versione non ottimizzata")

load_dotenv()

log = logging.getLogger(__name__)

# Costanti
# Permette di configurare la dimensione del batch via .env (es. BATCH=50000)
BATCH = int(os.getenv("BATCH", "10000"))
RATE_INT = 2

if NUMBA_AVAILABLE:
    @njit(cache=True, fastmath=True)
    def _sha256_round(data: np.ndarray) -> np.ndarray:
        """
        Implementazione semplificata SHA-256 con Numba.
        NOTA: Per massime prestazioni, considera librerie come hashlib in C.
        """
        # Questo è un placeholder - per vera ottimizzazione serve implementazione completa
        # o usare librerie C/Rust tramite CFFI
        pass

def _compute_hash_batch(header_76: bytes, start_nonce: int, batch_size: int, target_be: bytes):
    """
    Calcola hash per un batch di nonce e trova quello valido.

    Ottimizzazioni principali:
    - Precalcolo del primo chunk (64 byte) con sha256.copy() per evitare di
      rieseguire l'update della parte invariata ad ogni nonce.
    - Preallocazione di una "tail" (16 byte) in cui si aggiorna solo il nonce
      via struct.pack_into, minimizzando allocazioni.
    - Binding di funzioni locali per ridurre lookup in loop caldo.
    """
    # Suddivisione dell'header: primi 64 byte (chunk fisso) + tail statica (12 byte)
    first_chunk = header_76[:64]
    tail_static = header_76[64:]  # 12 byte: merkle[28:32] + ts(4) + bits(4)

    # Prepara il contesto sha256 per il primo chunk (invariato)
    sha_base = hashlib.sha256()
    sha_base.update(first_chunk)

    # Tail di 16 byte: 12 statici + 4 di nonce variabile
    tail = bytearray(16)
    tail[0:12] = tail_static

    # Local bindings per performance
    sha_copy = sha_base.copy
    sha256 = hashlib.sha256
    pack_into = struct.pack_into

    for i in range(batch_size):
        n = (start_nonce + i) & 0xFFFFFFFF
        # Scrive il nonce negli ultimi 4 byte della tail (little-endian)
        pack_into('<I', tail, 12, n)

        # Primo SHA-256: riusa lo stato del primo chunk
        ctx = sha_copy()
        ctx.update(tail)
        digest1 = ctx.digest()

        # Secondo SHA-256
        digest = sha256(digest1).digest()

        # Confronto con target (in big-endian): digest[::-1] è lendianness di Bitcoin
        if digest[::-1] < target_be:
            return n, digest

    return None, None

def mine_block_ultra(header_hex: str, target_hex: str, nonce_mode: str = "incremental", stop_event: Event | None = None):
    """
    Versione ultra-ottimizzata del mining.
    """
    log.info("Avvio mining ULTRA-OTTIMIZZATO - modalità %s", nonce_mode)

    # Decodifica header
    version   = unhexlify(header_hex[0:8])
    prev_hash = unhexlify(header_hex[8:72])
    merkle    = unhexlify(header_hex[72:136])
    ts_bytes  = unhexlify(header_hex[136:144])
    bits      = unhexlify(header_hex[144:152])

    header_76 = version + prev_hash + merkle + ts_bytes + bits
    target_be = int(target_hex, 16).to_bytes(32, "big")

    if nonce_mode == "incremental":
        nonce = 0
    elif nonce_mode in ("random", "mixed"):
        nonce = random.randint(0, 0xFFFFFFFF)
    else:
        raise ValueError("Modalità di mining non valida.")

    attempts = 0
    start_t = time.time()
    last_rate_t, last_rate_n = start_t, 0
    last_tsu = start_t

    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("Mining interrotto: nuovo blocco rilevato")
            return None, None, None

        # Aggiornamento timestamp
        timestamp_update_interval = int(os.getenv('TIMESTAMP_UPDATE_INTERVAL', 30))
        if timestamp_update_interval and (time.time() - last_tsu) >= timestamp_update_interval:
            ts_bytes = struct.pack("<I", int(time.time()))
            header_76 = version + prev_hash + merkle + ts_bytes + bits
            last_tsu = time.time()

        # Calcola batch
        found_nonce, digest = _compute_hash_batch(header_76, nonce, BATCH, target_be)
        
        if found_nonce is not None:
            total = time.time() - start_t
            hashrate = (attempts + BATCH) / total if total else 0
            
            full_header = header_76 + struct.pack("<I", found_nonce)
            
            log.info("Blocco trovato - nonce=%d tentativi=%d tempo=%.2fs hashrate=%.2f kH/s",
                     found_nonce, attempts + BATCH, total, hashrate/1000)
            log.info("Hash valido: %s", digest[::-1].hex())

            return hexlify(full_header).decode(), found_nonce, hashrate

        attempts += BATCH
        nonce = (nonce + BATCH) & 0xFFFFFFFF

        # Log periodico
        now = time.time()
        if now - last_rate_t >= RATE_INT:
            hashrate = (attempts - last_rate_n) / (now - last_rate_t)
            last_rate_t, last_rate_n = now, attempts

            log.info("Stato mining - hashrate=%.2f kH/s tentativi=%d nonce=%d",
                hashrate/1000, attempts, nonce)


# Funzione compatibile con l'interfaccia originale
def mine_block(header_hex: str, target_hex: str, nonce_mode: str = "incremental", stop_event: Event | None = None):
    """Wrapper per compatibilità con codice esistente.

    Usa sempre la versione ultra ottimizzata basata su hashlib.
    In assenza di Numba, evita import di moduli inesistenti.
    """
    return mine_block_ultra(header_hex, target_hex, nonce_mode, stop_event)
