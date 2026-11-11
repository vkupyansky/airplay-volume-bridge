#include <stdio.h>

#include "../src/receiver_config.h"

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fputs("Использование: config-test <конфигурация>\n", stderr);
        return 2;
    }
    int port = 0;
    const int result = receiver_config_check(argv[1], &port);
    if (result == 0) {
        printf("{\"port\":%d}\n", port);
    }
    return result;
}
