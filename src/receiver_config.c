#include <libconfig.h>
#include <stdio.h>
#include <string.h>

#include "receiver_config.h"

static int require_string(const config_t *config, const char *name,
                          const char *expected) {
    const char *value = NULL;
    if (config_lookup_string(config, name, &value) != CONFIG_TRUE || value[0] == '\0' ||
        (expected != NULL && strcmp(value, expected) != 0)) {
        fprintf(stderr, "config: неверное или отсутствующее поле %s\n", name);
        return 1;
    }
    return 0;
}

int receiver_config_check(const char *path, int *port) {
    config_t config;
    config_init(&config);
    int result = 1;
    if (path == NULL || port == NULL || config_read_file(&config, path) != CONFIG_TRUE) {
        fputs("config: не удалось прочитать конфигурацию\n", stderr);
        goto cleanup;
    }
    if (require_string(&config, "general.name", NULL) != 0 ||
        require_string(&config, "general.output_backend", "coreaudio") != 0 ||
        require_string(&config, "general.ignore_volume_control", "yes") != 0 ||
        require_string(&config, "sessioncontrol.allow_session_interruption", "no") != 0) {
        goto cleanup;
    }
    const config_setting_t *port_setting = config_lookup(&config, "general.port");
    if (port_setting == NULL || config_setting_type(port_setting) != CONFIG_TYPE_INT ||
        config_setting_get_int(port_setting) < 1 || config_setting_get_int(port_setting) > 65535) {
        fputs("config: general.port должен быть целым числом от 1 до 65535\n", stderr);
        goto cleanup;
    }
    if (config_lookup(&config, "general.password") != NULL ||
        config_lookup(&config, "general.run_this_when_volume_is_set") != NULL) {
        fputs("config: пароль и внешний обработчик громкости запрещены\n", stderr);
        goto cleanup;
    }
    if (config_lookup(&config, "general.allow_session_interruption") != NULL ||
        config_lookup(&config, "general.wait_for_completion") != NULL) {
        fputs("config: параметры сессии ошибочно размещены в general\n", stderr);
        goto cleanup;
    }
    *port = config_setting_get_int(port_setting);
    result = 0;

cleanup:
    config_destroy(&config);
    return result;
}
