import logging

from bitcoinrpc.authproxy import AuthServiceProxy

import config

log = logging.getLogger(__name__)


def close_rpc(conn) -> None:
    """Chiude la connessione HTTP sottostante di un AuthServiceProxy."""
    if conn is None:
        return
    try:
        conn._AuthServiceProxy__conn.close()
        log.debug("Connessione RPC chiusa")
    except Exception:
        pass


def connect_rpc(timeout: int = 30) -> AuthServiceProxy:
    """Crea una connessione RPC al nodo Bitcoin.

    timeout: secondi prima che la connessione HTTP scada.
    Usare un valore alto (es. 300) per connessioni dedicate al long-polling.
    """
    return AuthServiceProxy(
        f"http://{config.RPC_USER}:{config.RPC_PASSWORD}@{config.RPC_HOST}:{config.RPC_PORT}",
        timeout=timeout,
    )


def test_rpc_connection() -> None:
    """Verifica la connessione e mostra informazioni di base sulla blockchain."""
    log.info("Verifica connessione RPC")
    conn = None
    try:
        conn = connect_rpc()
        info = conn.getblockchaininfo()
        log.info(
            "Connessione RPC riuscita — chain=%s, blocchi=%d, difficoltà=%s",
            info["chain"], info["blocks"], info["difficulty"],
        )
    except Exception:
        log.exception("Errore di connessione RPC")
        raise
    finally:
        close_rpc(conn)


def get_best_block_hash(rpc) -> str | None:
    """Recupera l'hash del blocco più recente."""
    try:
        h = rpc.getbestblockhash()
        log.debug("Best block hash: %s", h)
        return h
    except Exception as e:
        log.error("Errore RPC getbestblockhash: %s", e)
        return None


def get_block_template(rpc) -> dict | None:
    """Richiede un template di blocco con supporto SegWit."""
    try:
        tpl = rpc.getblocktemplate({"rules": ["segwit"]})
        log.debug("Template ricevuto — altezza=%d, tx=%d", tpl["height"], len(tpl["transactions"]))
        return tpl
    except Exception as e:
        log.error("Errore RPC getblocktemplate: %s", e)
        return None


def wait_for_new_template(rpc, longpollid: str) -> bool:
    """
    Blocca finché Bitcoin Core segnala un nuovo template tramite long-polling.

    Passa longpollid alla chiamata getblocktemplate: il nodo risponde solo
    quando c'è un nuovo blocco o una variazione significativa delle transazioni.

    Ritorna True se il nodo ha risposto con un nuovo template (nuovo blocco).
    Ritorna False se la chiamata è scaduta o ha prodotto un errore di rete:
    in questo caso il chiamante deve fare retry senza riavviare il mining.
    """
    try:
        rpc.getblocktemplate({"rules": ["segwit"], "longpollid": longpollid})
        log.debug("Long-poll completato: nuovo template disponibile")
        return True
    except Exception as e:
        log.debug("Long-poll timeout o errore (retry): %s", e)
        return False


def ensure_witness_data(rpc, template: dict) -> None:
    """
    Arricchisce le transazioni del template con i dati witness completi.
    Usa una singola chiamata HTTP batch per ridurre la latenza rispetto a N chiamate singole.
    """
    txs = template["transactions"]
    if not txs:
        return

    # Batch JSON-RPC: una sola richiesta HTTP per tutte le transazioni
    try:
        batch   = [["getrawtransaction", tx["txid"], False] for tx in txs]
        results = rpc._batch(batch)
        raw_map = {
            txs[r["id"]]["txid"]: r["result"]
            for r in results
            if r.get("result") is not None
        }
    except Exception as e:
        log.warning("Batch RPC non disponibile, uso chiamate singole: %s", e)
        raw_map = {}
        for tx in txs:
            try:
                raw = rpc.getrawtransaction(tx["txid"], False)
                if raw:
                    raw_map[tx["txid"]] = raw
            except Exception as e2:
                log.debug("Raw witness mancante per %s: %s", tx["txid"], e2)

    template["transactions"] = [
        {"hash": tx["txid"], "data": raw_map.get(tx["txid"], tx["data"])}
        for tx in txs
    ]


def submit_block(rpc, serialized_block: str) -> None:
    """Invia il blocco minato al nodo Bitcoin."""
    log.info("Invio blocco serializzato (%d byte) al nodo", len(serialized_block) // 2)
    if not serialized_block:
        log.error("Blocco non serializzato correttamente — invio annullato")
        return
    try:
        result = rpc.submitblock(serialized_block)
        if result is None:
            log.info("Blocco accettato nella blockchain")
        else:
            log.error("submitblock ha restituito: %s", result)
    except Exception:
        log.exception("Errore RPC durante submitblock")
