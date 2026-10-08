#ifndef NAMAKI_DISPLAY_H
#define NAMAKI_DISPLAY_H

#include "platform.h"
#include "qmp.h"

#include <stdbool.h>
#include <stddef.h>

#define DISPLAY_WIDTH 1024
#define DISPLAY_HEIGHT 768

typedef struct {
    unsigned char *frame;
    size_t frame_size;

    namaki_mutex_t lock;
    namaki_thread_t thread;

    bool running;

    Qmp *qmp;
    char ppm_path[4096];
} Display;

bool display_init(
    Display *display,
    Qmp *qmp,
    const char *ppm_path
);

bool display_start(Display *display);

bool display_get_frame(
    Display *display,
    unsigned char **data,
    size_t *size
);

void display_stop(Display *display);

#endif