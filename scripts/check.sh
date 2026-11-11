#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p work/bin
export UV_PROJECT_ENVIRONMENT="$PWD/work/venv"
uv sync --frozen --group dev
clang -std=c17 -O2 -Wall -Wextra -Wpedantic -Werror \
  $(pkg-config --cflags libconfig) src/mac_volume.c src/system_volume.c src/receiver_config.c \
  $(pkg-config --libs libconfig) -framework AudioToolbox -framework CoreAudio \
  -o work/bin/.mac-volume.new
mv -f work/bin/.mac-volume.new work/bin/mac-volume
clang -std=c17 -O2 -Wall -Wextra -Wpedantic -Werror \
  tests/volume_value_test.c -o work/bin/volume-value-test
clang -std=c17 -O2 -Wall -Wextra -Wpedantic -Werror \
  $(pkg-config --cflags libconfig) tests/config_test.c src/receiver_config.c \
  $(pkg-config --libs libconfig) -o work/bin/config-test
clang -std=c17 -O2 -Wall -Wextra -Wpedantic -Werror \
  tests/system_volume_test.c src/system_volume.c \
  -framework AudioToolbox -framework CoreAudio -o work/bin/system-volume-test
work/bin/volume-value-test
work/bin/system-volume-test
receiver_source="$PWD/work/receiver-source/shairport-sync-5.6.3"
clang -std=c2x -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
  -I"$receiver_source/utilities" tests/logging_test.c \
  "$receiver_source/utilities/debug.o" -o work/bin/logging-test
work/bin/logging-test
uv run --frozen python -W error -m unittest discover -s tests -p 'test_*.py'
uv run --frozen ruff check scripts tests
uv run --frozen ruff format --check scripts tests
uv run --frozen mypy
bash -n scripts/build.sh scripts/check.sh
mkdir -p work/analysis
for source in src/mac_volume.c src/system_volume.c src/receiver_config.c; do
  clang --analyze -std=c17 -Wall -Wextra -Wpedantic -Werror \
    $(pkg-config --cflags libconfig) "$source" -o "work/analysis/$(basename "$source" .c).plist"
done
echo 'Локальные проверки пройдены. Громкость не менялась.'
