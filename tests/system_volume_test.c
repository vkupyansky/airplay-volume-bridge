#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../src/system_volume.h"

int main(void) {
    SystemVolumeState state = {.device_id = 999, .volume = 0.25, .muted = true};
    const double invalid[] = {NAN, INFINITY, -INFINITY, -0.01, 1.01};
    for (size_t index = 0; index < sizeof(invalid) / sizeof(invalid[0]); ++index) {
        const VolumeValue request = {.volume = invalid[index], .muted = false};
        assert(system_volume_apply(&request, &state) != 0);
        assert(state.device_id == 999 && state.volume == 0.25 && state.muted);
        assert(strcmp(system_volume_error(), "Неверный запрос системной громкости") == 0);
    }
    assert(system_volume_apply(NULL, &state) != 0);
    const VolumeValue request = {.volume = 0.5, .muted = false};
    assert(system_volume_apply(&request, NULL) != 0);
    assert(system_volume_read(NULL) != 0);
    puts("system_volume: неверные запросы отклонены до CoreAudio");
    return 0;
}
