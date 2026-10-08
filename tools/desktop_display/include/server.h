#ifndef NAMAKI_SERVER_H
#define NAMAKI_SERVER_H

#include "display.h"
#include "platform.h"
#include "qmp.h"

#include <stdbool.h>

typedef struct {
    namaki_socket_t fd;
    int port;

    char token[128];

    Display *display;
    Qmp *qmp;

    namaki_thread_t thread;
    bool running;
} Server;

bool server_start(
    Server *server,
    Display *display,
    Qmp *qmp,
    const char *token
);

void server_stop(Server *server);

int server_port(Server *server);

#endif