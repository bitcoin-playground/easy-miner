# Bitcoin Mining (GetBlockTemplate)

Questo progetto implementa un sistema completo di mining Bitcoin educativo che utilizza il protocollo **GetBlockTemplate** per interagire con un nodo Bitcoin Core tramite chiamate RPC (Remote Procedure Call). Il programma è progettato specificamente per scopi didattici e di apprendimento, offrendo una comprensione approfondita dei meccanismi interni del mining di Bitcoin, dalla costruzione dei blocchi al processo di Proof-of-Work. Il miner è ottimizzato per architetture ARM (ARM64 con estensioni Crypto), mantenendo al contempo compatibilità x86_64.

## Caratteristiche Principali

- **C extension SHA-256 ottimizzata** con loop in C via OpenSSL EVP, ottimizzazione midstate e fallback automatico al loop Python
- **Istruzioni hardware SHA-256** sfruttate automaticamente da OpenSSL su CPU ARM64 (ARMv8 Crypto) e x86_64 (Intel/AMD SHA-NI); con fallback a SHA256 software ottimizzato su CPU più vecchi
- **Gestione degli extranonce compatibile con Stratum v1** per la comunicazione mining con supporto per extranonce1 ed extranonce2 configurabili
- **Supporto multi-processo** per il mining parallelo con distribuzione automatica degli extranonce
- **Gestione avanzata delle transazioni** SegWit e legacy
- **Costruzione dinamica della transazione coinbase** con extranonce personalizzabili
- **Calcolo ottimizzato del Merkle Root** per blocchi con molte transazioni
- **Sistema di watchdog** per il rilevamento di nuovi blocchi sulla rete
- **Configurazione flessibile della difficoltà** per ambienti di test
- **Logging colorato** con formattazione ANSI e metriche di performance in tempo reale

## Compilazione Rapida `miner_core.so`

```bash
cd native
make clean && make
cd ..
```

Verifica:
```bash
ls -lh native/miner_core.so
```

## Funzionamento Teorico del Mining di Bitcoin

Il mining di Bitcoin è il processo attraverso il quale nuove transazioni vengono verificate e aggiunte a un registro pubblico distribuito chiamato blockchain. È anche il meccanismo attraverso il quale vengono creati nuovi bitcoin.

I miner competono per risolvere un complesso problema matematico basato su una funzione di hash crittografica (SHA-256 nel caso di Bitcoin). Il primo miner che trova una soluzione valida, chiamata "proof-of-work" (PoW), ha il diritto di aggiungere un nuovo blocco di transazioni alla blockchain e viene ricompensato con una certa quantità di bitcoin (la "ricompensa del blocco") più le commissioni di transazione incluse nel blocco.

Il processo di mining coinvolge i seguenti passaggi chiave:

1.  **Raccolta delle Transazioni**: I miner raccolgono le transazioni in sospeso dalla rete Bitcoin.
2.  **Costruzione del Blocco Candidato**: I miner creano un "blocco candidato" che include:
    *   Un riferimento (hash) al blocco precedente nella blockchain.
    *   Un insieme di transazioni valide (inclusa una speciale transazione "coinbase" che assegna la ricompensa del blocco al miner).
    *   Un timestamp.
    *   Un valore di "difficoltà target" che determina quanto deve essere difficile trovare la soluzione PoW.
    *   Un campo "nonce" (number used once), un numero che i miner modificano ripetutamente.
3.  **Ricerca del Nonce (Proof-of-Work)**: Questo è il cuore del processo di mining. I miner modificano il valore del nonce nell'header del blocco candidato e calcolano il doppio hash SHA-256 dell'header. L'obiettivo è trovare un nonce tale per cui l'hash risultante sia inferiore al target di difficoltà. Poiché le funzioni di hash sono imprevedibili, questo processo richiede una grande quantità di tentativi.
4.  **Validazione e Propagazione del Blocco**: Una volta che un miner trova un nonce valido, trasmette il blocco alla rete Bitcoin. Gli altri nodi verificano la validità del blocco (correttezza delle transazioni, validità del PoW, ecc.). Se il blocco è valido, viene aggiunto alla loro copia della blockchain e il miner riceve la ricompensa.
5.  **Aggiustamento della Difficoltà**: La difficoltà di mining viene aggiustata circa ogni 2016 blocchi (circa due settimane) per garantire che, in media, venga trovato un nuovo blocco ogni 10 minuti, indipendentemente dalla potenza di calcolo totale della rete.

