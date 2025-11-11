from bitcoinrpc.authproxy import AuthServiceProxy
import os
from dotenv import load_dotenv
import logging

load_dotenv() # Carica le variabili d'ambiente dal file .env

log = logging.getLogger(__name__)

def connect_rpc():
    """
    Crea una connessione RPC al nodo Bitcoin utilizzando le credenziali configurate.
    """
    rpc_user = os.getenv('RPC_USER')
    rpc_password = os.getenv('RPC_PASSWORD')
    rpc_host = os.getenv('RPC_HOST')
    rpc_port = os.getenv('RPC_PORT')
    
    return AuthServiceProxy(f"http://{rpc_user}:{rpc_password}@{rpc_host}:{rpc_port}")

def test_rpc_connection():
    """
    Verifica la connessione al nodo Bitcoin e mostra informazioni di base sulla blockchain.
    """
    # Avvio test connessione  → INFO
    log.info("Verifica connessione RPC")
    try:
        # Crea una connessione RPC
        rpc = connect_rpc()
        # Richiede informazioni generali sulla blockchain
        info = rpc.getblockchaininfo()
        # Mostra le informazioni principali
        # Esito positivo        → INFO
        log.info("Connessione RPC riuscita – chain=%s, blocchi=%d, difficoltà=%s",
                 info['chain'], info['blocks'], info['difficulty'])

    except Exception as e:
        # Stack-trace completo  → EXCEPTION
        log.exception("Errore di connessione RPC")
        raise

def get_best_block_hash(rpc):
    """
    Recupera l'hash del blocco più recente nella blockchain (best block).
    """
    try:
        # Richiede l'hash del blocco più recente
        best_block_hash = rpc.getbestblockhash()
        # Valore restituito    → DEBUG (informativo ma non essenziale)
        log.debug("Best block hash: %s", best_block_hash)
        return best_block_hash
    except Exception as e:
        # Gestisce eventuali errori durante la chiamata RPC
        log.error("Errore RPC getbestblockhash: %s", e)
        return None

def get_block_template(rpc):
    """
    Richiede un template di blocco al nodo Bitcoin con supporto per le regole SegWit.
    """
    try:
        # Richiede il template specificando il supporto per SegWit
        tpl = rpc.getblocktemplate({"rules": ["segwit"]})
        log.debug("Template ricevuto - altezza %d, %d tx",
                  tpl.get("height"), len(tpl["transactions"]))
        return tpl
    except Exception as e:
        # Gestisce eventuali errori durante la richiesta
        log.error("Errore RPC getblocktemplate: %s", e)
        return None
    
def ensure_witness_data(rpc, template):
    """
    Controlla e aggiorna le transazioni del template con dati completi, inclusi i dati witness.
    """
    # Lista per le transazioni corrette
    corrected_txs = []
    
    # Recupera informazioni dettagliate sulla mempool
    try:
        # getrawmempool(True) restituisce informazioni dettagliate su tutte le transazioni nella mempool
        mempool_info = rpc.getrawmempool(True)
    except Exception as e:
        log.warning("Impossibile recuperare la mempool dettagliata: %s", e)
        mempool_info = {}
    
    # Elabora ogni transazione nel template
    for tx in template["transactions"]:
        txid = tx["txid"]  # ID della transazione
        raw = tx["data"]   # Dati grezzi della transazione
        
        # Cerca il witness txid (wtxid) nella mempool
        if txid in mempool_info:
            # Se la transazione è nella mempool, prova a ottenere il wtxid
            wtxid = mempool_info[txid].get("wtxid", txid)
        else:
            # Altrimenti usa il txid normale
            wtxid = txid  # Usa il txid se il wtxid non è disponibile
        
        # Prova a recuperare la transazione completa con i dati witness
        try:
            # getrawtransaction recupera i dati grezzi completi di una transazione
            raw_tx_full = rpc.getrawtransaction(txid, False)
            if raw_tx_full:
                raw = raw_tx_full  # Usa i dati completi se disponibili
        except Exception as e:
            log.debug("Raw witness mancante per %s: %s", txid, e)
        
        # Aggiunge la transazione corretta alla lista
        corrected_txs.append({"hash": txid, "data": raw})
    
    # Sostituisce le transazioni nel template con quelle corrette
    template["transactions"] = corrected_txs

def submit_block(rpc, serialized_block):
    """
    Invia il blocco minato al nodo Bitcoin per la validazione e l'inclusione nella blockchain.
    """
    log.info("Invio del blocco serializzato (%d byte) al nodo",
             len(serialized_block)//2)
    
    # Verifica che il blocco sia stato serializzato correttamente
    if not serialized_block:
        log.error("Blocco non serializzato correttamente - invio annullato")
        return

    try:
        # Invia il blocco al nodo Bitcoin
        result = rpc.submitblock(serialized_block)
        
        # Verifica il risultato dell'invio
        if result is None:
            log.info("Blocco accettato nella blockchain")
        else:
            log.error("submitblock ha restituito un errore: %s", result)

    except Exception as e:
        # Gestisce eventuali errori durante la chiamata RPC
        log.exception("Errore RPC durante submitblock")
