from __future__ import annotations

import argparse
import importlib
import logging
import multiprocessing as mp
import os
import sys
import time

import config
import log_setup

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _extranonce2(base: str, idx: int) -> str:
    """Restituisce `base + idx` in esadecimale, mantenendo la stessa larghezza."""
    return f"{int(base, 16) + idx:0{len(base)}x}"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _worker(idx: int, base_ex2: str, q: mp.Queue) -> None:
    """Avvia un processo di mining e invia eventi strutturati al supervisore."""
    try:
        os.sched_setaffinity(0, {idx})
    except (AttributeError, OSError):
        pass

    # I worker inviano eventi strutturati via queue; i log verbosi vengono soppressi
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    main = importlib.import_module("main")
    try:
        main.main(
            event_queue=q,
            worker_idx=idx,
            extranonce2=_extranonce2(base_ex2, idx),
        )
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Supervisore
# ---------------------------------------------------------------------------

def _clear_lines(n: int) -> None:
    for _ in range(n):
        sys.stdout.write("\033[F\033[K")
    sys.stdout.flush()


def _aggregate(q: mp.Queue, n: int) -> str:
    """
    Riceve eventi strutturati dai worker e aggiorna il dashboard.
    Ritorna "restart" quando un blocco viene trovato e inviato.
    """
    rates:    list[float] = [0.0] * n
    attempts: list[int]   = [0]   * n
    block_hash:   str | None  = None
    winner_idx:   int | None  = None
    winner_rate:  float | None = None

    t_start     = time.time()
    last_print  = 0.0
    lines_printed = 0

    while True:
        try:
            tag, idx, val = q.get(timeout=0.1)

            if tag == "status":
                rates[idx]    = val["rate"]
                attempts[idx] = val["attempts"]
            elif tag == "found":
                winner_idx  = idx
                winner_rate = val.get("rate") if val else None
            elif tag == "hash":
                block_hash = val
            elif tag == "submit":
                _clear_lines(lines_printed)
                elapsed    = time.time() - t_start
                total_att  = sum(attempts)
                avg_rate_k = total_att / elapsed / 1000 if elapsed else 0.0
                print("=" * 78)
                print("[✓] BLOCCO TROVATO E INVIATO")
                print(f"  • Hash: {block_hash or 'N/D'}")
                if winner_idx is not None:
                    print(f"  • Worker: {winner_idx}")
                if winner_rate is not None:
                    print(f"  • Hashrate worker: {winner_rate:.2f} kH/s")
                print(f"  • Hashrate medio totale: {avg_rate_k:,.2f} kH/s")
                print(f"  • Tentativi totali: {total_att:,}")
                print("=" * 78)
                return "restart"

        except Exception:
            pass  # coda vuota

        now = time.time()
        if now - last_print >= 1.0:
            if lines_printed > 0:
                _clear_lines(lines_printed)

            tot_rate = sum(rates)
            tot_att  = sum(attempts)
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

            lines = [
                f"{ts} | STATO MINING",
                "=" * 40,
                f"Totale: {tot_rate:,.2f} kH/s | Tentativi: {tot_att:,}",
                "-" * 40,
            ]
            for i in range(n):
                lines.append(f"Worker {i:<2}: {rates[i]:.2f} kH/s  | Tentativi: {attempts[i]:,}")

            print("\n".join(lines), flush=True)
            lines_printed = len(lines)
            last_print    = now


# ---------------------------------------------------------------------------
# Ciclo avvio/riavvio
# ---------------------------------------------------------------------------

def launch(n: int, base_ex2: str) -> None:
    log.info("Extranonce2 per processo:")
    for i in range(n):
        log.info("  • Processo %d: extranonce2=%s", i, _extranonce2(base_ex2, i))

    while True:
        q       = mp.Queue()
        workers = [
            mp.Process(target=_worker, args=(i, base_ex2, q), daemon=True)
            for i in range(n)
        ]
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


# ---------------------------------------------------------------------------
# Entry-point CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser("Launcher multiprocesso per miner main.py")
    parser.add_argument(
        "-n", "--num-procs",
        type=int, default=config.NUM_PROCESSORS,
        help=f"Numero di worker (default: {config.NUM_PROCESSORS})",
    )
    parser.add_argument(
        "--base-extranonce2",
        default=config.EXTRANONCE2,
        help=f"Base esadecimale per EXTRANONCE2 (default: {config.EXTRANONCE2})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    log_setup.configure()

    mp.set_start_method("spawn", force=True)
    args = _parse_args()

    from rpc import test_rpc_connection
    test_rpc_connection()

    print(f"\nAvvio mining con {args.num_procs} processi (base extranonce2={args.base_extranonce2})\n")
    launch(args.num_procs, args.base_extranonce2)
