import time, threading, hashlib, logging, os
from dotenv import load_dotenv
from rpc import (
    connect_rpc, test_rpc_connection, get_block_template, ensure_witness_data,
    submit_block, get_best_block_hash 
)
from block_builder import (
    calculate_merkle_root, build_block_header, is_segwit_tx,
    serialize_block, build_coinbase_transaction
)
from miner import mine_block
from utils import calculate_target, watchdog_bestblock

# Carica le variabili d'ambiente dal file .env
load_dotenv()

log = logging.getLogger(__name__)

# Parametri mining
EXTRANONCE1 = "1234567890abcdef"
EXTRANONCE2 = "12341234"

# Intervallo controllo best-block
CHECK_INTERVAL = 20




# --------------------------- ciclo principale --------------------------------
def main():
    # TEST RPC
    test_rpc_connection()
    
    # Log dell'extranonce2 utilizzato
    log.info(f"Extranonce2 utilizzato: {EXTRANONCE2}")

    while True:
        try:
            log.info("=== Nuovo ciclo di mining ===")

            # STEP 1) Ottieni una nuova connessione per il template
            rpc_template = connect_rpc()

            # STEP 2) GET BLOCK TEMPLATE
            template = get_block_template(rpc_template)
            if not template:
                log.error("Impossibile ottenere il template del blocco. Riprovo...")
                time.sleep(5)
                continue

            # STEP 3) Assicurarsi di avere transazioni con dati completi
            ensure_witness_data(rpc_template, template)

            tot_tx       = len(template["transactions"])
            witness_tx   = sum(1 for tx in template["transactions"] if is_segwit_tx(tx["data"]))
            legacy_tx    = tot_tx - witness_tx

            log.info(f"Transazioni nel template: totali = {tot_tx}  |  legacy = {legacy_tx}  |  segwit = {witness_tx}")

            # STEP 4) COSTRUISCI COINBASE
            wallet_address = os.getenv('WALLET_ADDRESS')
            miner_info = rpc_template.getaddressinfo(wallet_address)
            miner_script_pubkey = miner_info["scriptPubKey"]
            coinbase_message = os.getenv('COINBASE_MESSAGE')
            coinbase_tx, coinbase_txid = build_coinbase_transaction(
                template, miner_script_pubkey, EXTRANONCE1, EXTRANONCE2, coinbase_message
            )
            log.info(f"Messaggio nella coinbase: {coinbase_message}")

            # STEP 5) CALCOLA TARGET
            modified_target = calculate_target(template, float(os.getenv('DIFFICULTY_FACTOR', 1.0)), rpc_template.getblockchaininfo().get("chain", ""))

            # STEP 6) CALCOLA MERKLE ROOT
            merkle_root = calculate_merkle_root(coinbase_txid, template["transactions"])

            # STEP 7) COSTRUISCI HEADER
            header_hex = build_block_header(
                template["version"], template["previousblockhash"],
                merkle_root, template["curtime"], template["bits"], 0
            )

            # ---------- watchdog: avvia thread di controllo best-block ----------
            stop_event = threading.Event()
            rpc_watch  = connect_rpc()
            new_block_event = threading.Event()
            t_watch = threading.Thread(
                target=watchdog_bestblock, args=(rpc_watch, stop_event, new_block_event, get_best_block_hash), daemon=True
            )
            t_watch.start()

            # STEP 8) MINING
            nonce_mode = os.getenv('NONCE_MODE', 'mixed')
            mined_header_hex, nonce, hashrate = mine_block(
                header_hex, modified_target, nonce_mode, stop_event
            )

            # Chiudi watchdog
            stop_event.set()
            t_watch.join(timeout=0.2)

            # se il mining è stato interrotto da nuovo blocco → ricomincia il ciclo
            if new_block_event.is_set() or mined_header_hex is None:
                log.info("Nuovo blocco minato: riparto con un template aggiornato")
                continue

            # STEP 9) SERIALIZZA IL BLOCCO
            serialized_block = serialize_block(
                mined_header_hex, coinbase_tx, template["transactions"]
            )
            if not serialized_block:
                log.error("Blocco non serializzato correttamente. Riprovo...")
                continue

            # STEP 10) CALCOLA L'HASH DEL BLOCCO E INVIALO
            # Calcola l'hash del blocco dall'header
            header_bytes = bytes.fromhex(mined_header_hex)
            block_hash = hashlib.sha256(hashlib.sha256(header_bytes).digest()).digest()[::-1].hex()
            log.info(f"Hash del blocco trovato: {block_hash}")
            
            # Invia il blocco
            rpc_submit = connect_rpc()
            submit_block(rpc_submit, serialized_block)

        except Exception:
            log.exception("Errore nel ciclo di mining")

        # Pausa prima di iniziare un nuovo ciclo
        log.info("Ciclo completato, in attesa del prossimo ciclo...")
        time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    main()
