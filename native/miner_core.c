/*
 * miner_core.c — loop SHA-256 dual-stream ottimizzato per Bitcoin mining
 *
 * Usa SHA256_Transform (compressione diretta a blocchi) invece dell'API
 * SHA256_Update/Final, eliminando l'overhead di gestione buffer e padding.
 *
 * Ottimizzazioni:
 *
 *  1. Midstate pre-calcolato (32 byte): copiato una volta per nonce
 *     al posto dell'intero SHA256_CTX (112 byte).
 *
 *  2. Blocchi tail e block2 con padding pre-calcolati per batch:
 *     cambiano solo i 4 byte del nonce per ogni iterazione.
 *
 *  3. Confronto hash diretto su parole a 32 bit con bswap32
 *     (8 iterazioni invece di 32 confronti byte-by-byte).
 *
 *  4. Dual-stream (2 nonce per iterazione):
 *     due SHA256_CTX indipendenti (ctx1, ctx2) con buffer separati
 *     (padded_tail1/2, padded_block2_1/2). L'assenza di dipendenze tra
 *     le due catene consente alla CPU (out-of-order, pipeline crypto)
 *     di sovrapporre le due sequenze di SHA256_Transform, sfruttando
 *     le due unità crittografiche hardware presenti su ARM64 e x86_64.
 *
 * Portabilità: codice C puro, nessuna istruzione assembly specifica.
 *   - ARM64: OpenSSL usa SHA256H/SHA256H2 (ARMv8 Crypto Extensions)
 *   - x86_64 SHA-NI: OpenSSL usa SHA256RNDS2
 *   - x86_64 generico: OpenSSL usa AVX2/SSE4 software
 *
 * Struttura SHA256_CTX (OpenSSL): h[8] = 32B | Nl,Nh = 8B | data[16] = 64B
 *                                  | num,md_len = 8B → totale 112B.
 * SHA256_Transform usa SOLO h[0..7] → copia di 32B è sufficiente.
 *
 * Build: gcc -O3 -march=native -shared -fPIC -Wno-deprecated-declarations
 *            -o miner_core.so miner_core.c -lssl -lcrypto
 */

#include <stdint.h>
#include <string.h>
#include <openssl/sha.h>

/* Costanti IV di SHA-256 (FIPS 180-4). */
static const uint32_t SHA256_IV[8] = {
    0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
    0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u,
};

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
 * Ritorna il nonce trovato (≥ 0), oppure -1 se nessun nonce valido.
 */
