"""
Configurazione centralizzata del logging con formattazione colorata.

Uso:
    import log_setup
    log_setup.configure()          # INFO di default
    log_setup.configure(debug=True) # abilita DEBUG
"""

import logging
import sys


# ---------------------------------------------------------------------------
# Colori ANSI
# ---------------------------------------------------------------------------
_RESET  = "\033[0m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_RED_B  = "\033[1;31m"
_GREEN  = "\033[32m"
_GREEN_B = "\033[1;32m"


class _ColorFormatter(logging.Formatter):
    """
    Formatter con colori per terminale.
    - Timestamp : grigio (dim)
    - Livello   : colorato per gravità
    - Modulo    : grigio, larghezza fissa
    - Messaggio : colorato per gravità; parole chiave speciali risaltano
    """

    _LEVEL_COLOR = {
        logging.DEBUG:    _DIM,
        logging.INFO:     "",           # default
        logging.WARNING:  _YELLOW,
        logging.ERROR:    _RED,
        logging.CRITICAL: _RED_B,
    }

    _LEVEL_LABEL = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO ",
        logging.WARNING:  "WARN ",
        logging.ERROR:    "ERR  ",
        logging.CRITICAL: "CRIT ",
    }

    # Parole chiave nei messaggi INFO che meritano risalto verde
    _GREEN_KEYWORDS = ("accettato", "trovato", "riuscita", "OK", "caricata")

    def format(self, record: logging.LogRecord) -> str:
        ts    = self.formatTime(record, "%H:%M:%S")
        level = self._LEVEL_LABEL.get(record.levelno, record.levelname[:5])
        color = self._LEVEL_COLOR.get(record.levelno, "")

        # Solo l'ultimo componente del nome del modulo, larghezza fissa 12
        name = record.name.split(".")[-1][:12]

        msg = record.getMessage()

        # Eccezione allegata (mantiene tutte le info)
        exc = ""
        if record.exc_info:
            exc = "\n" + self.formatException(record.exc_info)

        # Evidenzia in verde brillante messaggi INFO con parole chiave positive
        if record.levelno == logging.INFO and any(k in msg for k in self._GREEN_KEYWORDS):
            msg_colored = f"{_GREEN_B}{msg}{_RESET}"
        elif color:
            msg_colored = f"{color}{msg}{_RESET}"
        else:
            msg_colored = msg

        line = (
            f"{_DIM}{ts}{_RESET}"
            f"  {color}{level}{_RESET}"
            f"  {_DIM}{name:<12}{_RESET}"
            f"  {msg_colored}"
            f"{exc}"
        )
        return line


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

def configure(debug: bool = False) -> None:
    """
    Configura il logging root con il formatter colorato.
    Chiamare una sola volta all'avvio del processo (main.py / launcher.py).
    """
    level   = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ColorFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    # Rimuove handler preesistenti per evitare duplicati
    root.handlers.clear()
    root.addHandler(handler)
