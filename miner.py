import ctypes
import hashlib
import logging
import os
import random
import struct
import time
from binascii import hexlify, unhexlify
from threading import Event
from typing import Callable

import config
from utils import fmt_hashrate

log = logging.getLogger(__name__)

# Secondi tra un log di hashrate e il successivo
_RATE_INT = 2

# ---------------------------------------------------------------------------
# Caricamento C extension (native/miner_core.so)
# ---------------------------------------------------------------------------

_SO_PATH = os.path.join(os.path.dirname(__file__), "native", "miner_core.so")
_lib: ctypes.CDLL | None = None

try:
    _lib = ctypes.CDLL(_SO_PATH)
    _lib.find_nonce.restype  = ctypes.c_int64
    _lib.find_nonce.argtypes = [
        ctypes.c_char_p,  # header_76
        ctypes.c_char_p,  # target_be
        ctypes.c_uint32,  # start_nonce
        ctypes.c_uint32,  # batch_size
    ]
    log.info("C extension caricata: %s (SHA-256 hardware attivo)", _SO_PATH)
except OSError:
    log.warning(
        "C extension non trovata (%s). "
        "Esegui 'make' in native/ per abilitare il loop SHA-256 ottimizzato. "
        "Uso fallback Python.",
        _SO_PATH,
    )


# ---------------------------------------------------------------------------
# _compute_hash_batch — versione C (veloce) o Python (fallback)
# ---------------------------------------------------------------------------

def _compute_hash_batch(
    header_76: bytes,
    start_nonce: int,
    batch_size: int,
    target_be: bytes,
):
    """
    Cerca il primo nonce valido in [start_nonce, start_nonce+batch_size).
    Usa la C extension se disponibile, altrimenti il loop Python.
    Ritorna (nonce, digest) oppure (None, None).
    """
    if _lib is not None:
        result = _lib.find_nonce(header_76, target_be, start_nonce, batch_size)
        if result < 0:
            return None, None
        nonce  = int(result)
        digest = hashlib.sha256(
            hashlib.sha256(header_76 + struct.pack("<I", nonce)).digest()
        ).digest()
        return nonce, digest

    # --- Fallback Python (stessa logica, ottimizzato con midstate) ---
    first_chunk  = header_76[:64]
    tail_static  = header_76[64:]

    sha_base = hashlib.sha256()
    sha_base.update(first_chunk)

    tail       = bytearray(16)
    tail[0:12] = tail_static

    sha_copy  = sha_base.copy
    sha256    = hashlib.sha256
    pack_into = struct.pack_into

    for i in range(batch_size):
        n = (start_nonce + i) & 0xFFFFFFFF
        pack_into("<I", tail, 12, n)
        ctx    = sha_copy()
        ctx.update(tail)
        digest = sha256(ctx.digest()).digest()
        if digest[::-1] < target_be:
            return n, digest

    return None, None


# ---------------------------------------------------------------------------
# mine_block — ciclo principale di mining
# ---------------------------------------------------------------------------

def mine_block(
    header_hex: str,
    target_hex: str,
    nonce_mode: str = "incremental",
    stop_event: Event | None = None,
    status_callback: Callable | None = None,
):
    """
    Esegue il mining iterando nonce fino a trovare un hash valido o ricevere stop_event.

    Chiama status_callback(attempts, hashrate_hz) ogni ~2 secondi se fornito.
    Ritorna (header_hex_minato, nonce, hashrate) oppure (None, None, None) se interrotto.
    """
    backend = "C extension" if _lib is not None else "Python fallback"
    log.info("Avvio mining — modalità %s | backend: %s", nonce_mode, backend)

    if nonce_mode not in ("incremental", "random"):
        raise ValueError(f"Modalità di mining non valida: {nonce_mode!r}")

    version   = unhexlify(header_hex[0:8])
    prev_hash = unhexlify(header_hex[8:72])
    merkle    = unhexlify(header_hex[72:136])
    ts_bytes  = unhexlify(header_hex[136:144])
    bits      = unhexlify(header_hex[144:152])

    header_76 = version + prev_hash + merkle + ts_bytes + bits
    target_be = int(target_hex, 16).to_bytes(32, "big")

    nonce = 0 if nonce_mode == "incremental" else random.randint(0, 0xFFFFFFFF)

    batch_size  = config.BATCH
    ts_interval = config.TIMESTAMP_UPDATE_INTERVAL

    attempts    = 0
    start_t     = time.time()
    last_rate_t = start_t
    last_rate_n = 0
    last_tsu    = start_t

    while True:
        if stop_event is not None and stop_event.is_set():
            log.debug("Mining interrotto: stop_event ricevuto")
            return None, None, None

        now = time.time()

        if ts_interval and (now - last_tsu) >= ts_interval:
            ts_bytes   = struct.pack("<I", int(now))
            header_76  = version + prev_hash + merkle + ts_bytes + bits
            last_tsu   = now

        found_nonce, digest = _compute_hash_batch(header_76, nonce, batch_size, target_be)

        if found_nonce is not None:
            total       = time.time() - start_t
            hashrate    = (attempts + batch_size) / total if total else 0
            full_header = header_76 + struct.pack("<I", found_nonce)
            log.info(
                "Blocco trovato — nonce=%d | %s hash | %.2fs | %s",
                found_nonce, f"{attempts+batch_size:,}", total, fmt_hashrate(hashrate),
            )
            log.info("Hash valido: %s", digest[::-1].hex())
            return hexlify(full_header).decode(), found_nonce, hashrate

        attempts += batch_size
        nonce     = (nonce + batch_size) & 0xFFFFFFFF

        now = time.time()
        if now - last_rate_t >= _RATE_INT:
            hashrate    = (attempts - last_rate_n) / (now - last_rate_t)
            last_rate_t = now
            last_rate_n = attempts
            log.info(
                "hashrate %s  |  %s hash  |  nonce %d",
                fmt_hashrate(hashrate), f"{attempts:,}", nonce,
            )
            if status_callback:
                status_callback(attempts, hashrate)
