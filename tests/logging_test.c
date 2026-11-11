#define _POSIX_C_SOURCE 200809L

#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "debug.h"

static void *write_warnings(void *argument) {
    const unsigned int worker = *(const unsigned int *)argument;
    for (unsigned int event = 0; event < 8; ++event) {
        _warn("logging-test", 1, "worker=%u event=%u", worker, event);
    }
    return NULL;
}

int main(void) {
    int channel[2];
    assert(pipe(channel) == 0);
    const int original_stderr = dup(STDERR_FILENO);
    assert(original_stderr >= 0);
    assert(dup2(channel[1], STDERR_FILENO) == STDERR_FILENO);
    assert(close(channel[1]) == 0);

    debug_init(0, 0, 0, 0, NULL);
    _debug("logging-test", 1, 1, "disabled debug output");

    pthread_t threads[4];
    unsigned int workers[4];
    for (unsigned int index = 0; index < 4; ++index) {
        workers[index] = index;
        assert(pthread_create(&threads[index], NULL, write_warnings,
                              &workers[index]) == 0);
    }
    for (unsigned int index = 0; index < 4; ++index) {
        assert(pthread_join(threads[index], NULL) == 0);
    }

    char long_message[1400];
    memset(long_message, 'x', sizeof(long_message) - 1);
    long_message[sizeof(long_message) - 1] = '\0';
    _warn("logging-test", 1, "%s", long_message);
    _inform("logging-test", 1, "inform output");
    _die("logging-test", 1, "fatal output");

    assert(fflush(stderr) == 0);
    assert(dup2(original_stderr, STDERR_FILENO) == STDERR_FILENO);
    assert(close(original_stderr) == 0);

    char output[8192];
    size_t used = 0;
    ssize_t received;
    while ((received = read(channel[0], output + used,
                            sizeof(output) - used - 1)) > 0) {
        used += (size_t)received;
        assert(used < sizeof(output) - 1);
    }
    assert(received == 0);
    assert(close(channel[0]) == 0);
    output[used] = '\0';

    assert(strstr(output, "disabled debug output") == NULL);
    assert(strstr(output, "inform output\n") != NULL);
    assert(strstr(output, "fatal error: fatal output\n") != NULL);

    unsigned int warnings = 0;
    size_t longest = 0;
    size_t line_length = 0;
    for (const char *cursor = output; *cursor != '\0'; ++cursor) {
        ++line_length;
        if (*cursor == '\n') {
            if (line_length > longest) {
                longest = line_length;
            }
            line_length = 0;
        }
        if ((cursor == output || cursor[-1] == '\n') &&
            strncmp(cursor, "warning: ", 9) == 0) {
            ++warnings;
        }
    }
    assert(warnings == 33);
    assert(longest == 1024);
    puts("logging: потоковая диагностика, уровни и границы проверены");
    return 0;
}
