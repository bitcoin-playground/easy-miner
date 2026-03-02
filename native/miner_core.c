/*
 * miner_core.c — loop SHA-256 ottimizzato per Bitcoin mining
 *
 * Sfrutta le istruzioni hardware SHA-256 del Cortex-A76 (RPi 5) tramite
 * OpenSSL EVP, che le attiva automaticamente quando il flag "sha2" è
 * presente in /proc/cpuinfo (OPENSSL_armcap=0xbd su questa macchina).
 *
 * Ottimizzazione midstate: i primi 64 byte dell'header sono fissi per
 * tutto il batch. Il loro stato SHA-256 viene pre-calcolato una volta
 * sola; per ogni nonce si aggiungono solo i 16 byte rimanenti (tail).
 *
 * Build: gcc -O3 -march=native -shared -fPIC -o miner_core.so miner_core.c -lssl -lcrypto
 */

#include <stdint.h>
#include <string.h>
#include <openssl/evp.h>

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
    const EVP_MD *sha256_md = EVP_sha256();

    /* ---- Pre-calcolo midstate sui primi 64 byte fissi dell'header ---- */
    EVP_MD_CTX *base_ctx = EVP_MD_CTX_new();
    if (!base_ctx) return -1;

    if (!EVP_DigestInit_ex(base_ctx, sha256_md, NULL) ||
        !EVP_DigestUpdate(base_ctx, header_76, 64)) {
        EVP_MD_CTX_free(base_ctx);
        return -1;
    }

    /*
     * tail[0..11] = header_76[64..75]  (merkle_tail 4B + timestamp 4B + bits 4B)
     * tail[12..15] = nonce in little-endian (aggiornato per ogni iterazione)
     */
    uint8_t tail[16];
    memcpy(tail, header_76 + 64, 12);

    /*
     * ctx1 : context riusato per il primo SHA-256 (riparte dal midstate ogni volta)
     * ctx2 : context riusato per il secondo SHA-256 (sempre reinizializzato fresh)
     * Entrambi pre-allocati fuori dal loop per evitare malloc/free ad ogni nonce.
     */
    EVP_MD_CTX *ctx1 = EVP_MD_CTX_new();
    EVP_MD_CTX *ctx2 = EVP_MD_CTX_new();
    if (!ctx1 || !ctx2) {
        EVP_MD_CTX_free(ctx1);
        EVP_MD_CTX_free(ctx2);
        EVP_MD_CTX_free(base_ctx);
        return -1;
    }

    /* Pre-inizializza ctx2 per il secondo SHA-256; verrà copiato da questa
     * base ad ogni iterazione analogamente alla tecnica midstate del primo hash. */
    EVP_MD_CTX *base_ctx2 = EVP_MD_CTX_new();
    if (!base_ctx2 ||
        !EVP_DigestInit_ex(base_ctx2, sha256_md, NULL)) {
        EVP_MD_CTX_free(ctx1);
        EVP_MD_CTX_free(ctx2);
        EVP_MD_CTX_free(base_ctx);
        EVP_MD_CTX_free(base_ctx2);
        return -1;
    }

    uint8_t digest1[32];
    uint8_t digest2[32];
    unsigned int len = 32;
    int64_t result = -1;

    for (uint32_t i = 0; i < batch_size; i++) {
        uint32_t n = start_nonce + i;   /* wrap mod 2^32 intenzionale */

        /* Nonce in little-endian negli ultimi 4 byte della tail */
        tail[12] = (uint8_t)(n);
        tail[13] = (uint8_t)(n >>  8);
        tail[14] = (uint8_t)(n >> 16);
        tail[15] = (uint8_t)(n >> 24);

        /* Primo SHA-256: riparte dal midstate e aggiunge i 16 byte di tail */
        EVP_MD_CTX_copy_ex(ctx1, base_ctx);
        EVP_DigestUpdate(ctx1, tail, 16);
        EVP_DigestFinal_ex(ctx1, digest1, &len);

        /* Secondo SHA-256: reinizializza da base_ctx2 (fresh init), poi tutto d'un fiato */
        EVP_MD_CTX_copy_ex(ctx2, base_ctx2);
        EVP_DigestUpdate(ctx2, digest1, 32);
        EVP_DigestFinal_ex(ctx2, digest2, &len);

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

    EVP_MD_CTX_free(ctx1);
    EVP_MD_CTX_free(ctx2);
    EVP_MD_CTX_free(base_ctx);
    EVP_MD_CTX_free(base_ctx2);
    return result;
}
