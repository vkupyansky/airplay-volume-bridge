#ifndef SYSTEM_VOLUME_H
#define SYSTEM_VOLUME_H

#include <stdint.h>

#include "volume_value.h"

typedef struct {
    uint32_t device_id;
    double volume;
    bool muted;
} SystemVolumeState;

int system_volume_read(SystemVolumeState *state);
int system_volume_apply(const VolumeValue *request, SystemVolumeState *actual);
const char *system_volume_error(void);

#endif