## Architettura del Sistema

Questo sistema di mining educativo è strutturato in modo modulare per facilitare la comprensione dei diversi aspetti del mining Bitcoin. Il programma simula un ambiente di mining reale, interagendo con un nodo Bitcoin Core locale (tipicamente in modalità `regtest` o `testnet` per scopi di sviluppo e test).

### Struttura

Il progetto è organizzato in moduli specializzati, ognuno responsabile di un aspetto specifico del processo di mining:

- **`launcher.py`**: Orchestratore principale e gestore multi-processo
- **`main.py`**: Logica core del mining per singolo worker
- **`miner.py`**: Implementazione dell'algoritmo Proof-of-Work con C extension
- **`block_builder.py`**: Costruzione e serializzazione dei blocchi Bitcoin
- **`rpc.py`**: Interfaccia di comunicazione con Bitcoin Core
- **`utils.py`**: Funzioni crittografiche e di utilità
- **`config.py`**: Configurazione centralizzata tramite variabili d'ambiente
- **`log_setup.py`**: Formatter di logging colorato per terminale
- **`native/miner_core.c`**: Loop SHA-256 in C con OpenSSL EVP e ottimizzazione midstate
- **`native/Makefile`**: Build della C extension (`make` nella cartella `native/`)
- **`.env`**: File di configurazione delle variabili d'ambiente

### Flusso di Esecuzione

L'esecuzione del programma segue un pattern coordinato che rispecchia il funzionamento di un mining pool reale:

1.  **`launcher.py` (Punto di Ingresso e Supervisore)**:
    *   È lo script principale da eseguire per avviare il miner.
    *   Utilizza il modulo `multiprocessing` per creare e gestire un pool di processi worker.
    *   Ogni worker esegue una propria istanza del processo di mining (`main.main()`), ricevendo un `extranonce2` univoco (base + indice worker) per coprire spazi di nonce distinti.
    *   I worker comunicano con il supervisore tramite una coda `mp.Queue` con eventi strutturati: `status` (hashrate e tentativi), `found` (blocco trovato), `hash` (hash del blocco), `submit` (blocco inviato).
    *   La funzione `_aggregate` riceve questi eventi e aggiorna in tempo reale un dashboard testuale con hashrate per worker e totale aggregato.
    *   Quando un worker trova e invia un blocco con successo, tutti i worker vengono terminati e riavviati con un nuovo template di blocco.

2.  **`main.py` (Logica Principale del Singolo Worker)**:
    *   Contiene la logica di un singolo processo di mining.
    *   **Connessione RPC**: Stabilisce una connessione RPC con il nodo Bitcoin Core (`connect_rpc` da `rpc.py`). Recupera rete e `scriptPubKey` una volta sola prima del loop.
    *   **Ottenimento del Template del Blocco**: Richiede un template di blocco al nodo (`get_block_template` da `rpc.py`).
    *   **Gestione Dati Witness**: Assicura che tutte le transazioni nel template abbiano i dati witness completi tramite una singola richiesta batch RPC (`ensure_witness_data` da `rpc.py`).
    *   **Costruzione della Transazione Coinbase**: Crea la transazione coinbase (`build_coinbase_transaction` da `block_builder.py`). Questa transazione speciale:
        *   Include la ricompensa del blocco e le commissioni.
        *   Assegna la ricompensa all'indirizzo del miner specificato nel file `.env`.
        *   Include l'altezza del blocco (BIP34).
        *   Può includere un messaggio personalizzato (`COINBASE_MESSAGE` dal file `.env`).
        *   Incorpora `EXTRANONCE1` (fisso) e `EXTRANONCE2` (variabile per worker, fornito da `launcher.py`).
    *   **Modifica del Target di Difficoltà**: La funzione `calculate_target` permette di aggiustare la difficoltà di mining. Su `regtest`, può essere impostato un `DIFFICULTY_FACTOR` nel file `.env`. Su `testnet` o `mainnet`, il fattore è forzato a 1.
    *   **Calcolo del Merkle Root**: Calcola il Merkle root di tutte le transazioni nel blocco (`calculate_merkle_root` da `block_builder.py`).
    *   **Costruzione dell'Header del Blocco**: Assembla l'header del blocco (`build_block_header` da `block_builder.py`).
    *   **Watchdog per Nuovi Blocchi**: Avvia un thread `watchdog_bestblock` che controlla periodicamente se un nuovo blocco è stato trovato sulla rete. Se sì, imposta `stop_event` per interrompere il miner entro il prossimo batch.
    *   **Processo di Mining**: Chiama `mine_block` da `miner.py`.
    *   **Serializzazione e Invio**: Serializza il blocco (`serialize_block`) e lo invia al nodo (`submit_block`).

