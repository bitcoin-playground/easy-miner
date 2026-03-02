CC      = gcc
# -O3 -march=native: abilita tutte le ottimizzazioni e le estensioni hardware
# del processore corrente (su RPi 5 include SHA-256 hardware via +crypto).
# -fPIC: necessario per shared library.
# -Wall: abilita i warning principali.
CFLAGS  = -O3 -march=native -fPIC -Wall -Wno-deprecated-declarations
LDFLAGS = -lssl -lcrypto

TARGET  = miner_core.so
SRC     = miner_core.c

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) -shared -o $(TARGET) $(SRC) $(LDFLAGS)

clean:
	rm -f $(TARGET)

.PHONY: clean
