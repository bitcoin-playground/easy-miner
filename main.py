import hashlib
import logging
import threading
import time

import config
import log_setup
from block_builder import (
    build_block_header, build_coinbase_transaction,
    calculate_merkle_root, is_segwit_tx, serialize_block,
)
from miner import mine_block
from rpc import (
    connect_rpc, get_block_template,
    ensure_witness_data, submit_block, test_rpc_connection,
    wait_for_new_template,
)
from utils import calculate_target, watchdog_longpoll

log = logging.getLogger(__name__)


def _prepare_template(rpc) -> dict | None:
    """Ottiene e arricchisce il template di blocco. Ritorna None in caso di errore."""
    template = get_block_template(rpc)
    if not template:
        return None
    ensure_witness_data(rpc, template)
    tot_tx     = len(template["transactions"])
    witness_tx = sum(1 for tx in template["transactions"] if is_segwit_tx(tx["data"]))
    log.info(
        "Template altezza=%d  tx totali=%d  legacy=%d  segwit=%d",
        template["height"], tot_tx, tot_tx - witness_tx, witness_tx,
    )
    return template


def main(
    event_queue=None,
    worker_idx: int = 0,
    extranonce2: str | None = None,
) -> None:
    """
    Ciclo principale di mining.

    Parametri opzionali per l'uso multiprocesso via launcher:
      event_queue  – coda su cui inviare eventi strutturati al supervisore
      worker_idx   – indice del worker (per identificazione negli eventi)
      extranonce2  – valore extranonce2 specifico del worker
    """
    extranonce2 = extranonce2 or config.EXTRANONCE2

    test_rpc_connection()
    log.info("Extranonce2: %s | Coinbase: %s", extranonce2, config.COINBASE_MESSAGE)

    # Connessioni RPC riutilizzate per tutto il ciclo di vita del processo
    rpc       = connect_rpc()
    rpc_watch = connect_rpc()  # connessione dedicata al watchdog (può bloccare in long-poll)

    # Recupera rete e scriptPubKey una volta sola — non cambiano tra i cicli
    network      = rpc.getblockchaininfo().get("chain", "")
    miner_script = rpc.getaddressinfo(config.WALLET_ADDRESS)["scriptPubKey"]
    log.info("Rete: %s", network)

    def _on_status(attempts: int, hashrate: float) -> None:
        if event_queue is not None:
            event_queue.put(("status", worker_idx, {"rate": hashrate / 1000, "attempts": attempts}))

    while True:
        try:
            log.info("=== Nuovo ciclo di mining ===")

            # STEP 1-3: template
            template = _prepare_template(rpc)
            if not template:
                log.error("Impossibile ottenere il template. Riprovo tra 5s…")
                time.sleep(5)
                continue

            # STEP 4: coinbase
            coinbase_tx, coinbase_txid = build_coinbase_transaction(
                template, miner_script,
                config.EXTRANONCE1, extranonce2,
                config.COINBASE_MESSAGE,
            )

            # STEP 5-7: target, merkle root, header
            modified_target = calculate_target(template, config.DIFFICULTY_FACTOR, network)
            merkle_root     = calculate_merkle_root(coinbase_txid, template["transactions"])
            header_hex      = build_block_header(
                template["version"], template["previousblockhash"],
                merkle_root, template["curtime"], template["bits"], 0,
            )

            # STEP 8: avvia watchdog long-poll e mining
            stop_event      = threading.Event()
            new_block_event = threading.Event()
            longpollid      = template.get("longpollid", "")
            t_watch = threading.Thread(
                target=watchdog_longpoll,
                args=(rpc_watch, stop_event, new_block_event, longpollid, wait_for_new_template),
                daemon=True,
            )
            t_watch.start()

            mined_header_hex, nonce, hashrate = mine_block(
                header_hex, modified_target, config.NONCE_MODE, stop_event, _on_status,
            )

            stop_event.set()
            t_watch.join(timeout=0.2)

            if new_block_event.is_set() or mined_header_hex is None:
                log.info("Ciclo interrotto: riparto con template aggiornato")
                continue

            # STEP 9: hash del blocco e notifica al supervisore
            header_bytes = bytes.fromhex(mined_header_hex)
            block_hash   = hashlib.sha256(hashlib.sha256(header_bytes).digest()).digest()[::-1].hex()
            log.info("Hash del blocco trovato: %s", block_hash)

            if event_queue is not None:
                event_queue.put(("found", worker_idx, {"rate": hashrate / 1000 if hashrate else 0}))
                event_queue.put(("hash", worker_idx, block_hash))

            # STEP 10: serializza e invia
            serialized_block = serialize_block(mined_header_hex, coinbase_tx, template["transactions"])
            if not serialized_block:
                log.error("Serializzazione blocco fallita. Riprovo…")
                continue

            submit_block(rpc, serialized_block)

            if event_queue is not None:
                event_queue.put(("submit", worker_idx, None))

        except Exception:
            log.exception("Errore nel ciclo di mining")

        time.sleep(1)


if __name__ == "__main__":
    log_setup.configure()
    main()
