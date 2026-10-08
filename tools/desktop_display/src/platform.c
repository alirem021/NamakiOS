#define _POSIX_C_SOURCE 200809L

#include "platform.h"

#include <stdio.h>
#include <string.h>

#ifdef _WIN32

#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>

bool platform_init(void)
{
    WSADATA data;

    return WSAStartup(
        MAKEWORD(2, 2),
        &data
    ) == 0;
}

void platform_cleanup(void)
{
    WSACleanup();
}

void platform_sleep_ms(
    unsigned int milliseconds
)
{
    Sleep(milliseconds);
}

bool platform_mutex_init(
    namaki_mutex_t *mutex
)
{
    InitializeCriticalSection(mutex);

    return true;
}

void platform_mutex_lock(
    namaki_mutex_t *mutex
)
{
    EnterCriticalSection(mutex);
}

void platform_mutex_unlock(
    namaki_mutex_t *mutex
)
{
    LeaveCriticalSection(mutex);
}

void platform_mutex_destroy(
    namaki_mutex_t *mutex
)
{
    DeleteCriticalSection(mutex);
}

typedef struct {
    void *(*function)(void *);
    void *argument;
} ThreadArguments;

static DWORD WINAPI thread_entry(
    LPVOID argument
)
{
    ThreadArguments *args =
        argument;

    void *(*function)(void *) =
        args->function;

    void *function_argument =
        args->argument;

    function(function_argument);

    HeapFree(
        GetProcessHeap(),
        0,
        args
    );

    return 0;
}

bool platform_thread_create(
    namaki_thread_t *thread,
    void *(*function)(void *),
    void *argument
)
{
    ThreadArguments *args =
        HeapAlloc(
            GetProcessHeap(),
            HEAP_ZERO_MEMORY,
            sizeof(*args)
        );

    if (args == NULL)
        return false;

    args->function = function;
    args->argument = argument;

    *thread = CreateThread(
        NULL,
        0,
        thread_entry,
        args,
        0,
        NULL
    );

    if (*thread == NULL) {
        HeapFree(
            GetProcessHeap(),
            0,
            args
        );

        return false;
    }

    return true;
}

void platform_thread_join(
    namaki_thread_t thread
)
{
    WaitForSingleObject(
        thread,
        INFINITE
    );

    CloseHandle(thread);
}

namaki_socket_t platform_socket_close(
    namaki_socket_t socket
)
{
    if (socket != INVALID_SOCKET)
        closesocket(socket);

    return INVALID_SOCKET;
}

bool platform_send_all(
    namaki_socket_t socket,
    const void *data,
    size_t size
)
{
    const char *buffer = data;
    size_t sent = 0;

    while (sent < size) {
        int count = send(
            socket,
            buffer + sent,
            (int)(size - sent),
            0
        );

        if (count <= 0)
            return false;

        sent += (size_t)count;
    }

    return true;
}

bool platform_recv_all(
    namaki_socket_t socket,
    void *data,
    size_t size
)
{
    char *buffer = data;
    size_t received = 0;

    while (received < size) {
        int count = recv(
            socket,
            buffer + received,
            (int)(size - received),
            0
        );

        if (count <= 0)
            return false;

        received += (size_t)count;
    }

    return true;
}

bool platform_recv_byte(
    namaki_socket_t socket,
    char *value
)
{
    return recv(
        socket,
        value,
        1,
        0
    ) == 1;
}

#else

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

bool platform_init(void)
{
    return true;
}

void platform_cleanup(void)
{
}

void platform_sleep_ms(
    unsigned int milliseconds
)
{
    struct timespec delay = {
        .tv_sec =
            (time_t)(milliseconds / 1000),
        .tv_nsec =
            (long)(milliseconds % 1000)
            * 1000000L
    };

    nanosleep(
        &delay,
        NULL
    );
}

bool platform_mutex_init(
    namaki_mutex_t *mutex
)
{
    return pthread_mutex_init(
        mutex,
        NULL
    ) == 0;
}

void platform_mutex_lock(
    namaki_mutex_t *mutex
)
{
    pthread_mutex_lock(mutex);
}

void platform_mutex_unlock(
    namaki_mutex_t *mutex
)
{
    pthread_mutex_unlock(mutex);
}

