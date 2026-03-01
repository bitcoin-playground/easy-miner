"""Modulo di utilità comune per funzioni condivise nel progetto miner."""

import hashlib
import logging
import threading

import config

log = logging.getLogger(__name__)


def double_sha256(data: bytes) -> bytes:
    """Esegue il doppio SHA-256 su un dato."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def encode_varint(value: int) -> str:
    """Codifica un numero come VarInt secondo il protocollo Bitcoin."""
    if value < 0xFD:
        return value.to_bytes(1, "little").hex()
    if value <= 0xFFFF:
        return "fd" + value.to_bytes(2, "little").hex()
    if value <= 0xFFFFFFFF:
        return "fe" + value.to_bytes(4, "little").hex()
    if value <= 0xFFFFFFFFFFFFFFFF:
        return "ff" + value.to_bytes(8, "little").hex()
    raise ValueError("Il valore supera il limite massimo per VarInt (2^64-1)")


def decode_nbits(nBits: int) -> str:
    """Decodifica il campo nBits in un target a 256-bit in formato esadecimale."""
    exponent    = (nBits >> 24) & 0xFF
    significand = nBits & 0x007FFFFF
    return f"{(significand << (8 * (exponent - 3))):064x}"


def calculate_target(template, difficulty_factor: float, network: str) -> str:
    """Calcola il target modificato in base alla rete e al fattore di difficoltà."""
    if network == "regtest":
        if difficulty_factor < 0:
            difficulty_factor = 0.1
    else:
        difficulty_factor = 1.0

    nBits_int       = int(template["bits"], 16)
    original_target = decode_nbits(nBits_int)

    if difficulty_factor == 0:
        return original_target

    max_target   = 0x00000000FFFF0000000000000000000000000000000000000000000000000000
    target_value = int(max_target / difficulty_factor)
    return f"{min(target_value, (1 << 256) - 1):064x}"


def watchdog_bestblock(
    rpc_conn,
    stop_event: threading.Event,
    new_block_event: threading.Event,
    get_best_block_hash_func,
) -> None:
    """
    Controlla periodicamente se c'è un nuovo best block.
    Quando viene rilevato, imposta new_block_event e stop_event
    per interrompere il miner corrente entro il prossimo batch.
    """
    log.info("Watchdog avviato.")
    try:
        last_hash = get_best_block_hash_func(rpc_conn)
    except Exception as e:
        log.error("Watchdog: impossibile ottenere l'hash iniziale: %s", e)
        return

    while not stop_event.wait(config.CHECK_INTERVAL):
        try:
            new_hash = get_best_block_hash_func(rpc_conn)
            if new_hash and new_hash != last_hash:
                log.info("Nuovo best block: %s", new_hash)
                last_hash = new_hash
                new_block_event.set()
                stop_event.set()  # interrompe il miner corrente
                return
        except Exception as e:
            log.error("Errore watchdog: %s", e)

    log.info("Watchdog fermato.")
