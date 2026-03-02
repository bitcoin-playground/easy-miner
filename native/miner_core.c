/*
 * miner_core.c — loop SHA-256 ottimizzato per Bitcoin mining
 *
 * Sfrutta le istruzioni hardware SHA-256 del Cortex-A76 (RPi 5) tramite
 * OpenSSL SHA256_CTX (API di basso livello), che le attiva automaticamente
 * quando il flag "sha2" è presente in /proc/cpuinfo.
 *
 * Vantaggi rispetto all'API EVP:
 *   - SHA256_CTX è ~108 byte vs ~400 byte di EVP_MD_CTX
 *   - Il copia-midstate per nonce è un struct copy 4× più piccolo
 *   - Nessuna gestione di reference counting o allocazioni dinamiche
 *   - SHA256_Init() è una semplice assegnazione di costanti, non una malloc
 *
 * Ottimizzazione midstate: i primi 64 byte dell'header sono fissi per
 * tutto il batch. Il loro stato SHA-256 viene pre-calcolato una volta
 * sola; per ogni nonce si aggiungono solo i 16 byte rimanenti (tail).
 *
 * Build: gcc -O3 -march=native -shared -fPIC -o miner_core.so miner_core.c -lssl -lcrypto
 */

#include <stdint.h>
#include <string.h>
#include <openssl/sha.h>

/*
 * find_nonce — cerca il primo nonce nell'intervallo
 *              [start_nonce, start_nonce + batch_size) (mod 2^32)
 *              tale che SHA256d(header_76 || nonce_le4) < target_be.
 *
 * Parametri:
 *   header_76   : puntatore a 76 byte (header Bitcoin senza nonce)
 *   target_be   : puntatore a 32 byte (target in big-endian)
 *   start_nonce : primo nonce da provare
 *   batch_size  : numero di nonce da provare
 *
 * Ritorna il nonce trovato (valore ≥ 0), oppure -1 se nessun nonce
 * valido è stato trovato nel range specificato.
 */
int64_t find_nonce(
    const uint8_t *header_76,
    const uint8_t *target_be,
    uint32_t       start_nonce,
    uint32_t       batch_size)
{
    /* ---- Pre-calcolo midstate sui primi 64 byte fissi dell'header ---- */
    SHA256_CTX base_ctx;
    SHA256_Init(&base_ctx);
    SHA256_Update(&base_ctx, header_76, 64);
    /* base_ctx ora contiene lo stato SHA-256 dopo i primi 64 byte.
     * Per ogni nonce lo copiamo con un struct assignment (~108 byte vs ~400 di EVP). */

    /*
     * tail[0..11] = header_76[64..75]  (merkle_tail 4B + timestamp 4B + bits 4B)
     * tail[12..15] = nonce in little-endian (aggiornato per ogni iterazione)
     */
    uint8_t tail[16];
    memcpy(tail, header_76 + 64, 12);

    uint8_t digest1[32];
    uint8_t digest2[32];
    int64_t result = -1;

    for (uint32_t i = 0; i < batch_size; i++) {
        uint32_t n = start_nonce + i;   /* wrap mod 2^32 intenzionale */

        /* Nonce in little-endian negli ultimi 4 byte della tail */
        tail[12] = (uint8_t)(n);
        tail[13] = (uint8_t)(n >>  8);
        tail[14] = (uint8_t)(n >> 16);
        tail[15] = (uint8_t)(n >> 24);

        /* Primo SHA-256: struct copy del midstate (~108 byte) + aggiunge i 16 byte di tail */
        SHA256_CTX ctx1 = base_ctx;
        SHA256_Update(&ctx1, tail, 16);
        SHA256_Final(digest1, &ctx1);

        /* Secondo SHA-256: input è digest1 (32 byte), un solo blocco SHA-256 */
        SHA256_CTX ctx2;
        SHA256_Init(&ctx2);
        SHA256_Update(&ctx2, digest1, 32);
        SHA256_Final(digest2, &ctx2);

        /*
         * Confronto equivalente a Python: digest2[::-1] < target_be
         *
         * digest2[31] è il byte meno significativo dell'output SHA-256 grezzo,
         * che corrisponde al byte più significativo del block hash Bitcoin
         * (Bitcoin visualizza gli hash con byte invertiti).
         * Confrontiamo quindi digest2[31-j] con target_be[j] per j=0..31.
         */
        int valid = 0;
        for (int j = 0; j < 32; j++) {
            uint8_t hb = digest2[31 - j];
            uint8_t tb = target_be[j];
            if (hb < tb) { valid = 1; break; }
            if (hb > tb) {            break; }
        }

        if (valid) {
            result = (int64_t)n;
            break;
        }
    }

    return result;
}
