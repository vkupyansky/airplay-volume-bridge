#!/usr/bin/env python3
"""Проверка и воспроизводимая упаковка исходников релиза."""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path

PROJECT = "airplay-volume-bridge"
ROOTS = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "Makefile",
    PROJECT,
    "pyproject.toml",
    "uv.lock",
    "ruff.toml",
    ".gitattributes",
    ".gitignore",
    ".github",
    "config",
    "src",
    "scripts",
    "tests",
    "patches",
    "docs",
)
TEXT_SUFFIXES = frozenset(
    {
        ".c",
        ".h",
        ".sh",
        ".py",
        ".toml",
        ".lock",
        ".md",
        ".txt",
        ".yml",
        ".yaml",
        ".json",
        ".conf",
        ".plist",
        ".patch",
        ".svg",
    }
)
PERSONAL_PATH = re.compile(rb"/(?:Users|home)/[^/\s\"<>]+/|/(?:private)/var/folders/")
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class PackageError(RuntimeError):
    """Релиз не прошёл подготовку или проверку содержимого."""


def run_command(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("GITLEAKS_CONFIG", None)
    environment.pop("GITLEAKS_CONFIG_TOML", None)
    return subprocess.run(
        list(arguments),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
        env=environment,
    )


def source_files(project: Path) -> list[Path]:
    files: list[Path] = []

    def visit(path: Path) -> None:
        relative = path.relative_to(project)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise PackageError("Символическая ссылка в исходниках: " + str(relative))
        if path.name == "__pycache__" or path.suffix == ".pyc":
            return
        if stat.S_ISDIR(info.st_mode):
            for child in sorted(path.iterdir()):
                visit(child)
            return
        if not stat.S_ISREG(info.st_mode):
            raise PackageError("Недопустимый тип файла: " + str(relative))
        if len(relative.parts) > 1 or relative.name not in ROOTS:
            if path.suffix not in TEXT_SUFFIXES:
                raise PackageError("Неожиданный файл в исходниках: " + str(relative))
        if info.st_size > 10 * 1024 * 1024:
            raise PackageError("Слишком большой исходный файл: " + str(relative))
        files.append(path)

    for name in ROOTS:
        path = project / name
        if path.exists() or path.is_symlink():
            visit(path)
    return sorted(files)


def stage_sources(project: Path, stage: Path) -> None:
    for source in source_files(project):
        relative = source.relative_to(project)
        content = source.read_bytes()
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PackageError("Нетекстовый исходный файл: " + str(relative)) from error
        if any(byte < 32 and byte not in (9, 10, 13) for byte in content):
            raise PackageError("Двоичные данные в исходниках: " + str(relative))
        if PERSONAL_PATH.search(content) is not None:
            raise PackageError("Персональный путь в исходниках: " + str(relative))
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        executable = bool(source.stat().st_mode & stat.S_IXUSR)
        target.chmod(0o755 if executable else 0o644)


def project_version(stage: Path) -> str:
    try:
        with (stage / "pyproject.toml").open("rb") as source:
            metadata = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PackageError("Не удалось прочитать версию из pyproject.toml") from error
    project: object = metadata.get("project")
    if not isinstance(project, dict) or project.get("name") != PROJECT:
        raise PackageError("Неверное имя проекта в pyproject.toml")
    version: object = project.get("version")
    if (
        not isinstance(version, str)
        or re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version) is None
    ):
        raise PackageError("Версия выпуска должна иметь формат X.Y.Z без prerelease")
    return version


def write_archive(stage: Path, output: Path, prefix: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".source-", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
                with tarfile.open(fileobj=zipped, mode="w") as archive:
                    for path in [stage, *sorted(stage.rglob("*"))]:
                        relative = path.relative_to(stage)
                        name = (
                            prefix
                            if path == stage
                            else prefix + "/" + relative.as_posix()
                        )
                        info = tarfile.TarInfo(name)
                        info.mtime = info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        if path.is_dir():
                            info.type = tarfile.DIRTYPE
                            info.mode = 0o755
                            archive.addfile(info)
                        else:
                            info.mode = path.stat().st_mode & 0o777
                            info.size = path.stat().st_size
                            with path.open("rb") as source:
                                archive.addfile(info, source)
            raw.flush()
            os.fsync(raw.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def package(project: Path, *, runner: Runner = run_command) -> tuple[Path, Path]:
    project = project.resolve(strict=True)
    work = project / "work"
    if work.is_symlink():
        raise PackageError("Каталог work не должен быть символической ссылкой")
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-", dir=work) as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir(mode=0o700)
        stage_sources(project, stage)
        version = project_version(stage)
        command = [
            "gitleaks",
            "dir",
            str(stage),
            "--redact",
            "--no-banner",
            "--no-color",
            "--ignore-gitleaks-allow",
            "--gitleaks-ignore-path",
            str(stage),
            "--timeout",
            "30",
        ]
        try:
            result = runner(command)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PackageError("Не удалось запустить проверку gitleaks") from error
        if result.returncode != 0:
            raise PackageError("Gitleaks отклонил состав исходников; архив не создан")
        outputs = project / "outputs"
        if outputs.is_symlink():
            raise PackageError("Каталог outputs не должен быть символической ссылкой")
        outputs.mkdir(exist_ok=True)
        archive = outputs / f"{PROJECT}-{version}-source.tar.gz"
        write_archive(stage, archive, f"{PROJECT}-{version}")
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        checksums = outputs / "SHA256SUMS"
        descriptor, temporary_name = tempfile.mkstemp(prefix=".sums-", dir=outputs)
        temporary_sums = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(f"{digest}  {archive.name}\n")
                output.flush()
                os.fsync(output.fileno())
            temporary_sums.chmod(0o644)
            os.replace(temporary_sums, checksums)
        finally:
            temporary_sums.unlink(missing_ok=True)
    return archive, checksums


def main() -> int:
    project = Path(__file__).resolve().parent.parent
    try:
        archive, checksums = package(project)
    except (OSError, PackageError) as error:
        print(f"Упаковка не выполнена: {error}")
        return 1
    print("Подготовлены " + archive.name + " и " + checksums.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
