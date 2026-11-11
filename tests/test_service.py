from __future__ import annotations

import contextlib
import io
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

from scripts import service


class FakeCommands:
    def __init__(self, prefix: Path) -> None:
        self.prefix = prefix
        self.calls: list[list[str]] = []
        self.loaded = False
        self.running = False
        self.disabled = False
        self.disabled_value: str | None = None
        self.ready = True
        self.owns_port = True
        self.port = 5055
        self.receiver_version = (
            "5.6.3-bridge-" + service.VERSION + "-OpenSSL-dns_sd-coreaudio-ffmpeg\n"
        )
        self.dependency_error = False
        self.query_error = False
        self.shutdown_delay = 0
        self.remaining_shutdown_checks = 0
        self.process_checks: list[int] = []
        self.bootstrap_errors: list[int] = []
        self.last_exit = "0"

    def process_alive(self, pid: int) -> bool:
        self.process_checks.append(pid)
        if self.remaining_shutdown_checks:
            self.remaining_shutdown_checks -= 1
            return True
        return False

    def __call__(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        args = list(arguments)
        self.calls.append(args)
        output = ""
        error = ""
        code = 0
        if args[0] == "/test/brew":
            output = str(self.prefix) + "\n"
        elif (
            Path(args[0]).name
            == "python" + f"{sys.version_info.major}.{sys.version_info.minor}"
        ):
            output = f"{sys.version_info.major}.{sys.version_info.minor}\n"
        elif Path(args[0]).name in ("receiver", "shairport-sync"):
            if self.dependency_error:
                code = 1
                error = (
                    "dyld: Library not loaded: "
                    "/opt/homebrew/opt/ffmpeg/lib/libavcodec.dylib\n"
                    "Authorization: Bearer private-value\n"
                )
            else:
                output = self.receiver_version
        elif Path(args[0]).name == "mac-volume":
            if args[1] == "check-config":
                if "invalid-config" in Path(args[2]).read_text():
                    code = 1
                    error = "Неверная конфигурация\n"
                else:
                    output = '{"port":' + str(self.port) + "}\n"
            elif args[1] == "status":
                output = '{"volume":0.25,"muted":false}\n'
            else:
                raise AssertionError("Тест не должен менять громкость")
        elif args[0] == service.LAUNCHCTL:
            operation = args[1]
            if operation == "print":
                if self.query_error:
                    code, error = 5, "Permission denied\n"
                elif not self.loaded:
                    code, error = 113, "Could not find service\n"
                else:
                    output = (
                        "state = running\n" if self.running else "state = waiting\n"
                    )
                    if self.running:
                        output += "pid = 12345\n"
                    output += "last exit code = " + self.last_exit + "\n"
            elif operation == "print-disabled":
                value = self.disabled_value
                if value is None:
                    value = "disabled" if self.disabled else "enabled"
                output = f'{{ "{service.LABEL}" => {value} }}\n'
            elif operation == "disable":
                self.disabled = True
            elif operation == "enable":
                self.disabled = False
            elif operation == "bootout":
                self.loaded = self.running = False
                self.remaining_shutdown_checks = self.shutdown_delay
            elif operation == "bootstrap":
                if self.disabled or self.loaded:
                    raise AssertionError("Неверный порядок регистрации службы")
                if self.remaining_shutdown_checks:
                    raise AssertionError("Прежний процесс ещё не завершён")
                if self.bootstrap_errors:
                    code = self.bootstrap_errors.pop(0)
                    error = "Bootstrap failed: " + str(code) + "\n"
                else:
                    self.loaded = self.running = True
            elif operation == "kickstart":
                if not self.loaded or self.disabled:
                    raise AssertionError("Нельзя запустить незарегистрированную службу")
                self.running = True
            else:
                raise AssertionError("Неожиданная операция launchctl: " + operation)
        elif args[0] == service.LSOF:
            if self.loaded and self.running and self.owns_port:
                output = "p12345\n"
            else:
                code = 1
        elif args[0] == service.LOG:
            output = "Одна диагностическая запись\n"
        else:
            raise AssertionError("Неожиданная команда: " + args[0])
        return subprocess.CompletedProcess(args, code, stdout=output, stderr=error)


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.source = self.root / "source"
        binaries = self.source / "work/bin"
        binaries.mkdir(parents=True)
        for name in ("shairport-sync", "mac-volume"):
            binary = binaries / name
            binary.write_bytes(("binary: " + name).encode())
            binary.chmod(0o700)
        configuration = self.source / "config/receiver.conf"
        configuration.parent.mkdir()
        configuration.write_text(
            'general = { name = "Mac Volume Bridge"; port = 5055; '
            'output_backend = "coreaudio"; ignore_volume_control = "yes"; };\n'
            'sessioncontrol = { allow_session_interruption = "no"; };\n'
        )
        branch = f"{sys.version_info.major}.{sys.version_info.minor}"
        prefix = self.root / "brew/opt" / ("python@" + branch)
        interpreter = prefix / "bin" / ("python" + branch)
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        self.fake = FakeCommands(prefix)
        self.manager = service.Service(
            self.home,
            uid=os.getuid(),
            runner=self.fake,
            probe=lambda port: (
                self.fake.running and self.fake.ready and port == self.fake.port
            ),
            process_probe=self.fake.process_alive,
            ready_timeout=0,
        )
        self.paths = self.manager.paths
        which = patch("scripts.service.shutil.which", return_value="/test/brew")
        which.start()
        self.addCleanup(which.stop)

    def install(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.install(self.source)

    def lifecycle_calls(self) -> list[str]:
        return [args[1] for args in self.fake.calls if args[0] == service.LAUNCHCTL]

    def test_install_has_one_native_foreground_worker_and_private_state(self) -> None:
        self.install()
        self.assertTrue(self.fake.loaded and self.fake.running)
        self.assertFalse(self.fake.disabled)
        self.assertEqual(
            self.paths.config.read_bytes(),
            (self.source / "config/receiver.conf").read_bytes(),
        )
        configuration = plistlib.loads(self.paths.plist.read_bytes())
        self.assertEqual(configuration["Label"], service.LABEL)
        self.assertEqual(
            configuration["ProgramArguments"],
            [str(self.paths.receiver), "--configfile=" + str(self.paths.config)],
        )
        self.assertIs(configuration["KeepAlive"], True)
        self.assertEqual(configuration["ThrottleInterval"], 10)
        for absent in (
            "RunAtLoad",
            "ProcessType",
            "StandardErrorPath",
            "StandardOutPath",
        ):
            self.assertNotIn(absent, configuration)
        for directory in (
            self.paths.root,
            self.paths.bin,
            self.paths.config_dir,
            self.paths.logs,
        ):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.paths.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.paths.plist.stat().st_mode & 0o777, 0o600)

    def test_installed_cli_is_standalone_and_uses_stable_opt_python(self) -> None:
        self.install()
        first_line = self.paths.cli.read_text().splitlines()[0]
        branch = f"{sys.version_info.major}.{sys.version_info.minor}"
        self.assertEqual(
            first_line, "#!" + str(self.fake.prefix / "bin" / ("python" + branch))
        )
        self.assertNotIn("/Cellar/", first_line)
        result = subprocess.run(
            [sys.executable, str(self.paths.cli), "version"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.stdout, "AirPlay Volume Bridge " + service.VERSION + "\n"
        )

    def test_stop_is_persistent_and_repeatable(self) -> None:
        self.install()
        self.fake.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.stop()
            self.manager.stop()
        self.assertTrue(self.fake.disabled)
        self.assertFalse(self.fake.loaded)
        operations = self.lifecycle_calls()
        self.assertEqual(operations.count("bootout"), 1)
        self.assertLess(operations.index("disable"), operations.index("bootout"))

    def test_reinstall_preserves_user_config_and_disabled_state(self) -> None:
        self.install()
        configuration = self.paths.config.read_bytes() + b"// custom preference\n"
        self.paths.config.write_bytes(configuration)
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.stop()
        self.fake.calls.clear()
        (self.source / "work/bin/shairport-sync").write_bytes(b"updated receiver")
        (self.source / "config/receiver.conf").unlink()
        self.install()
        self.assertEqual(self.paths.config.read_bytes(), configuration)
        self.assertEqual(self.paths.receiver.read_bytes(), b"updated receiver")
        self.assertTrue(self.fake.disabled)
        self.assertFalse(self.fake.loaded)
        self.assertNotIn("enable", self.lifecycle_calls())
        self.assertNotIn("bootstrap", self.lifecycle_calls())

    def test_disabled_state_accepts_boolean_and_word_forms(self) -> None:
        for value, expected in (
            ("true", True),
            ("false", False),
            ("disabled", True),
            ("enabled", False),
        ):
            with self.subTest(value=value):
                self.fake.disabled_value = value
                self.assertIs(self.manager._disabled(), expected)

    def test_unknown_explicit_disabled_state_does_not_restart_service(self) -> None:
        self.install()
        self.fake.calls.clear()
        self.fake.disabled_value = "unknown"
        with self.assertRaisesRegex(service.ServiceError, "формат"):
            self.install()
        self.assertTrue(self.fake.running)
        self.assertNotIn("bootout", self.lifecycle_calls())

    def test_reinstall_running_service_uses_graceful_unload(self) -> None:
        self.install()
        self.fake.calls.clear()
        self.install()
        operations = self.lifecycle_calls()
        self.assertEqual(operations.count("bootout"), 1)
        self.assertEqual(operations.count("bootstrap"), 1)
        self.assertLess(operations.index("bootout"), operations.index("bootstrap"))

    def test_start_enables_stopped_service_and_is_idempotent(self) -> None:
        self.install()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.stop()
        self.fake.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.start()
            self.manager.start()
        self.assertFalse(self.fake.disabled)
        self.assertTrue(self.fake.loaded and self.fake.running)
        self.assertEqual(self.lifecycle_calls().count("bootstrap"), 1)

    def test_restart_unloads_before_bootstrap_without_force_kill(self) -> None:
        self.install()
        self.fake.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.restart()
        operations = self.lifecycle_calls()
        self.assertLess(operations.index("bootout"), operations.index("bootstrap"))
        self.assertNotIn("kickstart", operations)
        self.assertTrue(self.fake.running)

    def test_restart_waits_for_old_pid_after_registration_disappears(self) -> None:
        self.install()
        self.fake.shutdown_delay = 2
        self.fake.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.restart()
        self.assertEqual(self.fake.process_checks, [12345, 12345, 12345])
        self.assertTrue(self.fake.running)

    def test_restart_retries_transient_bootstrap_error_after_own_bootout(self) -> None:
        self.install()
        self.fake.bootstrap_errors = [5, 5]
        self.fake.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.restart()
        self.assertEqual(self.lifecycle_calls().count("bootstrap"), 3)
        self.assertTrue(self.fake.running)

    def test_start_does_not_retry_bootstrap_error_without_own_bootout(self) -> None:
        self.install()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.stop()
        self.fake.bootstrap_errors = [5]
        self.fake.calls.clear()
        with self.assertRaises(service.ServiceError):
            self.manager.start()
        self.assertEqual(self.lifecycle_calls().count("bootstrap"), 1)

    def test_restart_does_not_retry_other_bootstrap_errors(self) -> None:
        self.install()
        self.fake.bootstrap_errors = [78]
        self.fake.calls.clear()
        with self.assertRaises(service.ServiceError):
            self.manager.restart()
        self.assertEqual(self.lifecycle_calls().count("bootstrap"), 1)

    def test_shutdown_timeout_prevents_bootstrap(self) -> None:
        self.install()
        self.manager.lifecycle_timeout = 0
        self.fake.shutdown_delay = 1
        self.fake.calls.clear()
        with self.assertRaisesRegex(service.ServiceError, "прежний процесс"):
            self.manager.restart()
        self.assertNotIn("bootstrap", self.lifecycle_calls())

    def test_uninstall_is_idempotent_and_preserves_user_data(self) -> None:
        self.install()
        configuration = self.paths.config.read_bytes()
        user_data = self.paths.root / "user-data.txt"
        user_data.write_text("keep")
        exported_log = self.paths.logs / "export.txt"
        exported_log.write_text("keep")
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.uninstall()
            self.manager.uninstall()
        self.assertFalse(self.fake.loaded)
        self.assertTrue(self.fake.disabled)
        self.assertFalse(self.paths.receiver.exists())
        self.assertFalse(self.paths.helper.exists())
        self.assertFalse(self.paths.cli.exists())
        self.assertFalse(self.paths.plist.exists())
        self.assertEqual(self.paths.config.read_bytes(), configuration)
        self.assertEqual(user_data.read_text(), "keep")
        self.assertEqual(exported_log.read_text(), "keep")

    def test_dependency_failure_does_not_stop_or_replace_running_installation(
        self,
    ) -> None:
        self.install()
        previous = self.paths.receiver.read_bytes()
        self.fake.calls.clear()
        self.fake.dependency_error = True
        with self.assertRaises(service.ServiceError) as caught:
            self.install()
        self.assertIn("Library not loaded", str(caught.exception))
        self.assertNotIn("private-value", str(caught.exception))
        self.assertEqual(self.paths.receiver.read_bytes(), previous)
        self.assertTrue(self.fake.running)
        self.assertNotIn("bootout", self.lifecycle_calls())

    def test_invalid_config_does_not_stop_running_installation(self) -> None:
        self.install()
        self.paths.config.write_text("invalid-config")
        self.fake.calls.clear()
        with self.assertRaises(service.ServiceError):
            self.install()
        self.assertTrue(self.fake.running)
        self.assertNotIn("bootout", self.lifecycle_calls())

    def test_old_receiver_without_bridge_marker_is_rejected_before_bootout(
        self,
    ) -> None:
        self.install()
        previous = self.paths.receiver.read_bytes()
        self.fake.calls.clear()
        self.fake.receiver_version = "5.6.3-OpenSSL-dns_sd-coreaudio-ffmpeg\n"
        with self.assertRaisesRegex(service.ServiceError, "AirPlay Volume Bridge"):
            self.install()
        self.assertTrue(self.fake.running)
        self.assertEqual(self.paths.receiver.read_bytes(), previous)
        self.assertNotIn("bootout", self.lifecycle_calls())

    def test_query_error_is_not_treated_as_missing_service(self) -> None:
        self.install()
        self.fake.calls.clear()
        self.fake.query_error = True
        with self.assertRaises(service.ServiceError):
            self.manager.start()
        self.assertNotIn("bootstrap", self.lifecycle_calls())

    def test_missing_source_does_not_create_installation(self) -> None:
        (self.source / "work/bin/mac-volume").unlink()
        with self.assertRaises(service.ServiceError):
            self.install()
        self.assertFalse(self.paths.root.exists())
        self.assertFalse(self.fake.loaded)

    def test_cellar_python_is_rejected(self) -> None:
        self.fake.prefix = self.root / "brew/Cellar/python/3.15.2"
        with self.assertRaisesRegex(service.ServiceError, "opt"):
            self.install()
        self.assertFalse(self.fake.loaded)

    def test_public_config_permissions_are_rejected(self) -> None:
        self.install()
        self.paths.config.chmod(0o644)
        self.fake.calls.clear()
        with self.assertRaises(service.ServiceError):
            self.install()
        self.assertTrue(self.fake.running)
        self.assertNotIn("bootout", self.lifecycle_calls())

    def test_status_separates_pid_from_rtsp_readiness(self) -> None:
        self.install()
        self.fake.ready = False
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.manager.status()
        self.assertIn("Регистрация: есть", output.getvalue())
        self.assertIn("Процесс: PID 12345", output.getvalue())
        self.assertIn("Готовность RTSP: не подтверждена", output.getvalue())
        self.assertIn("check и logs", output.getvalue())

    def test_status_describes_never_exited_without_truncation(self) -> None:
        self.install()
        self.fake.last_exit = "(never exited)"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.manager.status()
        self.assertIn("Последнее завершение: ещё не было", output.getvalue())
        self.assertNotIn("(never", output.getvalue())

    def test_status_rejects_rtsp_reply_from_another_process(self) -> None:
        self.install()
        self.fake.owns_port = False
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.manager.status()
        self.assertIn("Готовность RTSP: не подтверждена", output.getvalue())

    def test_readiness_uses_validated_configuration_port(self) -> None:
        self.fake.port = 6066
        self.install()
        listener_call = next(
            args for args in self.fake.calls if args[0] == service.LSOF
        )
        self.assertIn("-iTCP:6066", listener_call)

    def test_install_reports_readiness_timeout(self) -> None:
        self.fake.ready = False
        with self.assertRaisesRegex(service.ServiceError, "готовность"):
            self.install()
        self.assertTrue(self.fake.loaded)

    def test_check_and_logs_never_invoke_volume_mutation(self) -> None:
        self.install()
        self.fake.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.check()
            self.manager.logs()
        helper_calls = [
            args[1] for args in self.fake.calls if Path(args[0]).name == "mac-volume"
        ]
        self.assertEqual(helper_calls, ["check-config", "status"])
        log_command = next(args for args in self.fake.calls if args[0] == service.LOG)
        self.assertIn(
            f'subsystem == "{service.LABEL}" AND process == "receiver"', log_command
        )
        self.assertEqual(self.lifecycle_calls(), [])

    def test_concurrent_management_is_rejected(self) -> None:
        self.install()
        with self.manager._lock():
            with self.assertRaisesRegex(service.ServiceError, "операция"):
                self.manager.stop()


if __name__ == "__main__":
    unittest.main()
