#!/bin/bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$project_dir"

version=5.6.3
archive_sha256=670f9cefdb9c2d70f7df71890e63ab0a31d08a815dd0734245d935bfd01e766e
archive="work/downloads/shairport-sync-$version.tar.gz"
source_dir="$project_dir/work/receiver-source/shairport-sync-$version"

for tool in clang autoreconf automake pkg-config brew; do
  command -v "$tool" >/dev/null || { echo "Не найден инструмент: $tool" >&2; exit 1; }
done

openssl_prefix="$(HOMEBREW_NO_AUTO_UPDATE=1 brew --prefix openssl@3)"
brew_prefix="$(HOMEBREW_NO_AUTO_UPDATE=1 brew --prefix)"
python_bin="$(HOMEBREW_NO_AUTO_UPDATE=1 brew --prefix python@3.15)/bin/python3.15"
test -x "$python_bin" || { echo 'Нужен Homebrew Python 3.15' >&2; exit 1; }
export PKG_CONFIG_PATH="$openssl_prefix/lib/pkgconfig:$brew_prefix/lib/pkgconfig"
packages="libconfig popt libssl libcrypto libavutil libavcodec libavformat libswresample"
pkg-config --exists $packages || { echo 'Не установлены зависимости из README.md' >&2; exit 1; }

mkdir -p work/downloads work/receiver-source work/bin
if [ ! -f "$archive" ]; then
  curl --fail --location --retry 3 --connect-timeout 10 --max-time 120 \
    --output "$archive.part" "https://github.com/mikebrady/shairport-sync/archive/refs/tags/$version.tar.gz"
  mv "$archive.part" "$archive"
fi
printf '%s  %s\n' "$archive_sha256" "$archive" | shasum -a 256 -c -
build_log="$project_dir/work/shairport-build.log"
trap 'tail -n 35 "$build_log" >&2' ERR
(
  if [ -f "$source_dir/Makefile" ]; then
    make -C "$source_dir" clean
  fi
  tar -xzf "$archive" -C work/receiver-source
  cp src/system_volume.c src/system_volume.h src/volume_value.h "$source_dir/"
  "$python_bin" scripts/build_info.py --header "$source_dir/bridge_version.h"
  cd "$source_dir"
  patch --silent -p1 < "$project_dir/patches/shairport-native-volume.patch"
  patch --silent -p1 < "$project_dir/patches/shairport-unified-logging.patch"
  patch --silent -p1 < "$project_dir/patches/shairport-version.patch"
  autoreconf --force --install --verbose
  # проверки autoconf используют искусственные объявления функций
  CFLAGS=-O2 CXXFLAGS=-O2 ./configure --with-os=darwin --with-coreaudio \
    --with-dns_sd --with-ssl=openssl --with-ffmpeg \
    --without-create-user-group --without-configfiles --prefix=/usr/local \
    --sysconfdir=/etc/airplay-volume-bridge
  make -j4 CFLAGS="$(pkg-config --cflags $packages) -O2 -Werror" CXXFLAGS='-O2 -Werror'
) > "$build_log" 2>&1
trap - ERR

cp "$source_dir/shairport-sync" work/bin/.shairport-sync.new
chmod 755 work/bin/.shairport-sync.new
mv -f work/bin/.shairport-sync.new work/bin/shairport-sync
clang -std=c17 -O2 -Wall -Wextra -Wpedantic -Werror \
  $(pkg-config --cflags libconfig) src/mac_volume.c src/system_volume.c src/receiver_config.c \
  $(pkg-config --libs libconfig) -framework AudioToolbox -framework CoreAudio \
  -o work/bin/.mac-volume.new
mv -f work/bin/.mac-volume.new work/bin/mac-volume
"$python_bin" scripts/build_info.py
work/bin/shairport-sync -V
echo 'Сборка завершена. Проверки: bash scripts/check.sh'
