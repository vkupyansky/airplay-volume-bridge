#include <assert.h>
#include <stddef.h>
#include <stdio.h>

#include "../src/volume_value.h"

static void expect_volume(const char *text, double expected) {
    VolumeValue value = {.volume = -1.0, .muted = true};
    assert(volume_value_parse(text, &value));
    assert(!value.muted);
    assert(fabs(value.volume - expected) < 1e-12);
}

int main(void) {
    expect_volume("-30", 0.0);
    expect_volume("-15", 0.5);
    expect_volume("0", 1.0);
    expect_volume("-0.0", 1.0);
    expect_volume("-7.5", 0.75);
    expect_volume("-1.5e1", 0.5);

    VolumeValue value = {.volume = -1.0, .muted = false};
    assert(volume_value_parse("-144", &value));
    assert(value.muted);
    assert(volume_value_parse("-144.000", &value));
    assert(value.muted);
    assert(!volume_value_parse(NULL, &value));
    assert(!volume_value_parse("0", NULL));

    // нативный приёмник передаёт число напрямую, без строкового CLI
    assert(volume_value_from_db(-15.0, &value));
    assert(value.volume == 0.5 && !value.muted);
    assert(volume_value_from_db(-144.0, &value));
    assert(value.muted);
    const double invalid_numbers[] = {NAN, INFINITY, -INFINITY, -30.001, 0.001, -143.0};
    for (size_t i = 0; i < sizeof(invalid_numbers) / sizeof(invalid_numbers[0]); ++i) {
        value = (VolumeValue){.volume = 0.25, .muted = true};
        assert(!volume_value_from_db(invalid_numbers[i], &value));
        assert(value.volume == 0.25 && value.muted);
    }

    const char *invalid[] = {
        "", " ", " -15", "-15 ", "-15x", "0\n", "NaN", "nan(1)",
        "inf", "+inf", "-inf", "-30.001", "0.001", "-143", "-144.001",
        "-145", "--1", "1e999", "-1e-999", "-15,5",
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        value = (VolumeValue){.volume = 0.25, .muted = true};
        assert(!volume_value_parse(invalid[i], &value));
        assert(value.volume == 0.25);
        assert(value.muted);
    }

    puts("volume_value: все проверки пройдены");
    return 0;
}
