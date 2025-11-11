"""Modulo di utilità comune per funzioni condivise nel progetto miner."""

import hashlib
import os
import time
import logging
import threading

log = logging.getLogger(__name__)

# Intervallo controllo best-block
CHECK_INTERVAL = 20

def double_sha256(data):
    """Esegue il doppio SHA-256 su un dato."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()

def encode_varint(value):
    """Codifica un numero come VarInt secondo il protocollo Bitcoin."""
    thresholds = [(0xfd, ""), (0xffff, "fd"), (0xffffffff, "fe"), (0xffffffffffffffff, "ff")]
    
    for threshold, prefix in thresholds:
        if value <= threshold:
            byte_length = max(1, (threshold.bit_length() + 7) // 8)
            return prefix + value.to_bytes(byte_length, 'little').hex()
            
    raise ValueError("Il valore supera il limite massimo per VarInt (2^64-1)")

def decode_nbits(nBits: int) -> str:
    """Decodifica il campo nBits in un target a 256-bit in formato esadecimale."""
    exponent = (nBits >> 24) & 0xff
    significand = nBits & 0x007fffff
    return f"{(significand << (8 * (exponent - 3))):064x}"

def calculate_target(template, difficulty_factor, network):
    """Calcola il target modificato in base alla rete e al fattore di difficoltà."""
    if network == "regtest":
        if difficulty_factor < 0:
            difficulty_factor = 0.1
    else:
        difficulty_factor = 1.0
    
    nBits_int = int(template["bits"], 16)
    original_target = decode_nbits(nBits_int)
    
    if difficulty_factor == 0:
        return original_target
    else:
        max_target = 0x00000000FFFF0000000000000000000000000000000000000000000000000000
        target_value = int(max_target / difficulty_factor)
        max_possible_target = (1 << 256) - 1
        if target_value > max_possible_target:
            target_value = max_possible_target
        return f"{target_value:064x}"

def watchdog_bestblock(rpc_conn, stop_event: threading.Event, new_block_event: threading.Event, get_best_block_hash_func):
    """
    Controlla periodicamente se c'è un nuovo best block e setta new_block_event.
    """
    log.info("Watchdog per il best block avviato.")
    try:
        last_hash = get_best_block_hash_func(rpc_conn)
    except Exception as e:
        log.error(f"Watchdog: impossibile ottenere l'hash del blocco iniziale: {e}")
        return

    while not stop_event.wait(CHECK_INTERVAL):
        try:
            new_hash = get_best_block_hash_func(rpc_conn)
            if new_hash and new_hash != last_hash:
                log.info(f"Nuovo best block trovato: {new_hash}")
                last_hash = new_hash
                new_block_event.set()  # Segnala che è stato trovato un nuovo blocco
        except Exception as e:
            log.error(f"Errore nel watchdog: {e}")

    log.info("Watchdog per il best block fermato.")