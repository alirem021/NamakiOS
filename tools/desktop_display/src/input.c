#include "input.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int get_int(
    const char *object,
    const char *name,
    int fallback
)
{
    char pattern[64];

    snprintf(
        pattern,
        sizeof(pattern),
        "\"%s\":",
        name
    );

    const char *p = strstr(
        object,
        pattern
    );

    if (p == NULL)
        return fallback;

    return atoi(
        p + strlen(pattern)
    );
}

static bool get_bool(
    const char *object,
    const char *name,
    bool fallback
)
{
    char pattern[64];

    snprintf(
        pattern,
        sizeof(pattern),
        "\"%s\":",
        name
    );

    const char *p = strstr(
        object,
        pattern
    );

    if (p == NULL)
        return fallback;

    return strncmp(
        p + strlen(pattern),
        "true",
        4
    ) == 0;
}

static bool get_string(
    const char *object,
    const char *name,
    char *output,
    size_t size
)
{
    char pattern[64];

    snprintf(
        pattern,
        sizeof(pattern),
        "\"%s\":\"",
        name
    );

    const char *p = strstr(
        object,
        pattern
    );

    if (p == NULL)
        return false;

    p += strlen(pattern);

    size_t i = 0;

    while (
        p[i] != '\0' &&
        p[i] != '"' &&
        i + 1 < size
    ) {
        output[i] = p[i];
        i++;
    }

    output[i] = '\0';

    return p[i] == '"';
}

bool input_process(
    Qmp *qmp,
    const char *json
)
{
    const char *p = json;

    while ((p = strchr(p, '{')) != NULL) {
        const char *end = strchr(
            p,
            '}'
        );

        if (end == NULL)
            return false;

        size_t length =
            (size_t)(end - p + 1);

        char object[512];

        if (length >= sizeof(object))
            return false;

        memcpy(
            object,
            p,
            length
        );

        object[length] = '\0';

        char arguments[1024];
        char response[65536];

        if (strstr(
                object,
                "\"type\":\"move\""
            ) != NULL) {

            int x = get_int(
                object,
                "x",
                0
            );

            int y = get_int(
                object,
                "y",
                0
            );

            snprintf(
                arguments,
                sizeof(arguments),
                "{"
                "\"events\":["
                "{"
                "\"type\":\"abs\","
                "\"data\":{"
                "\"axis\":\"x\","
                "\"value\":%d"
                "}"
                "},"
                "{"
                "\"type\":\"abs\","
                "\"data\":{"
                "\"axis\":\"y\","
                "\"value\":%d"
                "}"
                "}"
                "]"
                "}",
                x * 32767 / 1023,
                y * 32767 / 767
            );

            if (!qmp_call(
                    qmp,
                    "input-send-event",
                    arguments,
                    response,
                    sizeof(response)
                ))
                return false;

        } else if (strstr(
                       object,
                       "\"type\":\"button\""
                   ) != NULL) {

            int button = get_int(
                object,
                "button",
                0
            );

            bool down = get_bool(
                object,
                "down",
                false
            );

            const char *name =
                button == 0 ? "left" :
                button == 1 ? "middle" :
                "right";

            snprintf(
                arguments,
                sizeof(arguments),
                "{"
                "\"events\":["
                "{"
                "\"type\":\"btn\","
                "\"data\":{"
                "\"button\":\"%s\","
                "\"down\":%s"
                "}"
                "}"
                "]"
                "}",
                name,
                down ? "true" : "false"
            );

            if (!qmp_call(
                    qmp,
                    "input-send-event",
                    arguments,
                    response,
                    sizeof(response)
                ))
                return false;

        } else if (strstr(
                       object,
                       "\"type\":\"wheel\""
                   ) != NULL) {

            bool up = get_bool(
                object,
                "up",
                false
            );

            const char *button =
                up
                    ? "wheel-up"
                    : "wheel-down";

            snprintf(
                arguments,
                sizeof(arguments),
                "{"
                "\"events\":["
                "{"
                "\"type\":\"btn\","
                "\"data\":{"
                "\"button\":\"%s\","
                "\"down\":true"
                "}"
                "},"
                "{"
                "\"type\":\"btn\","
                "\"data\":{"
                "\"button\":\"%s\","
                "\"down\":false"
                "}"
                "}"
                "]"
                "}",
                button,
                button
            );

            if (!qmp_call(
                    qmp,
                    "input-send-event",
                    arguments,
                    response,
                    sizeof(response)
                ))
                return false;

        } else if (strstr(
                       object,
                       "\"type\":\"key\""
                   ) != NULL) {

            char key[64];

            if (!get_string(
                    object,
                    "key",
                    key,
                    sizeof(key)
                ))
                return false;

            bool down = get_bool(
                object,
                "down",
                false
            );

            snprintf(
                arguments,
                sizeof(arguments),
                "{"
                "\"events\":["
                "{"
                "\"type\":\"key\","
                "\"data\":{"
                "\"key\":{"
                "\"type\":\"qcode\","
                "\"data\":\"%s\""
                "},"
                "\"down\":%s"
                "}"
                "}"
                "]"
                "}",
                key,
                down ? "true" : "false"
            );

            if (!qmp_call(
                    qmp,
                    "input-send-event",
                    arguments,
                    response,
                    sizeof(response)
                ))
                return false;
        }

        p = end + 1;
    }

    return true;
}