from __future__ import annotations

import hashlib
import subprocess
import tarfile
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

from scripts import package


class Scanner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.files: list[str] = []
        self.command: list[str] = []

    def __call__(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.command = list(arguments)
        stage = Path(arguments[2])
        self.files = [
            path.relative_to(stage).as_posix()
            for path in sorted(stage.rglob("*"))
            if path.is_file()
        ]
        return subprocess.CompletedProcess(arguments, self.returncode, "", "")


class PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.write(
            "pyproject.toml",
            '[project]\nname = "airplay-volume-bridge"\nversion = "0.1.0"\n',
        )
        self.write("README.md", "# Source package\n")
        self.write("README.ru.md", "# Исходники\n")
        self.write("src/main.c", "int main(void) { return 0; }\n")

    def write(self, relative: str, content: str) -> Path:
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_allowlist_excludes_runtime_and_caches_before_scan(self) -> None:
        self.write("work/runtime/airplay-password.txt", "private runtime content")
        self.write("outputs/old.txt", "old output")
        self.write("AGENTS.md", "internal instructions")
        self.write("GLOBAL-CONTEXT.md", "private preferences")
        self.write("scripts/__pycache__/cache.pyc", "cache")
        self.write("scripts/cache.pyc", "cache")
        self.write(".github/workflows/check.yml", "name: checks\n")
        scanner = Scanner()
        archive, checksums = package.package(self.project, runner=scanner)
        self.assertEqual(
            scanner.files,
            [
                ".github/workflows/check.yml",
                "README.md",
                "README.ru.md",
                "pyproject.toml",
                "src/main.c",
            ],
        )
        self.assertIn("--redact", scanner.command)
        self.assertIn("--ignore-gitleaks-allow", scanner.command)
        self.assertIn("--no-banner", scanner.command)
        with tarfile.open(archive) as source:
            names = source.getnames()
            self.assertTrue(
                all(name.startswith("airplay-volume-bridge-0.1.0") for name in names)
            )
            self.assertFalse(
                any("work/" in name or "__pycache__" in name for name in names)
            )
        self.assertEqual(
            checksums.read_text(),
            hashlib.sha256(archive.read_bytes()).hexdigest()
            + "  "
            + archive.name
            + "\n",
        )

    def test_archive_is_deterministic_and_has_neutral_metadata(self) -> None:
        executable = self.write("airplay-volume-bridge", "#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        first, _ = package.package(self.project, runner=Scanner())
        before = first.read_bytes()
        second, _ = package.package(self.project, runner=Scanner())
        self.assertEqual(before, second.read_bytes())
        with tarfile.open(second) as archive:
            for member in archive.getmembers():
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
                self.assertEqual((member.uname, member.gname), ("", ""))
                self.assertFalse(member.name.startswith("/"))
                self.assertNotIn("..", Path(member.name).parts)
            member = archive.getmember(
                "airplay-volume-bridge-0.1.0/airplay-volume-bridge"
            )
            self.assertEqual(member.mode, 0o755)

    def test_symlink_is_rejected(self) -> None:
        (self.project / "src/link.c").symlink_to(self.project / "README.md")
        with self.assertRaises(package.PackageError):
            package.package(self.project, runner=Scanner())
        self.assertFalse((self.project / "outputs").exists())

    def test_binary_is_rejected_even_with_source_suffix(self) -> None:
        (self.project / "src/main.c").write_bytes(b"\x00binary")
        with self.assertRaises(package.PackageError):
            package.package(self.project, runner=Scanner())

    def test_unknown_file_is_rejected(self) -> None:
        (self.project / "src/receiver").write_bytes(b"text but not source")
        with self.assertRaises(package.PackageError):
            package.package(self.project, runner=Scanner())

    def test_scan_failure_prevents_archive(self) -> None:
        with self.assertRaises(package.PackageError):
            package.package(self.project, runner=Scanner(returncode=1))
        self.assertFalse((self.project / "outputs").exists())

    def test_personal_path_is_rejected(self) -> None:
        self.write("README.md", "/" + "Users" + "/private-user/Documents/project")
        with self.assertRaises(package.PackageError):
            package.package(self.project, runner=Scanner())

    def test_unsafe_version_is_rejected(self) -> None:
        self.write(
            "pyproject.toml",
            '[project]\nname = "airplay-volume-bridge"\nversion = "../output"\n',
        )
        with self.assertRaises(package.PackageError):
            package.package(self.project, runner=Scanner())


if __name__ == "__main__":
    unittest.main()
