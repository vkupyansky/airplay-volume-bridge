#!/usr/bin/env python3
"""Установка и управление пользовательской службой AirPlay."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import plistlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"
SERVICE_ID = "airplay-volume-bridge"
LABEL = "local." + SERVICE_ID
LAUNCHCTL = "/bin/launchctl"
LOG = "/usr/bin/log"
LSOF = "/usr/sbin/lsof"
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class ServiceError(RuntimeError):
    """Ошибка подготовки, запуска или проверки службы."""


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def root(self) -> Path:
        return self.home / "Library/Application Support" / SERVICE_ID

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def config(self) -> Path:
        return self.config_dir / "receiver.conf"

    @property
    def receiver(self) -> Path:
        return self.bin / "receiver"

    @property
    def helper(self) -> Path:
        return self.bin / "mac-volume"

    @property
    def cli(self) -> Path:
        return self.bin / SERVICE_ID

    @property
    def agents(self) -> Path:
        return self.home / "Library/LaunchAgents"

    @property
    def plist(self) -> Path:
        return self.agents / (LABEL + ".plist")

    @property
    def logs(self) -> Path:
        return self.home / "Library/Logs" / SERVICE_ID


def run_command(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def probe_receiver(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(b"OPTIONS * RTSP/1.0\r\nCSeq: 1\r\n\r\n")
            response = connection.recv(4096)
    except OSError:
        return False
    return response.startswith(b"RTSP/1.0 200 ")


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def require_private(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    correct_type = (
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    )
    expected_mode = 0o700 if directory else 0o600
    if (
        not correct_type
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != expected_mode
    ):
        raise ServiceError(f"Неверные владелец, тип или права: {path.name}")


def ensure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_private(path, directory=True)


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".install-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Service:
    def __init__(
        self,
        home: Path,
        *,
        uid: int,
        runner: Runner = run_command,
        probe: Callable[[int], bool] = probe_receiver,
        process_probe: Callable[[int], bool] = process_alive,
        ready_timeout: float = 5,
        lifecycle_timeout: float = 5,
    ) -> None:
        self.paths = Paths(home)
        self.uid = uid
        self.runner = runner
        self.probe = probe
        self.process_probe = process_probe
        self.ready_timeout = ready_timeout
        self.lifecycle_timeout = lifecycle_timeout

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    @property
    def target(self) -> str:
        return self.domain + "/" + LABEL

    def _safe_error(self, text: str) -> str:
        lines = []
        for line in text.splitlines():
            if any(
                word in line.lower()
                for word in ("password", "token", "secret", "authorization", "bearer")
            ):
                continue
            cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", line).strip()
            if cleaned:
                lines.append(cleaned.replace(str(self.paths.home), "~"))
        return "\n".join(lines[:3])[:600]

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(arguments)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ServiceError(
                f"Не удалось выполнить {Path(arguments[0]).name}: "
                "проверь установку зависимости и доступность GUI-сеанса"
            ) from error

    def _checked(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        result = self._run(arguments)
        self._require_success(arguments, result)
        return result

    def _require_success(
        self, arguments: Sequence[str], result: subprocess.CompletedProcess[str]
    ) -> None:
        if result.returncode != 0:
            detail = self._safe_error(result.stderr)
            message = f"{Path(arguments[0]).name}: ошибка {result.returncode}"
            if detail:
                message += "\n" + detail
            raise ServiceError(message)

    def _registration(self) -> str | None:
        result = self._run([LAUNCHCTL, "print", self.target])
        if result.returncode == 0:
            return result.stdout
        if (
            result.returncode == 113
            and "could not find service" in result.stderr.lower()
        ):
            return None
        detail = self._safe_error(result.stderr)
        raise ServiceError(
            "Не удалось прочитать состояние launchd" + (": " + detail if detail else "")
        )

    def _disabled(self) -> bool:
        result = self._checked([LAUNCHCTL, "print-disabled", self.domain])
        values = re.findall(
            r'"' + re.escape(LABEL) + r'"\s*=>\s*([^\s,;}]+)', result.stdout
        )
        if not values and '"' + LABEL + '"' not in result.stdout:
            return False
        if len(values) == 1 and values[0] in ("true", "disabled"):
            return True
        if len(values) == 1 and values[0] in ("false", "enabled"):
            return False
        raise ServiceError("Неизвестный формат состояния автозапуска launchd")

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        require_private(self.paths.root, directory=True)
        descriptor = os.open(
            self.paths.root / ".service.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ServiceError("Неверные права файла блокировки службы")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ServiceError(
                    "Другая операция со службой ещё выполняется"
                ) from error
            yield
        finally:
            os.close(descriptor)

    def _python(self) -> Path:
        branch = f"{sys.version_info.major}.{sys.version_info.minor}"
        brew = shutil.which("brew")
        if brew is None:
            raise ServiceError("Для установки нужен Homebrew в PATH")
        result = self._checked([brew, "--prefix", "python@" + branch])
        prefix = Path(result.stdout.strip())
        if not prefix.is_absolute() or prefix.parts[-2:] != ("opt", "python@" + branch):
            raise ServiceError("Homebrew не вернул устойчивый opt-путь Python")
        interpreter = prefix / "bin" / ("python" + branch)
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise ServiceError("Не установлен Python в выбранной opt-ветке Homebrew")
        if not os.path.samefile(interpreter, sys.executable):
            raise ServiceError(
                "Запусти установку Python из выбранной opt-ветки Homebrew"
            )
        version = self._checked(
            [
                str(interpreter),
                "-c",
                "import sys; "
                "print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ]
        )
        if version.stdout.strip() != branch or sys.version_info < (3, 10):
            raise ServiceError(
                "Нужна согласованная установленная ветка Python 3.10 или новее"
            )
        if any(character.isspace() for character in str(interpreter)):
            raise ServiceError(
                "Путь интерпретатора для shebang не должен содержать пробелы"
            )
        return interpreter

    def _plist(self) -> bytes:
        return plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": [
                    str(self.paths.receiver),
                    "--configfile=" + str(self.paths.config),
                ],
                "KeepAlive": True,
                "ThrottleInterval": 10,
                "Umask": 0o077,
            },
            sort_keys=True,
        )

    def _configuration_port(self, helper: Path, config: Path) -> int:
        result = self._checked([str(helper), "check-config", str(config)])
        try:
            data: object = json.loads(result.stdout)
        except ValueError as error:
            raise ServiceError("Проверка конфигурации вернула неверный JSON") from error
        if not isinstance(data, dict):
            raise ServiceError("Проверка конфигурации не вернула порт приёмника")
        port = data.get("port")
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
        ):
            raise ServiceError("Проверка конфигурации вернула неверный порт")
        return port

    def _validate_payload(self, receiver: Path, helper: Path, config: Path) -> int:
        for binary in (receiver, helper):
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise ServiceError(
                    f"Не найден исполняемый файл: {binary.name}. Сначала выполни сборку"
                )
        version = self._checked([str(receiver), "-V"])
        if (
            not version.stdout.startswith("5.6.3-bridge-" + VERSION + "-")
            or "coreaudio" not in version.stdout.lower()
        ):
            raise ServiceError(
                "Нужен проверенный приёмник AirPlay Volume Bridge "
                + VERSION
                + " на Shairport Sync 5.6.3 с CoreAudio"
            )
        port = self._configuration_port(helper, config)
        self._checked([str(helper), "status"])
        return port

    def _check(self) -> int:
        for directory in (self.paths.root, self.paths.bin, self.paths.config_dir):
            require_private(directory, directory=True)
        require_private(self.paths.config, directory=False)
        for binary in (self.paths.receiver, self.paths.helper, self.paths.cli):
            info = binary.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise ServiceError(f"Неверные владелец, тип или права: {binary.name}")
        require_private(self.paths.plist, directory=False)
        if plistlib.loads(self.paths.plist.read_bytes()) != plistlib.loads(
            self._plist()
        ):
            raise ServiceError(
                "Регистрация службы отличается от установленной версии; повтори install"
            )
        with self.paths.cli.open(encoding="utf-8") as launcher:
            interpreter = launcher.readline().rstrip("\n")[2:]
        if not Path(interpreter).is_file() or "/Cellar/" in interpreter:
            raise ServiceError(
                "Интерпретатор установленного CLI недоступен; повтори install"
            )
        return self._validate_payload(
            self.paths.receiver, self.paths.helper, self.paths.config
        )

    def _bootout(self) -> bool:
        registration = self._registration()
        if registration is None:
            return False
        pid = re.search(r"^\s*pid = ([1-9][0-9]*)$", registration, re.MULTILINE)
        self._checked([LAUNCHCTL, "bootout", self.target])
        deadline = time.monotonic() + self.lifecycle_timeout
        while True:
            old_process_alive = pid is not None and self.process_probe(
                int(pid.group(1))
            )
            if not old_process_alive and self._registration() is None:
                return True
            if time.monotonic() >= deadline:
                raise ServiceError("launchd ещё не завершил прежний процесс службы")
            time.sleep(0.1)

    def _bootstrap(self, *, after_bootout: bool = False) -> None:
        arguments = [LAUNCHCTL, "bootstrap", self.domain, str(self.paths.plist)]
        deadline = time.monotonic() + self.lifecycle_timeout
        while True:
            result = self._run(arguments)
            if result.returncode == 0:
                return
            retry = (
                after_bootout
                and result.returncode == 5
                and self._registration() is None
                and time.monotonic() < deadline
            )
            if not retry:
                self._require_success(arguments, result)
            time.sleep(0.1)

    def _owns_port(self, pid: str, port: int) -> bool:
        result = self._run(
            [LSOF, "-nP", "-a", "-p", pid, "-iTCP:" + str(port), "-sTCP:LISTEN", "-Fp"]
        )
        if result.returncode == 0:
            return ("p" + pid) in result.stdout.splitlines()
        if result.returncode == 1 and not result.stderr.strip():
            return False
        raise ServiceError("Не удалось проверить владельца сетевого порта приёмника")

    def _ready(self, registration: str | None, port: int) -> bool:
        pid = re.search(r"^\s*pid = ([1-9][0-9]*)$", registration or "", re.MULTILINE)
        return (
            pid is not None and self._owns_port(pid.group(1), port) and self.probe(port)
        )

    def _wait_ready(self, port: int) -> None:
        deadline = time.monotonic() + self.ready_timeout
        while True:
            registration = self._registration()
            if self._ready(registration, port):
                return
            if time.monotonic() >= deadline:
                raise ServiceError(
                    "Приёмник не подтвердил готовность. Выполни status и logs"
                )
            time.sleep(0.1)

    def install(self, source: Path) -> None:
        source = source.resolve(strict=True)
        receiver = source / "work/bin/shairport-sync"
        helper = source / "work/bin/mac-volume"
        if not receiver.is_file() or not helper.is_file():
            raise ServiceError(
                "Нет собранных файлов. Выполни сборку и укажи "
                "install --source <каталог проекта>"
            )
        for directory in (
            self.paths.root,
            self.paths.bin,
            self.paths.config_dir,
            self.paths.logs,
        ):
            ensure_directory(directory)
        self.paths.agents.mkdir(mode=0o700, parents=True, exist_ok=True)
        agents_info = self.paths.agents.lstat()
        if (
            not stat.S_ISDIR(agents_info.st_mode)
            or agents_info.st_uid != os.getuid()
            or agents_info.st_mode & 0o022
        ):
            raise ServiceError("Неверные владелец, тип или права каталога LaunchAgents")
        with self._lock():
            interpreter = self._python()
            if self.paths.config.exists() or self.paths.config.is_symlink():
                require_private(self.paths.config, directory=False)
                configuration = self.paths.config.read_bytes()
            else:
                configuration = (source / "config/receiver.conf").read_bytes()
            with tempfile.TemporaryDirectory(
                prefix=".prepare-", dir=self.paths.root
            ) as temporary:
                staging = Path(temporary)
                candidate = staging / "receiver.conf"
                staged_receiver = staging / "receiver"
                staged_helper = staging / "mac-volume"
                atomic_write(candidate, configuration, 0o600)
                atomic_write(staged_receiver, receiver.read_bytes(), 0o700)
                atomic_write(staged_helper, helper.read_bytes(), 0o700)
                port = self._validate_payload(staged_receiver, staged_helper, candidate)
                receiver_content = staged_receiver.read_bytes()
                helper_content = staged_helper.read_bytes()
            script = Path(__file__).read_bytes()
            if script.startswith(b"#!"):
                script = script.split(b"\n", 1)[1]
            launcher = ("#!" + str(interpreter) + "\n").encode() + script
            disabled = self._disabled()
            removed = self._bootout()
            atomic_write(self.paths.receiver, receiver_content, 0o700)
            atomic_write(self.paths.helper, helper_content, 0o700)
            atomic_write(self.paths.cli, launcher, 0o700)
            atomic_write(self.paths.config, configuration, 0o600)
            atomic_write(self.paths.plist, self._plist(), 0o600)
            if not disabled:
                self._checked([LAUNCHCTL, "enable", self.target])
                self._bootstrap(after_bootout=removed)
                self._wait_ready(port)
        print(
            "Служба установлена; отключение пользователем сохранено"
            if disabled
            else "Служба установлена и готова"
        )

    def start(self) -> None:
        with self._lock():
            port = self._check()
            self._checked([LAUNCHCTL, "enable", self.target])
            if self._registration() is None:
                self._bootstrap()
            else:
                self._checked([LAUNCHCTL, "kickstart", self.target])
            self._wait_ready(port)
        print("Служба готова")

    def _stop(self) -> None:
        self._checked([LAUNCHCTL, "disable", self.target])
        self._bootout()

    def stop(self) -> None:
        if not self.paths.root.exists():
            self._stop()
        else:
            with self._lock():
                self._stop()
        print("Служба остановлена; автозапуск отключён")

    def restart(self) -> None:
        with self._lock():
            port = self._check()
            removed = self._bootout()
            self._checked([LAUNCHCTL, "enable", self.target])
            self._bootstrap(after_bootout=removed)
            self._wait_ready(port)
        print("Служба перезапущена и готова")

    def uninstall(self) -> None:
        if self.paths.root.exists():
            with self._lock():
                self._stop()
                if self.paths.bin.exists():
                    require_private(self.paths.bin, directory=True)
                    for binary in (
                        self.paths.receiver,
                        self.paths.helper,
                        self.paths.cli,
                    ):
                        binary.unlink(missing_ok=True)
                self.paths.plist.unlink(missing_ok=True)
        else:
            self._stop()
            self.paths.plist.unlink(missing_ok=True)
        print("Служба удалена; конфигурация и пользовательские данные сохранены")

    def status(self) -> None:
        registration = self._registration()
        disabled = self._disabled()
        print("Установка: " + ("есть" if self.paths.cli.is_file() else "отсутствует"))
        print("Автозапуск: " + ("отключён пользователем" if disabled else "разрешён"))
        print("Регистрация: " + ("есть" if registration is not None else "отсутствует"))
        pid = re.search(r"^\s*pid = ([1-9][0-9]*)$", registration or "", re.MULTILINE)
        print("Процесс: " + ("PID " + pid.group(1) if pid else "не запущен"))
        ready = False
        if pid is not None:
            try:
                port = self._configuration_port(self.paths.helper, self.paths.config)
                ready = self._ready(registration, port)
            except ServiceError as error:
                print("Проверка готовности: " + str(error))
        print("Готовность RTSP: " + ("подтверждена" if ready else "не подтверждена"))
        exit_code = re.search(
            r"^\s*last exit code = (.+)$", registration or "", re.MULTILINE
        )
        if exit_code:
            value = exit_code.group(1).strip()
            print(
                "Последнее завершение: ещё не было"
                if value == "(never exited)"
                else "Последний код выхода: " + value
            )
        if not ready and not disabled:
            print("Диагностика: выполни check и logs")

    def check(self) -> None:
        self._check()
        print("Файлы, конфигурация, зависимости и доступ к аудиовыходу проверены")

    def logs(self) -> None:
        result = self._checked(
            [
                LOG,
                "show",
                "--last",
                "1h",
                "--style",
                "compact",
                "--predicate",
                f'subsystem == "{LABEL}" AND process == "receiver"',
            ]
        )
        print(result.stdout, end="")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Управление службой AirPlay Volume Bridge"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    installation = commands.add_parser("install", help="установить или обновить службу")
    installation.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parent.parent
    )
    for command in (
        "uninstall",
        "start",
        "stop",
        "restart",
        "status",
        "logs",
        "check",
        "version",
    ):
        commands.add_parser(command)
    options = parser.parse_args(arguments)
    if options.command == "version":
        print("AirPlay Volume Bridge " + VERSION)
        return 0
    service = Service(Path.home(), uid=os.getuid())
    try:
        if options.command == "install":
            service.install(options.source)
        else:
            getattr(service, options.command)()
    except (ServiceError, OSError, ValueError, plistlib.InvalidFileException) as error:
        print("Ошибка: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
