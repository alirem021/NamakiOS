#ifndef NAMAKI_INPUT_H
#define NAMAKI_INPUT_H

#include "qmp.h"

#include <stdbool.h>

bool input_process(
    Qmp *qmp,
    const char *json
);

#endif