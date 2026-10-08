#include "display.h"
#include "platform.h"
#include "qmp.h"
#include "server.h"

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>

static volatile sig_atomic_t running = 1;

static void stop_handler(
    int signal_number
)
{
    (void)signal_number;

    running = 0;
}

int main(
    int argc,
    char **argv
)
{
    if (argc != 3) {
        fprintf(
            stderr,
            "Usage: %s <qmp-port> <ppm-path>\n",
            argv[0]
        );

        return 1;
    }

    int qmp_port =
        atoi(argv[1]);

    if (
        qmp_port <= 0 ||
        qmp_port > 65535
    ) {
        fprintf(
            stderr,
            "Invalid QMP port.\n"
        );

        return 1;
    }

    if (!platform_init()) {
        fprintf(
            stderr,
            "Platform initialization failed.\n"
        );

        return 1;
    }

    signal(
        SIGINT,
        stop_handler
    );

    signal(
        SIGTERM,
        stop_handler
    );

    const char *ppm_path =
        argv[2];

    Qmp qmp;

    if (!qmp_connect(
            &qmp,
            qmp_port
        )) {
        fprintf(
            stderr,
            "Failed to connect to QMP.\n"
        );

        platform_cleanup();

        return 1;
    }

    Display display;

    if (!display_init(
            &display,
            &qmp,
            ppm_path
        )) {
        qmp_close(&qmp);
        platform_cleanup();

        return 1;
    }

    if (!display_start(
            &display
        )) {
        display_stop(&display);
        qmp_close(&qmp);
        platform_cleanup();

        return 1;
    }

    Server server;

    if (!server_start(
            &server,
            &display,
            &qmp,
            "namaki"
        )) {
        display_stop(&display);
        qmp_close(&qmp);
        platform_cleanup();

        return 1;
    }

    printf(
        "NAMAKI_DESKTOP_PORT=%d\n",
        server_port(&server)
    );

    fflush(stdout);

    while (running)
        platform_sleep_ms(100);

    server_stop(&server);

    display_stop(&display);

    qmp_close(&qmp);

    platform_cleanup();

    return 0;
}