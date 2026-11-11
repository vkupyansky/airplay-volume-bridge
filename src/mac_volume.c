#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "system_volume.h"
#include "receiver_config.h"

static void print_state(const SystemVolumeState *state) {
    printf("{\"device_id\":%" PRIu32 ",\"volume\":%.9g,\"muted\":%s}\n",
           state->device_id, state->volume, state->muted ? "true" : "false");
}

static int usage(void) {
    fputs("Использование: mac-volume status | mac-volume --dry-run <дБ> | mac-volume <дБ>\n"
          "              mac-volume check-config <путь>\n"
          "Допустимы конечные значения от -30 до 0; -144 включает mute\n", stderr);
    return 2;
}

int main(int argc, char *argv[]) {
    if (argc == 3 && strcmp(argv[1], "check-config") == 0) {
        int port = 0;
        const int result = receiver_config_check(argv[2], &port);
        if (result == 0) {
            printf("{\"port\":%d}\n", port);
        }
        return result;
    }
    const bool status_only = argc == 2 && strcmp(argv[1], "status") == 0;
    const bool dry_run = argc == 3 && strcmp(argv[1], "--dry-run") == 0;
    VolumeValue request = {0};

    if (!status_only) {
        if ((!dry_run && argc != 2) ||
            !volume_value_parse(argv[dry_run ? 2 : 1], &request)) {
            return usage();
        }
        if (dry_run) {
            if (request.muted) {
                puts("{\"volume\":null,\"muted\":true}");
            } else {
                printf("{\"volume\":%.9g,\"muted\":false}\n", request.volume);
            }
            return 0;
        }
    }

    SystemVolumeState state = {0};
    const int result = status_only ? system_volume_read(&state)
                                   : system_volume_apply(&request, &state);
    if (result == 0) {
        print_state(&state);
    } else {
        fprintf(stderr, "%s\n", system_volume_error());
    }
    return result;
}
