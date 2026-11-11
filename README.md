[![Русский](https://img.shields.io/badge/%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-181717?style=for-the-badge)](README.ru.md)

# AirPlay Volume Bridge

A local macOS service that receives music from an iPhone over AirPlay and lets
the phone's volume slider and buttons control the Mac's system volume.

**[Download v0.1.0 source](https://github.com/vkupyansky/airplay-volume-bridge/releases/download/v0.1.0/airplay-volume-bridge-0.1.0-source.tar.gz)** · [Release and SHA256SUMS](https://github.com/vkupyansky/airplay-volume-bridge/releases/tag/v0.1.0)

## Features

- Stream Apple Music from an iPhone to the Mac's speakers.
- Control the Mac's system volume from the phone, including mute.
- One native C process, with no separate process for each button press.
- Login startup and crash recovery managed by launchd.
- No iPhone app, cloud service, account or connection password.
- Readiness checks, service controls and the macOS system log.

## Install and run

Verified on macOS 27.2.1 with Apple Silicon. Building requires Xcode Command Line
Tools, Homebrew and Python 3.15. Other macOS versions and Intel Macs have not
been verified.

Extract the release archive, open a terminal in its directory and run:

```sh
brew install autoconf automake pkgconf libconfig popt openssl@3 ffmpeg python@3.15
make install
```

The build downloads Shairport Sync 5.6.3, verifies SHA-256 and applies local
patches. Existing Homebrew dependencies are retained. Binaries are built on
your Mac; the archive does not include prebuilt executables.

Installation places the service separately from the source checkout, starts it
and enables login startup. Administrator access is not required. Reinstallation
preserves configuration and an explicit user-disabled state.

## Usage

1. Connect the iPhone and Mac to the same Wi-Fi network.
2. Play a track in Apple Music on the iPhone.
3. Select **Mac Volume Bridge** in the AirPlay list.
4. Adjust the iPhone's volume slider or buttons. The Mac's system volume changes.

No password is required. Other devices on the same network can connect;
use the service on a trusted home network and do not expose its port to the internet.

The installed service controls work even after removing the source checkout:

```sh
BRIDGE="$HOME/Library/Application Support/airplay-volume-bridge/bin/airplay-volume-bridge"
"$BRIDGE" status
```

| Command | Action |
| --- | --- |
| `start` | Start the service and enable login startup |
| `stop` | Stop the service and disable login startup |
| `restart` | Restart gracefully |
| `status` | Show registration, process state and RTSP readiness |
| `check` | Validate files, configuration, dependencies and audio output |
| `logs` | Show the service's events from the last hour |
| `version` | Show the version |
| `uninstall` | Remove the service, preserving configuration and data |

To update, extract a new release and run `make install` again.
After an earlier `stop`, run `start` explicitly.

## Checks

Development also requires [uv](https://docs.astral.sh/uv/).
Tool versions are pinned in `uv.lock`.

```sh
make check
```

This builds the C code with warnings as errors and runs tests, strict type
checking, Ruff and static analysis. The protocol test uses a separate local
port and does not change volume. Installed-service and iPhone scenarios
are described in the [testing guide](docs/testing.md) (Russian).

## Limitations

- Uses standard AirPlay with one audio stream. AirPlay 2 and multiroom are disabled.
- This is a separate receiver; select its own entry in the AirPlay list.
- Control flows from iPhone to Mac. Mac volume keys do not necessarily move the iPhone slider.
- Requires an audio output with software volume control. HDMI and some USB DACs may
  not provide it; the service reports an error instead of claiming success.
- Homebrew libraries remain necessary after installation; keep them while using the service.
- No Apple notarization. Sources are built and signed locally.
- Connection after Mac sleep and wake has not yet been verified.

## Documentation

The technical guides are maintained in Russian:

- [Architecture and file locations](docs/architecture.md).
- [Checks and verification limits](docs/testing.md).
- [Release preparation](docs/release.md).

## License

The project's code and patches use [MIT](LICENSE).
The receiver is based on Shairport Sync; third-party components and their
licenses are listed in [NOTICE](NOTICE). Dependencies are downloaded separately
and are not included in the source archive.