3.  **`miner.py` (Algoritmo di Mining - Proof-of-Work)**:
    *   Contiene la funzione `mine_block` che implementa l'algoritmo di ricerca del nonce.
    *   All'avvio del modulo tenta di caricare `native/miner_core.so` tramite `ctypes`. Se non disponibile, usa automaticamente il fallback Python.
    *   **C extension (`native/miner_core.c`)**: Implementa il loop SHA-256 in C con OpenSSL EVP. Sfrutta automaticamente le istruzioni hardware SHA-256 (ARMv8 Crypto Extensions / Intel SHA-NI) quando disponibili. Ottimizzazione midstate: i primi 64 byte dell'header sono fissi per tutto il batch; il loro stato SHA-256 viene pre-calcolato una volta sola. I 4 contesti EVP sono pre-allocati fuori dal loop (zero malloc per iterazione).
    *   **Fallback Python**: Se la C extension non è disponibile, usa lo stesso algoritmo midstate implementato in Python con `hashlib`.
    *   **Iterazione sul Nonce**: Le modalità di scelta del nonce (`NONCE_MODE`) possono essere:
        *   `incremental`: Il nonce viene incrementato linearmente.
        *   `random`: Il nonce iniziale è casuale.
        *   `mixed`: Alias per `random` (nonce iniziale casuale, poi incrementato).
    *   **Aggiornamento Timestamp**: Se `TIMESTAMP_UPDATE_INTERVAL` è impostato, il timestamp nell'header viene aggiornato periodicamente per ampliare lo spazio di ricerca.
    *   **Logging e Callback**: Ogni ~2 secondi stampa hashrate (con separatori migliaia) e invoca `status_callback` per inviare metriche al supervisore tramite queue.

4.  **`block_builder.py` (Costruzione dei Blocchi)**:
    *   Fornisce funzioni specializzate per costruire le varie parti di un blocco Bitcoin:
        *   `tx_encode_coinbase_height`: Codifica l'altezza del blocco per la transazione coinbase (BIP34).
        *   `is_segwit_tx`: Verifica se una transazione è in formato SegWit.
        *   `build_coinbase_transaction`: Costruisce la transazione coinbase completa (versione fissa a 2 per compatibilità BIP68/SegWit).
        *   `calculate_merkle_root`: Calcola il Merkle root delle transazioni.
        *   `build_block_header`: Assembla l'header del blocco.
        *   `serialize_block`: Serializza l'intero blocco (header + transazioni) nel formato di rete.

5.  **`utils.py` (Funzioni di Utilità Comuni)**:
    *   Modulo centralizzato contenente funzioni di utilità condivise:
        *   `double_sha256`: Calcola il doppio hash SHA-256.
        *   `encode_varint` / `decode_varint`: Codifica/decodifica numeri interi nel formato VarInt di Bitcoin.
        *   `decode_nbits`: Converte il campo `bits` (difficoltà compatta) nel target di difficoltà a 256 bit.
        *   `calculate_target`: Calcola e modifica il target di difficoltà in base alla rete e al fattore configurato.
        *   `watchdog_bestblock`: Thread che monitora la rete e interrompe il mining se appare un nuovo blocco.

