from __future__ import annotations

import argparse
import importlib
import logging
import multiprocessing as mp
import os
import re
import sys
import time
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env
load_dotenv()

# Configurazione pattern
PATTERNS = {
    # Stato periodico del miner
    "status": re.compile(
        r"Stato mining - hashrate=([0-9.]+) kH/s tentativi=(\d+)"
    ),

    # Blocco trovato (con dettagli)
    "found_stats": re.compile(
        r"Blocco trovato - nonce=\d+ tentativi=(\d+) tempo=([0-9.]+)s hashrate=([0-9.]+) kH/s"
    ),

    # Blocco trovato (semplice)
    "found_simple": re.compile(r"Blocco trovato"),

    # Hash del blocco
    "hash": re.compile(r"Hash del blocco trovato: ([0-9a-fA-F]+)"),

    # Esito submit
    "submit": re.compile(r"Blocco accettato nella blockchain|submitblock ha restituito un errore"),
}

# Processo worker

def _extranonce2(base: str, idx: int) -> str:
    """Restituisce `base + idx` in esadecimale, mantenendo la stessa larghezza."""
    return f"{int(base, 16) + idx:0{len(base)}x}"

def _worker(idx: int, base_ex2: str, q: mp.Queue):
    """Avvia un processo di mining."""
    # Pin CPU (best-effort)
    try:
        os.sched_setaffinity(0, {idx})
    except (AttributeError, OSError):
        pass

    # patch al modulo main
    main = importlib.import_module("main")
    main.EXTRANONCE2 = _extranonce2(base_ex2, idx)

    class _QueueHandler(logging.Handler):
        """Invia LogRecord al processo padre via coda."""
        def emit(self, record: logging.LogRecord) -> None:
            msg = self.format(record)

            # Metriche periodiche
            if m := PATTERNS["status"].search(msg):
                rate = float(m.group(1))
                attempts = int(m.group(2))
                q.put(("status", idx, {"rate": rate, "attempts": attempts}))
                return
            # Blocco trovato
            if m := PATTERNS["found_stats"].search(msg):
                attempts = int(m.group(1))
                t_sec = float(m.group(2))
                rate = float(m.group(3))
                q.put(("found", idx, {"attempts": attempts, "time": t_sec, "rate": rate}))
                return
            if PATTERNS["found_simple"].search(msg):
                q.put(("found", idx, None))
                return
            # Hash/submit
            if m := PATTERNS["hash"].search(msg):
                q.put(("hash", idx, m.group(1)))
                return
            if PATTERNS["submit"].search(msg):
                q.put(("submit", idx, None))
                return
            # Altri record ignorati

    _fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    h = _QueueHandler(); h.setFormatter(_fmt)
    logging.basicConfig(level=logging.INFO, handlers=[h])

    try:
        main.main()
    except KeyboardInterrupt:
        pass

# Supervisore

def _clear_lines(n_lines: int):
    """Pulisce le ultime n righe del terminale."""
    for _ in range(n_lines):
        sys.stdout.write("\033[F\033[K")
    sys.stdout.flush()

def _aggregate(q: mp.Queue, n: int) -> str:
    """Aggrega metriche da tutti i worker e gestisce l'output del terminale."""
    rates = [0.0] * n
    attempts = [0] * n
    block_hash: str | None = None
    winner_idx: int | None = None
    winner_info: dict | None = None

    t_start = time.time()
    last_print = 0.0
    lines_printed = 0

    while True:
        try:
            tag, idx, val = q.get(timeout=0.1)

            if tag == "status":
                rates[idx] = val["rate"]
                attempts[idx] = val["attempts"]
            elif tag == "found":
                winner_idx = idx
                winner_info = val
            elif tag == "hash":
                block_hash = val
            elif tag == "submit":
                _clear_lines(lines_printed)
                elapsed = time.time() - t_start
                total_attempts = sum(attempts)
                avg_rate_k = total_attempts / elapsed / 1000 if elapsed else 0.0
                print("=" * 78)
                print(f"[✓] BLOCCO TROVATO E INVIATO")
                print(f"  • Hash: {block_hash or 'N/D'}")
                if winner_idx is not None and winner_info:
                    print(f"  • Worker: {winner_idx}")
                    print(f"  • Tempo ricerca: {winner_info['time']:.2f}s")
                    print(f"  • Tentativi worker: {winner_info['attempts']:,}")
                print(f"  • Hashrate medio totale: {avg_rate_k:,.2f} kH/s")
                print(f"  • Tentativi totali: {total_attempts:,}")
                print("=" * 78)
                return "restart"

        except Exception:
            pass  # Coda vuota

        now = time.time()
        if now - last_print >= 1.0:
            if lines_printed > 0:
                _clear_lines(lines_printed)

            tot_rate = sum(rates)
            tot_attempts = sum(attempts)
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

            lines = []
            lines.append(f"{ts} | STATO MINING")
            lines.append("=" * 40)
            lines.append(f"Totale: {tot_rate:,.2f} kH/s | Tentativi: {tot_attempts:,}")
            lines.append("-" * 40)
            for i in range(n):
                rate_str = f"{rates[i]:.2f} kH/s"
                attempts_str = f"{attempts[i]:,}"
                lines.append(f"Worker {i:<2}: {rate_str:<15} | Tentativi: {attempts_str:<15}")
            
            output = "\n".join(lines)
            print(output, flush=True)
            lines_printed = len(lines)
            last_print = now

