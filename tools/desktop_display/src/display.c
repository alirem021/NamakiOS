#include "display.h"
#include "platform.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <zlib.h>

#define FRAME_INTERVAL_MS 22

static void sleep_frame(void)
{
    platform_sleep_ms(
        FRAME_INTERVAL_MS
    );
}

static void write_u32(
    unsigned char *buffer,
    uint32_t value
)
{
    buffer[0] =
        (unsigned char)(value >> 24);

    buffer[1] =
        (unsigned char)(value >> 16);

    buffer[2] =
        (unsigned char)(value >> 8);

    buffer[3] =
        (unsigned char)value;
}

static bool png_chunk(
    unsigned char **output,
    size_t *output_size,
    const char *type,
    const unsigned char *data,
    size_t data_size
)
{
    size_t old_size =
        *output_size;

    size_t new_size =
        old_size + 12 + data_size;

    unsigned char *buffer =
        realloc(
            *output,
            new_size
        );

    if (buffer == NULL)
        return false;

    *output = buffer;

    unsigned char *p =
        buffer + old_size;

    write_u32(
        p,
        (uint32_t)data_size
    );

    p += 4;

    memcpy(
        p,
        type,
        4
    );

    p += 4;

    if (data_size > 0) {
        memcpy(
            p,
            data,
            data_size
        );

        p += data_size;
    }

    uint32_t crc =
        (uint32_t)crc32(
            0L,
            buffer + old_size + 4,
            (uInt)(4 + data_size)
        );

    write_u32(
        p,
        crc
    );

    *output_size =
        new_size;

    return true;
}

static bool make_png(
    const unsigned char *rgb,
    int width,
    int height,
    unsigned char **output,
    size_t *output_size
)
{
    size_t raw_size =
        (size_t)height *
        ((size_t)width * 3 + 1);

    unsigned char *raw =
        malloc(raw_size);

    if (raw == NULL)
        return false;

    for (
        int y = 0;
        y < height;
        y++
    ) {
        size_t offset =
            (size_t)y *
            ((size_t)width * 3 + 1);

        raw[offset] = 0;

        memcpy(
            raw + offset + 1,
            rgb + (size_t)y * width * 3,
            (size_t)width * 3
        );
    }

    uLongf compressed_size =
        compressBound(
            (uLong)raw_size
        );

    unsigned char *compressed =
        malloc(compressed_size);

    if (compressed == NULL) {
        free(raw);
        return false;
    }

    if (
        compress2(
            compressed,
            &compressed_size,
            raw,
            (uLong)raw_size,
            1
        ) != Z_OK
    ) {
        free(raw);
        free(compressed);
        return false;
    }

    static const unsigned char signature[] = {
        137, 80, 78, 71,
        13, 10, 26, 10
    };

    unsigned char *png =
        malloc(sizeof(signature));

    if (png == NULL) {
        free(raw);
        free(compressed);
        return false;
    }

    memcpy(
        png,
        signature,
        sizeof(signature)
    );

    size_t png_size =
        sizeof(signature);

    unsigned char ihdr[13];

    write_u32(
        ihdr,
        (uint32_t)width
    );

    write_u32(
        ihdr + 4,
        (uint32_t)height
    );

    ihdr[8] = 8;
    ihdr[9] = 2;
    ihdr[10] = 0;
    ihdr[11] = 0;
    ihdr[12] = 0;

    if (!png_chunk(
            &png,
            &png_size,
            "IHDR",
            ihdr,
            sizeof(ihdr)
        ))
        goto fail;

    if (!png_chunk(
            &png,
            &png_size,
            "IDAT",
            compressed,
            compressed_size
        ))
        goto fail;

    if (!png_chunk(
            &png,
            &png_size,
            "IEND",
            NULL,
            0
        ))
        goto fail;

    free(raw);
    free(compressed);

    *output = png;
    *output_size = png_size;

    return true;

fail:
    free(raw);
    free(compressed);
    free(png);

    return false;
}

