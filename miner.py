import random
import struct
import time
import hashlib
import logging
from binascii import hexlify, unhexlify
from threading import Event
from typing import Callable

import config

log = logging.getLogger(__name__)

# Secondi tra un log di hashrate e il successivo
_RATE_INT = 2


def _compute_hash_batch(
    header_76: bytes,
    start_nonce: int,
    batch_size: int,
    target_be: bytes,
):
    """
    Calcola hash per un batch di nonce e ritorna il primo nonce valido trovato.

    Ottimizzazioni:
    - Precalcolo del primo chunk (64 byte) con sha256.copy() per evitare di
      rieseguire l'update della parte invariata ad ogni nonce.
    - Preallocazione di una "tail" (16 byte) aggiornata solo nel campo nonce
      tramite struct.pack_into, minimizzando allocazioni.
    - Binding locale di funzioni per ridurre lookup nel loop caldo.
    """
    first_chunk  = header_76[:64]
    tail_static  = header_76[64:]   # 12 byte: merkle[28:32] + ts(4) + bits(4)

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

        ctx = sha_copy()
        ctx.update(tail)
        digest = sha256(ctx.digest()).digest()

        if digest[::-1] < target_be:
            return n, digest

    return None, None


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
    log.info("Avvio mining — modalità %s", nonce_mode)

    if nonce_mode not in ("incremental", "random", "mixed"):
        raise ValueError(f"Modalità di mining non valida: {nonce_mode!r}")

    version   = unhexlify(header_hex[0:8])
    prev_hash = unhexlify(header_hex[8:72])
    merkle    = unhexlify(header_hex[72:136])
    ts_bytes  = unhexlify(header_hex[136:144])
    bits      = unhexlify(header_hex[144:152])

    header_76 = version + prev_hash + merkle + ts_bytes + bits
    target_be = int(target_hex, 16).to_bytes(32, "big")

    nonce = 0 if nonce_mode == "incremental" else random.randint(0, 0xFFFFFFFF)

    # Leggi configurazione una volta prima del loop
    batch_size  = config.BATCH
    ts_interval = config.TIMESTAMP_UPDATE_INTERVAL

    attempts    = 0
    start_t     = time.time()
    last_rate_t = start_t
    last_rate_n = 0
    last_tsu    = start_t

    while True:
        if stop_event is not None and stop_event.is_set():
            log.info("Mining interrotto: stop_event ricevuto")
            return None, None, None

        now = time.time()

        # Aggiornamento periodico del timestamp nell'header
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
                "Blocco trovato - nonce=%d tentativi=%d tempo=%.2fs hashrate=%.2f kH/s",
                found_nonce, attempts + batch_size, total, hashrate / 1000,
            )
            log.info("Hash valido: %s", digest[::-1].hex())
            return hexlify(full_header).decode(), found_nonce, hashrate

        attempts += batch_size
        nonce     = (nonce + batch_size) & 0xFFFFFFFF

        # Log e callback periodici
        now = time.time()
        if now - last_rate_t >= _RATE_INT:
            hashrate    = (attempts - last_rate_n) / (now - last_rate_t)
            last_rate_t = now
            last_rate_n = attempts
            log.info(
                "Stato mining - hashrate=%.2f kH/s tentativi=%d nonce=%d",
                hashrate / 1000, attempts, nonce,
            )
            if status_callback:
                status_callback(attempts, hashrate)
