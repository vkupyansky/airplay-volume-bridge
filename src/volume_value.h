#ifndef VOLUME_VALUE_H
#define VOLUME_VALUE_H

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdlib.h>

typedef struct {
    double volume;
    bool muted;
} VolumeValue;

static inline bool volume_value_from_db(double decibels, VolumeValue *value) {
    if (value == NULL || !isfinite(decibels)) {
        return false;
    }
    if (decibels == -144.0) {
        *value = (VolumeValue){.volume = 0.0, .muted = true};
        return true;
    }
    if (decibels < -30.0 || decibels > 0.0) {
        return false;
    }
    *value = (VolumeValue){.volume = (decibels + 30.0) / 30.0, .muted = false};
    return true;
}

static inline bool volume_value_parse(const char *text, VolumeValue *value) {
    if (text == NULL || value == NULL || text[0] == '\0' ||
        isspace((unsigned char)text[0])) {
        return false;
    }

    char *end = NULL;
    errno = 0;
    const double decibels = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' || !isfinite(decibels)) {
        return false;
    }

    return volume_value_from_db(decibels, value);
}

#endif