static bool read_ppm(
    const char *path,
    unsigned char **rgb
)
{
    FILE *file =
        fopen(path, "rb");

    if (file == NULL)
        return false;

    char magic[3];

    int width;
    int height;
    int maximum;

    if (
        fscanf(
            file,
            "%2s %d %d %d",
            magic,
            &width,
            &height,
            &maximum
        ) != 4
    ) {
        fclose(file);
        return false;
    }

    if (
        strcmp(magic, "P6") != 0 ||
        width != DISPLAY_WIDTH ||
        height != DISPLAY_HEIGHT ||
        maximum != 255
    ) {
        fclose(file);
        return false;
    }

    fgetc(file);

    size_t size =
        (size_t)width *
        (size_t)height *
        3;

    unsigned char *buffer =
        malloc(size);

    if (buffer == NULL) {
        fclose(file);
        return false;
    }

    if (
        fread(
            buffer,
            1,
            size,
            file
        ) != size
    ) {
        free(buffer);
        fclose(file);
        return false;
    }

    fclose(file);

    *rgb = buffer;

    return true;
}

static void *capture_thread(
    void *argument
)
{
    Display *display =
        argument;

    while (display->running) {
        char arguments[4096];

        int written =
            snprintf(
                arguments,
                sizeof(arguments),
                "{\"filename\":\"%s\"}",
                display->ppm_path
            );

        if (
            written < 0 ||
            (size_t)written >=
                sizeof(arguments)
        )
            break;

        char response[65536];

        if (!qmp_call(
                display->qmp,
                "screendump",
                arguments,
                response,
                sizeof(response)
            ))
            break;

        unsigned char *rgb = NULL;

        if (!read_ppm(
                display->ppm_path,
                &rgb
            )) {
            sleep_frame();
            continue;
        }

        unsigned char *png = NULL;
        size_t png_size = 0;

        if (
            make_png(
                rgb,
                DISPLAY_WIDTH,
                DISPLAY_HEIGHT,
                &png,
                &png_size
            )
        ) {
            platform_mutex_lock(
                &display->lock
            );

            free(display->frame);

            display->frame = png;
            display->frame_size =
                png_size;

            platform_mutex_unlock(
                &display->lock
            );
        }

        free(rgb);

        sleep_frame();
    }

    return NULL;
}

bool display_init(
    Display *display,
    Qmp *qmp,
    const char *ppm_path
)
{
    memset(
        display,
        0,
        sizeof(*display)
    );

    display->qmp = qmp;

    snprintf(
        display->ppm_path,
        sizeof(display->ppm_path),
        "%s",
        ppm_path
    );

    if (!platform_mutex_init(
            &display->lock
        ))
        return false;

    return true;
}

bool display_start(
    Display *display
)
{
    display->running = true;

    if (!platform_thread_create(
            &display->thread,
            capture_thread,
            display
        )) {
        display->running = false;
        return false;
    }

    return true;
}

bool display_get_frame(
    Display *display,
    unsigned char **data,
    size_t *size
)
{
    platform_mutex_lock(
        &display->lock
    );

    if (display->frame == NULL) {
        platform_mutex_unlock(
            &display->lock
        );

        return false;
    }

    unsigned char *copy =
        malloc(
            display->frame_size
        );

    if (copy == NULL) {
        platform_mutex_unlock(
            &display->lock
        );

        return false;
    }

    memcpy(
        copy,
        display->frame,
        display->frame_size
    );

    *data = copy;
    *size =
        display->frame_size;

    platform_mutex_unlock(
        &display->lock
    );

    return true;
}

void display_stop(
    Display *display
)
{
    if (!display->running)
        return;

    display->running = false;

    platform_thread_join(
        display->thread
    );

    platform_mutex_lock(
        &display->lock
    );

    free(display->frame);

    display->frame = NULL;
    display->frame_size = 0;

    platform_mutex_unlock(
        &display->lock
    );

    platform_mutex_destroy(
        &display->lock
    );
}