void platform_mutex_destroy(
    namaki_mutex_t *mutex
)
{
    pthread_mutex_destroy(mutex);
}

bool platform_thread_create(
    namaki_thread_t *thread,
    void *(*function)(void *),
    void *argument
)
{
    return pthread_create(
        thread,
        NULL,
        function,
        argument
    ) == 0;
}

void platform_thread_join(
    namaki_thread_t thread
)
{
    pthread_join(
        thread,
        NULL
    );
}

namaki_socket_t platform_socket_close(
    namaki_socket_t socket
)
{
    if (socket >= 0)
        close(socket);

    return -1;
}

bool platform_send_all(
    namaki_socket_t socket,
    const void *data,
    size_t size
)
{
    const char *buffer = data;
    size_t sent = 0;

    while (sent < size) {
        ssize_t count = send(
            socket,
            buffer + sent,
            size - sent,
            0
        );

        if (count <= 0)
            return false;

        sent += (size_t)count;
    }

    return true;
}

bool platform_recv_all(
    namaki_socket_t socket,
    void *data,
    size_t size
)
{
    char *buffer = data;
    size_t received = 0;

    while (received < size) {
        ssize_t count = recv(
            socket,
            buffer + received,
            size - received,
            0
        );

        if (count <= 0)
            return false;

        received += (size_t)count;
    }

    return true;
}

bool platform_recv_byte(
    namaki_socket_t socket,
    char *value
)
{
    return recv(
        socket,
        value,
        1,
        0
    ) == 1;
}

#endif

namaki_socket_t platform_tcp_connect(
    const char *host,
    int port
)
{
    namaki_socket_t socket_fd =
        socket(
            AF_INET,
            SOCK_STREAM,
            0
        );

    if (socket_fd == NAMAKI_INVALID_SOCKET)
        return NAMAKI_INVALID_SOCKET;

    struct sockaddr_in address;

    memset(
        &address,
        0,
        sizeof(address)
    );

    address.sin_family = AF_INET;

    address.sin_port = htons(
        (uint16_t)port
    );

    if (inet_pton(
            AF_INET,
            host,
            &address.sin_addr
        ) != 1) {
        return platform_socket_close(
            socket_fd
        );
    }

    if (connect(
            socket_fd,
            (struct sockaddr *)&address,
            sizeof(address)
        ) != 0) {
        return platform_socket_close(
            socket_fd
        );
    }

    return socket_fd;
}

namaki_socket_t platform_tcp_listen(
    int port,
    int backlog,
    int *actual_port
)
{
    namaki_socket_t server =
        socket(
            AF_INET,
            SOCK_STREAM,
            0
        );

    if (server == NAMAKI_INVALID_SOCKET)
        return NAMAKI_INVALID_SOCKET;

    int reuse = 1;

#ifdef _WIN32

    setsockopt(
        server,
        SOL_SOCKET,
        SO_REUSEADDR,
        (const char *)&reuse,
        sizeof(reuse)
    );

#else

    setsockopt(
        server,
        SOL_SOCKET,
        SO_REUSEADDR,
        &reuse,
        sizeof(reuse)
    );

#endif

    struct sockaddr_in address;

    memset(
        &address,
        0,
        sizeof(address)
    );

    address.sin_family = AF_INET;

    address.sin_addr.s_addr =
        htonl(INADDR_LOOPBACK);

    address.sin_port = htons(
        (uint16_t)port
    );

    if (bind(
            server,
            (struct sockaddr *)&address,
            sizeof(address)
        ) != 0) {
        return platform_socket_close(
            server
        );
    }

    if (listen(
            server,
            backlog
        ) != 0) {
        return platform_socket_close(
            server
        );
    }

    struct sockaddr_in bound;

#ifdef _WIN32

    int length = sizeof(bound);

#else

    socklen_t length = sizeof(bound);

#endif

    if (getsockname(
            server,
            (struct sockaddr *)&bound,
            &length
        ) != 0) {
        return platform_socket_close(
            server
        );
    }

    if (actual_port != NULL)
        *actual_port = ntohs(
            bound.sin_port
        );

    return server;
}

namaki_socket_t platform_tcp_accept(
    namaki_socket_t server
)
{
    return accept(
        server,
        NULL,
        NULL
    );
}