int64_t find_nonce(
    const uint8_t *header_76,
    const uint8_t *target_be,
    uint32_t       start_nonce,
    uint32_t       batch_size)
{
    /* ---- Pre-calcolo midstate sui primi 64 byte fissi dell'header ---- */
    SHA256_CTX ctx1, ctx2;
    SHA256_Init(&ctx1);
    SHA256_Update(&ctx1, header_76, 64);

    uint32_t midstate[8];
    memcpy(midstate, ctx1.h, 32);

    /*
     * Template del blocco tail con padding (64 byte), pre-calcolato per il batch.
     *
     * Layout (dopo i 64 byte di midstate, input totale = 80 byte):
     *   [0..11]  = header_76[64..75]  (merkle_tail 4B + timestamp 4B + bits 4B)
     *   [12..15] = nonce little-endian  ← aggiornato per ogni iterazione
     *   [16]     = 0x80               (marker padding SHA-256)
     *   [17..55] = 0x00
     *   [56..63] = 0x0000000000000280 (640 bit = 80 byte, big-endian)
     */
    uint8_t tail_tmpl[64];
    memset(tail_tmpl, 0, sizeof(tail_tmpl));
    memcpy(tail_tmpl, header_76 + 64, 12);
    tail_tmpl[16] = 0x80;
    tail_tmpl[62] = 0x02;
    tail_tmpl[63] = 0x80;

    /* Due copie del tail — una per stream, nessuna dipendenza di memoria. */
    uint8_t padded_tail1[64], padded_tail2[64];
    memcpy(padded_tail1, tail_tmpl, 64);
    memcpy(padded_tail2, tail_tmpl, 64);

    /*
     * Template per il secondo SHA-256 con padding (64 byte).
     *
     * Input del secondo SHA-256 = digest1 (32 byte), un solo blocco:
     *   [0..31]  = digest1            ← aggiornato per ogni iterazione
     *   [32]     = 0x80
     *   [33..55] = 0x00
     *   [56..63] = 0x0000000000000100 (256 bit = 32 byte, big-endian)
     */
    uint8_t block2_tmpl[64];
    memset(block2_tmpl, 0, sizeof(block2_tmpl));
    block2_tmpl[32] = 0x80;
    block2_tmpl[62] = 0x01;
    block2_tmpl[63] = 0x00;

    /* Due copie del blocco per il secondo SHA-256. */
    uint8_t padded_block2_1[64], padded_block2_2[64];
    memcpy(padded_block2_1, block2_tmpl, 64);
    memcpy(padded_block2_2, block2_tmpl, 64);

    /* Target pre-decodificato come 8 parole big-endian per il confronto. */
    uint32_t target_words[8];
    for (int j = 0; j < 8; j++) {
        target_words[j] = ((uint32_t)target_be[j*4+0] << 24) |
                          ((uint32_t)target_be[j*4+1] << 16) |
                          ((uint32_t)target_be[j*4+2] <<  8) |
                          ((uint32_t)target_be[j*4+3]);
    }

    int64_t  result = -1;
    uint32_t i;

    /* ================================================================
     * Loop principale — dual-stream: 2 nonce per iterazione.
     *
     * ctx1 processa nonce n, ctx2 processa nonce n+1.
     * Buffer tail e block2 separati → nessuna dipendenza di memoria
     * tra le due catene → la CPU può sovrapporre le SHA256_Transform.
     * ================================================================ */
    for (i = 0; i + 1 < batch_size; i += 2) {
        uint32_t n1 = start_nonce + i;
        uint32_t n2 = n1 + 1;   /* wrap mod 2^32 intenzionale */

        /* Scrivi i nonce nei rispettivi tail (little-endian). */
        padded_tail1[12] = (uint8_t)(n1);
        padded_tail1[13] = (uint8_t)(n1 >>  8);
        padded_tail1[14] = (uint8_t)(n1 >> 16);
        padded_tail1[15] = (uint8_t)(n1 >> 24);

        padded_tail2[12] = (uint8_t)(n2);
        padded_tail2[13] = (uint8_t)(n2 >>  8);
        padded_tail2[14] = (uint8_t)(n2 >> 16);
        padded_tail2[15] = (uint8_t)(n2 >> 24);

        /* Primo SHA-256: ripristina midstate per entrambi i contesti.
         * Le due memcpy e le due SHA256_Transform sono indipendenti:
         * la CPU le sovrappone sfruttando le due pipeline crittografiche. */
        memcpy(ctx1.h, midstate, 32);
        memcpy(ctx2.h, midstate, 32);
        SHA256_Transform(&ctx1, padded_tail1);
        SHA256_Transform(&ctx2, padded_tail2);

        /* Estrai digest1 in padded_block2_1 (da ctx1.h). */
        for (int j = 0; j < 8; j++) {
            uint32_t w = ctx1.h[j];
            padded_block2_1[j*4+0] = (uint8_t)(w >> 24);
            padded_block2_1[j*4+1] = (uint8_t)(w >> 16);
            padded_block2_1[j*4+2] = (uint8_t)(w >>  8);
            padded_block2_1[j*4+3] = (uint8_t)(w);
        }

        /* Estrai digest1 in padded_block2_2 (da ctx2.h). */
        for (int j = 0; j < 8; j++) {
            uint32_t w = ctx2.h[j];
            padded_block2_2[j*4+0] = (uint8_t)(w >> 24);
            padded_block2_2[j*4+1] = (uint8_t)(w >> 16);
            padded_block2_2[j*4+2] = (uint8_t)(w >>  8);
            padded_block2_2[j*4+3] = (uint8_t)(w);
        }

        /* Secondo SHA-256: reinizializza h[] con l'IV per entrambi,
         * poi una compressione su digest1 + padding. */
        memcpy(ctx1.h, SHA256_IV, 32);
        memcpy(ctx2.h, SHA256_IV, 32);
        SHA256_Transform(&ctx1, padded_block2_1);
        SHA256_Transform(&ctx2, padded_block2_2);

        /* Confronto Bitcoin-endian per n1 (bswap32 word-by-word). */
        int valid = 0;
        for (int j = 0; j < 8; j++) {
            uint32_t hw = __builtin_bswap32(ctx1.h[7 - j]);
            if (hw < target_words[j]) { valid = 1; break; }
            if (hw > target_words[j]) {             break; }
        }
        if (valid) { result = (int64_t)n1; break; }

        /* Confronto Bitcoin-endian per n2. */
        valid = 0;
        for (int j = 0; j < 8; j++) {
            uint32_t hw = __builtin_bswap32(ctx2.h[7 - j]);
            if (hw < target_words[j]) { valid = 1; break; }
            if (hw > target_words[j]) {             break; }
        }
        if (valid) { result = (int64_t)n2; break; }
    }

    /* ================================================================
     * Nonce residuo: gestisce batch_size dispari o batch_size == 1.
     * ================================================================ */
    if (result < 0 && i < batch_size) {
        uint32_t n = start_nonce + i;

        padded_tail1[12] = (uint8_t)(n);
        padded_tail1[13] = (uint8_t)(n >>  8);
        padded_tail1[14] = (uint8_t)(n >> 16);
        padded_tail1[15] = (uint8_t)(n >> 24);

        memcpy(ctx1.h, midstate, 32);
        SHA256_Transform(&ctx1, padded_tail1);

        for (int j = 0; j < 8; j++) {
            uint32_t w = ctx1.h[j];
            padded_block2_1[j*4+0] = (uint8_t)(w >> 24);
            padded_block2_1[j*4+1] = (uint8_t)(w >> 16);
            padded_block2_1[j*4+2] = (uint8_t)(w >>  8);
            padded_block2_1[j*4+3] = (uint8_t)(w);
        }

        memcpy(ctx1.h, SHA256_IV, 32);
        SHA256_Transform(&ctx1, padded_block2_1);

        int valid = 0;
        for (int j = 0; j < 8; j++) {
            uint32_t hw = __builtin_bswap32(ctx1.h[7 - j]);
            if (hw < target_words[j]) { valid = 1; break; }
            if (hw > target_words[j]) {             break; }
        }
        if (valid) result = (int64_t)n;
    }

    return result;
}
