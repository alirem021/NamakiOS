CC ?= cc

CFLAGS = -std=c11 -O2 -Wall -Wextra -Wpedantic -Iinclude

TARGET = namaki-desktop

SOURCES = \
	src/main.c \
	src/platform.c \
	src/qmp.c \
	src/display.c \
	src/input.c \
	src/server.c

OBJECTS = $(SOURCES:.c=.o)

ifeq ($(OS),Windows_NT)

TARGET := namaki-desktop.exe
LDFLAGS = -lz -lws2_32

else

UNAME_S := $(shell uname -s)

ifeq ($(UNAME_S),Darwin)

LDFLAGS = -lz -pthread

else

LDFLAGS = -lz -pthread

endif

endif


all: $(TARGET)


$(TARGET): $(OBJECTS)
	$(CC) $(OBJECTS) -o $@ $(LDFLAGS)


src/%.o: src/%.c
	$(CC) $(CFLAGS) -c $< -o $@


clean:
	rm -f $(OBJECTS) $(TARGET)


.PHONY: all clean