6.  **`rpc.py` (Interazione con Bitcoin Core)**:
    *   Contiene funzioni per interagire con il nodo Bitcoin Core tramite RPC:
        *   `connect_rpc`: Stabilisce la connessione.
        *   `test_rpc_connection`: Verifica la connessione.
        *   `get_best_block_hash`: Ottiene l'hash del blocco più recente.
        *   `get_block_template`: Richiede un template di blocco.
        *   `ensure_witness_data`: Recupera i dati witness mancanti con una singola richiesta batch RPC.
        *   `submit_block`: Invia un blocco minato al nodo.

7.  **`config.py` (Configurazione Centralizzata)**:
    *   Unico punto di caricamento delle variabili d'ambiente tramite `python-dotenv`.
    *   Espone costanti tipizzate usate da tutti gli altri moduli.

8.  **`log_setup.py` (Logging Colorato)**:
    *   `_ColorFormatter`: Formatter ANSI con colori per livello (grigio per DEBUG, giallo per WARNING, rosso per ERROR). I messaggi INFO con parole chiave positive (es. "trovato", "accettato") vengono evidenziati in verde.
    *   `configure(debug=False)`: Configura il logger root; chiamare una sola volta all'avvio del processo.

9.  **`.env` (Configurazione)**:
    *   Contiene i parametri di configurazione del miner tramite variabili d'ambiente:
        *   Credenziali RPC (`RPC_USER`, `RPC_PASSWORD`, `RPC_HOST`, `RPC_PORT`).
        *   Indirizzo del wallet del miner (`WALLET_ADDRESS`) a cui inviare la ricompensa.
        *   `DIFFICULTY_FACTOR`: Per regolare la difficoltà in `regtest`.
        *   `NONCE_MODE`: Strategia di ricerca del nonce (`incremental`, `random`, `mixed`).
        *   `TIMESTAMP_UPDATE_INTERVAL`: Frequenza di aggiornamento del timestamp durante il mining (secondi, 0 per disabilitare).
        *   `BATCH`: Numero di nonce testati per chiamata alla C extension (default: 50000).
        *   `COINBASE_MESSAGE`: Messaggio personalizzato da includere nella transazione coinbase.
        *   `EXTRANONCE1`: Parte fissa dell'extranonce (identica per tutti i worker).
        *   `EXTRANONCE2`: Base esadecimale per l'extranonce variabile (ogni worker usa `base + indice`).
        *   `NUM_PROCESSORS`: Numero di processi worker da avviare (default: numero di core CPU).

## Come si Usa

1.  **Prerequisiti**:
    *   Python 3.11+
    *   Un nodo Bitcoin Core in esecuzione (modalità `regtest`, `testnet` o `mainnet`).
    *   Per la C extension (fortemente consigliata):
        *   **GCC** — compilatore C (pacchetto `gcc`)
        *   **OpenSSL development headers** — include e librerie per `<openssl/sha.h>` (pacchetto `libssl-dev`)
        *   **make** — per eseguire il Makefile (pacchetto `make`)

    Su sistemi Debian/Ubuntu:
    ```bash
    sudo apt update
    sudo apt install gcc libssl-dev make
    ```

    Verifica che OpenSSL 3.x sia disponibile:
    ```bash
    openssl version   # atteso: OpenSSL 3.x.x ...
    ```

