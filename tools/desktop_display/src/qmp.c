#define _POSIX_C_SOURCE 200809L

#include "qmp.h"
#include "platform.h"

#include <stdio.h>
#include <string.h>

#define QMP_BUFFER_SIZE 65536

static bool read_line(
    namaki_socket_t socket,
    char *buffer,
    size_t size
)
{
    size_t position = 0;

    while (position + 1 < size) {
        char c;

        if (!platform_recv_byte(
                socket,
                &c
            ))
            return false;

        buffer[position++] = c;

        if (c == '\n') {
            buffer[position] = '\0';
            return true;
        }
    }

    return false;
}

bool qmp_connect(
    Qmp *qmp,
    int port
)
{
    qmp->fd =
        NAMAKI_INVALID_SOCKET;

    if (!platform_mutex_init(
            &qmp->lock
        ))
        return false;

    qmp->fd =
        platform_tcp_connect(
            "127.0.0.1",
            port
        );

    if (qmp->fd ==
        NAMAKI_INVALID_SOCKET) {
        platform_mutex_destroy(
            &qmp->lock
        );

        return false;
    }

    char greeting[
        QMP_BUFFER_SIZE
    ];

    if (!read_line(
            qmp->fd,
            greeting,
            sizeof(greeting)
        )) {
        qmp_close(qmp);
        return false;
    }

    char response[
        QMP_BUFFER_SIZE
    ];

    return qmp_call(
        qmp,
        "qmp_capabilities",
        NULL,
        response,
        sizeof(response)
    );
}

bool qmp_call(
    Qmp *qmp,
    const char *command,
    const char *arguments,
    char *response,
    size_t response_size
)
{
    if (
        qmp == NULL ||
        qmp->fd == NAMAKI_INVALID_SOCKET
    )
        return false;

    platform_mutex_lock(
        &qmp->lock
    );

    char request[
        QMP_BUFFER_SIZE
    ];

    int written;

    if (arguments != NULL) {
        written = snprintf(
            request,
            sizeof(request),
            "{\"execute\":\"%s\","
            "\"arguments\":%s}\n",
            command,
            arguments
        );
    } else {
        written = snprintf(
            request,
            sizeof(request),
            "{\"execute\":\"%s\"}\n",
            command
        );
    }

    if (
        written < 0 ||
        (size_t)written >= sizeof(request)
    ) {
        platform_mutex_unlock(
            &qmp->lock
        );

        return false;
    }

    if (!platform_send_all(
            qmp->fd,
            request,
            (size_t)written
        )) {
        platform_mutex_unlock(
            &qmp->lock
        );

        return false;
    }

    bool success = false;

    while (true) {
        if (!read_line(
                qmp->fd,
                response,
                response_size
            ))
            break;

        if (strstr(
                response,
                "\"error\""
            ) != NULL)
            break;

        if (strstr(
                response,
                "\"return\""
            ) != NULL) {
            success = true;
            break;
        }
    }

    platform_mutex_unlock(
        &qmp->lock
    );

    return success;
}

void qmp_close(
    Qmp *qmp
)
{
    if (qmp == NULL)
        return;

    if (
        qmp->fd !=
        NAMAKI_INVALID_SOCKET
    ) {
        qmp->fd =
            platform_socket_close(
                qmp->fd
            );
    }

    platform_mutex_destroy(
        &qmp->lock
    );
}