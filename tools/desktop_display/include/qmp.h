#ifndef NAMAKI_QMP_H
#define NAMAKI_QMP_H

#include "platform.h"

#include <stdbool.h>
#include <stddef.h>

typedef struct {
    namaki_socket_t fd;
    namaki_mutex_t lock;
} Qmp;

bool qmp_connect(Qmp *qmp, int port);

bool qmp_call(
    Qmp *qmp,
    const char *command,
    const char *arguments,
    char *response,
    size_t response_size
);

void qmp_close(Qmp *qmp);

#endif