2.  **Compilare la C extension** (consigliato per prestazioni ottimali):

    Il file `native/miner_core.c` implementa il loop SHA-256 in C usando l'API `SHA256_CTX` di OpenSSL. Il codice è **portabile**: compila e funziona su qualsiasi architettura Linux con GCC e OpenSSL. Le prestazioni variano in base alle istruzioni hardware disponibili sul CPU:

    | Architettura | CPU di esempio | Accelerazione hardware |
    |---|---|---|
    | ARM64 | Raspberry Pi 4/5, AWS Graviton, Apple M1 (Linux) | ARMv8 Crypto (`SHA256H/H2`) — attiva se `sha2` in `/proc/cpuinfo` |
    | x86_64 moderno | Intel Ice Lake+, AMD Zen+ | SHA-NI (`SHA256RNDS2`) — attiva se `sha_ni` in `/proc/cpuinfo` |
    | x86_64 generico | Qualsiasi Intel/AMD anche senza SHA-NI | SHA256 software con AVX2/SSE4 — ~3-4× più lento di SHA-NI, ma sempre ~5× più veloce del loop Python |

    In tutti i casi la compilazione è identica. Per verificare quali istruzioni sono disponibili sul proprio CPU:
    ```bash
    grep -o 'sha2\|sha_ni' /proc/cpuinfo | sort -u
    ```

    ```bash
    cd native
    make
    ```

    Il Makefile compila con `-O3 -march=native` (ottimizzazioni massime per la CPU corrente) e produce `native/miner_core.so`. Per una compilazione pulita:
    ```bash
    make clean && make
    ```

    Verifica che la libreria sia stata creata:
    ```bash
    ls -lh native/miner_core.so
    # atteso: file .so di circa 60-70 KB
    ```

    All'avvio del miner, il log conferma quale backend è attivo:
    ```
    INFO  miner         C extension caricata: .../native/miner_core.so (SHA-256 hardware attivo)
    ```
    Se la `.so` non è presente o non è compatibile, il miner avvisa e usa il fallback Python senza interrompere l'esecuzione:
    ```
    WARN  miner         C extension non trovata (...). Esegui 'make' in native/ per abilitare
                        il loop SHA-256 ottimizzato. Uso fallback Python.
    ```

    **Troubleshooting compilazione:**

    | Errore | Causa | Soluzione |
    |---|---|---|
    | `fatal error: openssl/sha.h: No such file or directory` | `libssl-dev` non installato | `sudo apt install libssl-dev` |
    | `gcc: command not found` | GCC non installato | `sudo apt install gcc` |
    | `make: command not found` | make non installato | `sudo apt install make` |
    | Warning `deprecated since OpenSSL 3.0` | Normale, non è un errore | Ignorare, il codice funziona |

3.  **Creare e usare un ambiente virtuale (venv)**:
    ```bash
    # Nella radice del progetto
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```
    Per uscire: `deactivate`

4.  **Configurazione (file `.env`)**:
    *   Copia `.env_example` in `.env` e compilalo con i tuoi valori.
    *   Imposta `RPC_USER`, `RPC_PASSWORD`, `RPC_HOST` e `RPC_PORT` in modo che corrispondano alla configurazione RPC del tuo nodo Bitcoin Core (file `bitcoin.conf`).
    *   Imposta `WALLET_ADDRESS` con un indirizzo valido del tuo wallet Bitcoin.
    *   (Opzionale) Modifica `DIFFICULTY_FACTOR` se stai usando `regtest` per rendere il mining più facile (es. `0.01`) o più difficile (es. `10`). Su `testnet`/`mainnet` è ignorato.
    *   (Opzionale) Scegli `NONCE_MODE` tra `incremental`, `random`, o `mixed`.
    *   (Opzionale) Imposta `TIMESTAMP_UPDATE_INTERVAL` in secondi (`0` per disabilitare).
    *   (Opzionale) Regola `BATCH` in base alla CPU: valori più alti riducono l'overhead di chiamata ma aumentano la latenza di risposta allo `stop_event`.

5.  **Avvio del Miner**:
    ```bash
    python launcher.py
    ```
    Argomenti disponibili:
    *   `-n` / `--num-procs`: Numero di processi worker (default: `NUM_PROCESSORS` dal `.env`).
    *   `--base-extranonce2`: Valore esadecimale di base per `extranonce2` (default: `EXTRANONCE2` dal `.env`).

    Esempio con 4 worker:
    ```bash
    python launcher.py -n 4
    ```

    Per avviare un singolo processo senza launcher:
    ```bash
    python main.py
    ```

6.  **Monitoraggio**:
    *   Il `launcher.py` aggiorna ogni secondo un dashboard con hashrate per worker e totale aggregato.
    *   Quando un worker trova un blocco, vengono visualizzati hash, worker vincitore, hashrate e tentativi totali.
    *   I log di ogni worker usano colori ANSI per distinguere livelli e messaggi rilevanti.
    *   All'avvio, il log indica quale backend è attivo: `C extension` (SHA-256 hardware) o `Python fallback`.

7.  **Interruzione**:
    *   Premere `Ctrl+C` nel terminale dove `launcher.py` è in esecuzione.

## Licenza

Questo progetto è distribuito con la licenza MIT. Consulta il file `LICENSE` per maggiori dettagli.
