"""Configurazione centralizzata: carica e valida tutte le variabili d'ambiente."""
import multiprocessing as mp
import os

from dotenv import load_dotenv

load_dotenv()

# RPC
RPC_USER     = os.getenv("RPC_USER", "user")
RPC_PASSWORD = os.getenv("RPC_PASSWORD", "password")
RPC_HOST     = os.getenv("RPC_HOST", "127.0.0.1")
RPC_PORT     = int(os.getenv("RPC_PORT", "8332"))

# Wallet
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")

# Mining
DIFFICULTY_FACTOR         = float(os.getenv("DIFFICULTY_FACTOR", "1.0"))
NONCE_MODE                = os.getenv("NONCE_MODE", "incremental")
TIMESTAMP_UPDATE_INTERVAL = int(os.getenv("TIMESTAMP_UPDATE_INTERVAL", "30"))
BATCH                     = int(os.getenv("BATCH", "10000"))
COINBASE_MESSAGE          = os.getenv("COINBASE_MESSAGE", "/py-miner/")
EXTRANONCE1               = os.getenv("EXTRANONCE1", "1234567890abcdef")
EXTRANONCE2               = os.getenv("EXTRANONCE2", "12341234")

# Worker
_n             = int(os.getenv("NUM_PROCESSORS", "0"))
NUM_PROCESSORS = _n if _n > 0 else mp.cpu_count()

# Watchdog
CHECK_INTERVAL = 20
