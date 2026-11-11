#define _POSIX_C_SOURCE 200809L

#include <AudioToolbox/AudioHardwareService.h>
#include <CoreAudio/CoreAudio.h>
#include <inttypes.h>
#include <stdio.h>
#include <time.h>

#include "system_volume.h"

static _Thread_local char last_error[256];

const char *system_volume_error(void) {
    return last_error;
}

static int fail(const char *message) {
    (void)snprintf(last_error, sizeof(last_error), "%s", message);
    return 1;
}

static const AudioObjectPropertyAddress volume_property = {
    .mSelector = kAudioHardwareServiceDeviceProperty_VirtualMainVolume,
    .mScope = kAudioDevicePropertyScopeOutput,
    .mElement = kAudioObjectPropertyElementMain,
};

static const AudioObjectPropertyAddress mute_property = {
    .mSelector = kAudioDevicePropertyMute,
    .mScope = kAudioDevicePropertyScopeOutput,
    .mElement = kAudioObjectPropertyElementMain,
};

static int report_error(const char *operation, OSStatus status) {
    (void)snprintf(last_error, sizeof(last_error), "%s: OSStatus=%" PRId32,
                   operation, (int32_t)status);
    return 1;
}

static int default_output(AudioDeviceID *device) {
    const AudioObjectPropertyAddress property = {
        .mSelector = kAudioHardwarePropertyDefaultOutputDevice,
        .mScope = kAudioObjectPropertyScopeGlobal,
        .mElement = kAudioObjectPropertyElementMain,
    };
    UInt32 size = (UInt32)sizeof(*device);
    const OSStatus status = AudioObjectGetPropertyData(
        kAudioObjectSystemObject, &property, 0, NULL, &size, device);
    if (status != noErr) {
        return report_error("Не удалось получить устройство вывода", status);
    }
    if (size != sizeof(*device) || *device == kAudioObjectUnknown) {
        return fail("Системное устройство вывода недоступно");
    }
    return 0;
}

static int read_property(AudioDeviceID device,
                         const AudioObjectPropertyAddress *property,
                         void *value, UInt32 expected_size,
                         const char *operation) {
    UInt32 size = expected_size;
    const OSStatus status = AudioObjectGetPropertyData(
        device, property, 0, NULL, &size, value);
    if (status != noErr) {
        return report_error(operation, status);
    }
    if (size != expected_size) {
        (void)snprintf(last_error, sizeof(last_error), "%s: неверный размер свойства", operation);
        return 1;
    }
    return 0;
}

static int read_state(AudioDeviceID device, SystemVolumeState *state) {
    Float32 volume = 0.0F;
    UInt32 muted = 0;
    if (read_property(device, &volume_property, &volume, (UInt32)sizeof(volume),
                      "Не удалось прочитать громкость") != 0 ||
        read_property(device, &mute_property, &muted, (UInt32)sizeof(muted),
                      "Не удалось прочитать состояние mute") != 0) {
        return 1;
    }
    if (!isfinite(volume) || volume < 0.0F || volume > 1.0F || muted > 1) {
        return fail("Устройство вернуло неверное состояние громкости");
    }
    *state = (SystemVolumeState){
        .device_id = device, .volume = volume, .muted = muted != 0,
    };
    return 0;
}

int system_volume_read(SystemVolumeState *state) {
    if (state == NULL) {
        return fail("Не передан результат чтения системной громкости");
    }
    AudioDeviceID device = kAudioObjectUnknown;
    return default_output(&device) != 0 ? 1 : read_state(device, state);
}

static int require_writable(AudioDeviceID device,
                            const AudioObjectPropertyAddress *property,
                            const char *operation) {
    Boolean writable = false;
    const OSStatus status = AudioObjectIsPropertySettable(device, property, &writable);
    if (status != noErr) {
        return report_error(operation, status);
    }
    if (!writable) {
        (void)snprintf(last_error, sizeof(last_error), "%s: свойство недоступно для записи", operation);
        return 1;
    }
    return 0;
}

static int write_property(AudioDeviceID device,
                          const AudioObjectPropertyAddress *property,
                          const void *value, UInt32 size,
                          const char *operation) {
    const OSStatus status = AudioObjectSetPropertyData(
        device, property, 0, NULL, size, value);
    return status == noErr ? 0 : report_error(operation, status);
}

static int confirm_state(AudioDeviceID device, const SystemVolumeState *expected,
                         SystemVolumeState *actual) {
    const struct timespec delay = {.tv_sec = 0, .tv_nsec = 2000000};
    // короткое ожидание допускает асинхронное применение, не задерживая поток на секунду
    for (unsigned int attempt = 0; attempt < 5; ++attempt) {
        if (read_state(device, actual) != 0) {
            return 1;
        }
        if (actual->muted == expected->muted &&
            fabs(actual->volume - expected->volume) <= 0.01) {
            return 0;
        }
        if (attempt + 1 < 5 && nanosleep(&delay, NULL) != 0) {
            return fail("Ожидание подтверждения громкости прервано");
        }
    }
    (void)snprintf(last_error, sizeof(last_error),
                   "Устройство не подтвердило изменение: volume=%.9g, muted=%s",
                   actual->volume, actual->muted ? "true" : "false");
    return 1;
}

int system_volume_apply(const VolumeValue *request, SystemVolumeState *actual) {
    if (request == NULL || actual == NULL || !isfinite(request->volume) ||
        request->volume < 0.0 || request->volume > 1.0) {
        return fail("Неверный запрос системной громкости");
    }
    SystemVolumeState expected = {0};
    if (system_volume_read(&expected) != 0 ||
        require_writable(expected.device_id, &mute_property,
                         "Нельзя изменить состояние mute") != 0) {
        return 1;
    }
    if (!request->muted &&
        require_writable(expected.device_id, &volume_property,
                         "Нельзя изменить системную громкость") != 0) {
        return 1;
    }

    AudioDeviceID current_device = kAudioObjectUnknown;
    if (default_output(&current_device) != 0) {
        return 1;
    }
    if (current_device != expected.device_id) {
        return fail("Устройство вывода изменилось, повторите действие");
    }

    if (!request->muted) {
        const Float32 volume = (Float32)request->volume;
        if (write_property(expected.device_id, &volume_property, &volume,
                           (UInt32)sizeof(volume),
                           "Не удалось изменить громкость") != 0) {
            return 1;
        }
        expected.volume = volume;
    }
    const UInt32 muted = request->muted ? 1U : 0U;
    if (write_property(expected.device_id, &mute_property, &muted,
                       (UInt32)sizeof(muted),
                       "Не удалось изменить mute; громкость могла измениться") != 0) {
        return 1;
    }
    expected.muted = request->muted;
    return confirm_state(expected.device_id, &expected, actual);
}