# Ciclo avvio/riavvio

def launch(n: int, base_ex2: str) -> None:
    # Visualizza l'extranonce2 che ogni processo utilizzerà nella coinbase (solo in debug)
    log = logging.getLogger(__name__)
    log.info("Extranonce2 utilizzati dai processi:")
    for i in range(n):
        extranonce2 = _extranonce2(base_ex2, i)
        log.info(f"  • Processo {i}: extranonce2={extranonce2}")
    
    while True:
        q: mp.Queue = mp.Queue()
        workers = [mp.Process(target=_worker, args=(i, base_ex2, q), daemon=True) for i in range(n)]
        for p in workers:
            p.start()

        try:
            reason = _aggregate(q, n)
        finally:
            for p in workers:
                if p.is_alive():
                    p.terminate()
            for p in workers:
                p.join()

        if reason != "restart":
            break
        print("\nRiavvio dei worker…\n")
        time.sleep(1)

# Entry-point CLI
def _parse_args():
    # Importa l'EXTRANONCE2 da main.py come valore predefinito
    import main
    default_extranonce2 = main.EXTRANONCE2
        
    parser = argparse.ArgumentParser("Launcher multiprocesso per miner main.py")

    # Determina il numero di processori
    try:
        num_procs_env = int(os.getenv("NUM_PROCESSORS", 0))
        if num_procs_env > 0:
            default_procs = num_procs_env
        else:
            default_procs = mp.cpu_count()
    except (ValueError, TypeError):
        default_procs = mp.cpu_count()

    parser.add_argument("-n", "--num-procs", type=int, default=default_procs, help=f"Numero di worker (default: {default_procs} da .env o CPU)")
    parser.add_argument("--base-extranonce2", default=default_extranonce2, 
                        help=f"Base esadecimale per EXTRANONCE2 (default: {default_extranonce2})")
    return parser.parse_args()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger(__name__)
    log.info("Launcher avviato. Parametri e configurazione caricati correttamente.")
    
    from rpc import connect_rpc, test_rpc_connection, get_block_template
    from block_builder import is_segwit_tx
    from utils import calculate_target
    import os
    from dotenv import load_dotenv
    
    # Carica le variabili d'ambiente dal file .env
    load_dotenv()
    
    mp.set_start_method("spawn", force=True)
    args = _parse_args()
    
    test_rpc_connection()
    
    rpc_conn = connect_rpc()
    template = get_block_template(rpc_conn)
    
    if template:
        tot_tx = len(template["transactions"])
        witness_tx = sum(1 for tx in template["transactions"] if is_segwit_tx(tx["data"]))
        legacy_tx = tot_tx - witness_tx
        log.info(f"Transazioni nel template: totali = {tot_tx}  |  legacy = {legacy_tx}  |  segwit = {witness_tx}")
        log.info(f"Messaggio nella coinbase: {os.getenv('COINBASE_MESSAGE')}")
        
        blockchain_info = rpc_conn.getblockchaininfo()
        network = blockchain_info.get("chain", "")
        difficulty_factor = float(os.getenv('DIFFICULTY_FACTOR', 0.01))
        
        if network != "regtest":
            difficulty_factor = 1.0
            log.info(f"Rete {network} rilevata: DIFFICULTY_FACTOR impostato a 1.0")
        elif difficulty_factor < 0:
            log.warning("DIFFICULTY_FACTOR deve essere >= 0. Impostazione a 0.1")
            difficulty_factor = 0.1
        
        modified_target = calculate_target(template, difficulty_factor, network)
        log.info(f"Target modificato (difficoltà {difficulty_factor}): {modified_target}")
    
    log.info(f"Avvio mining - modalità {os.getenv('NONCE_MODE')}")
    
    print(f"\nAvvio mining con {args.num_procs} processi (base extranonce2={args.base_extranonce2})\n")
    launch(args.num_procs, args.base_extranonce2)
