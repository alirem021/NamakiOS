#ifndef NAMAKI_PLATFORM_H
#define NAMAKI_PLATFORM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winsock2.h>

typedef SOCKET namaki_socket_t;
typedef HANDLE namaki_thread_t;
typedef CRITICAL_SECTION namaki_mutex_t;

#define NAMAKI_INVALID_SOCKET INVALID_SOCKET

#else

#include <pthread.h>

typedef int namaki_socket_t;
typedef pthread_t namaki_thread_t;
typedef pthread_mutex_t namaki_mutex_t;

#define NAMAKI_INVALID_SOCKET (-1)

#endif

bool platform_init(void);
void platform_cleanup(void);

void platform_sleep_ms(unsigned int milliseconds);

bool platform_mutex_init(namaki_mutex_t *mutex);
void platform_mutex_lock(namaki_mutex_t *mutex);
void platform_mutex_unlock(namaki_mutex_t *mutex);
void platform_mutex_destroy(namaki_mutex_t *mutex);

bool platform_thread_create(
    namaki_thread_t *thread,
    void *(*function)(void *),
    void *argument
);

void platform_thread_join(
    namaki_thread_t thread
);

namaki_socket_t platform_socket_close(
    namaki_socket_t socket
);

bool platform_send_all(
    namaki_socket_t socket,
    const void *data,
    size_t size
);

bool platform_recv_all(
    namaki_socket_t socket,
    void *data,
    size_t size
);

bool platform_recv_byte(
    namaki_socket_t socket,
    char *value
);

namaki_socket_t platform_tcp_connect(
    const char *host,
    int port
);

namaki_socket_t platform_tcp_listen(
    int port,
    int backlog,
    int *actual_port
);

namaki_socket_t platform_tcp_accept(
    namaki_socket_t server
);

#endif