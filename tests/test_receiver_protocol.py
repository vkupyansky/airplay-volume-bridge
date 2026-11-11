from __future__ import annotations

import socket
import subprocess
import tempfile
import time
import unittest
from io import BufferedIOBase
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
BINARY = PROJECT / "work/bin/shairport-sync"


def free_port() -> int:
    for _ in range(10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = int(reservation.getsockname()[1])
        if port != 5055:
            return port
    raise AssertionError("Не удалось выделить отдельный тестовый порт")


def connect_receiver(process: subprocess.Popen[bytes], port: int) -> socket.socket:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                f"Тестовый receiver завершился при запуске: {process.returncode}"
            )
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            connection.settimeout(3.0)
            return connection
        except OSError:
            time.sleep(0.05)
    raise AssertionError("Тестовый receiver не открыл порт за 10 секунд")


def rtsp_request(
    stream: BufferedIOBase,
    method: str,
    sequence: int,
    port: int,
    *,
    body: bytes = b"",
    content_type: str | None = None,
) -> int:
    if method not in {"OPTIONS", "ANNOUNCE", "SET_PARAMETER", "TEARDOWN"}:
        raise ValueError("Этот тест не должен запускать аудиовоспроизведение")
    headers = [
        f"{method} rtsp://127.0.0.1:{port}/1 RTSP/1.0",
        f"CSeq: {sequence}",
        "User-Agent: BridgeProtocolTest",
        f"Content-Length: {len(body)}",
    ]
    if content_type is not None:
        headers.append(f"Content-Type: {content_type}")
    stream.write("\r\n".join(headers).encode("ascii") + b"\r\n\r\n" + body)
    stream.flush()

    status_line = stream.readline(8193)
    if len(status_line) > 8192 or not status_line.endswith(b"\r\n"):
        raise AssertionError("Receiver вернул неверную строку статуса RTSP")
    parts = status_line.decode("ascii").split(" ", 2)
    if len(parts) < 2 or parts[0] != "RTSP/1.0":
        raise AssertionError("Receiver вернул неверный статус RTSP")
    status = int(parts[1])
    response_headers: dict[str, str] = {}
    size = len(status_line)
    while True:
        line = stream.readline(8193)
        size += len(line)
        if len(line) > 8192 or size > 32768 or not line.endswith(b"\r\n"):
            raise AssertionError("Receiver вернул неверные заголовки RTSP")
        if line == b"\r\n":
            break
        key, separator, value = line.decode("ascii").partition(":")
        if not separator:
            raise AssertionError("В ответе RTSP отсутствует разделитель заголовка")
        response_headers[key.lower()] = value.strip()
    if response_headers.get("cseq") != str(sequence):
        raise AssertionError("Ответ RTSP относится к другому запросу")
    length = int(response_headers.get("content-length", "0"))
    if not 0 <= length <= 65536:
        raise AssertionError("Неверная длина ответа RTSP")
    if len(stream.read(length)) != length:
        raise AssertionError("Неполное тело ответа RTSP")
    return status


class ReceiverProtocolTests(unittest.TestCase):
    def test_real_receiver_rejects_invalid_volume_without_audio(self) -> None:
        self.assertTrue(BINARY.is_file(), "Сначала выполни bash scripts/build.sh")
        port = free_port()
        with tempfile.TemporaryDirectory(prefix="bridge-protocol-") as directory:
            config = Path(directory) / "receiver.conf"
            config.write_text(
                "general = {\n"
                ' name = "BridgeProtocolTest";\n'
                f" port = {port};\n"
                ' interface = "lo0";\n'
                ' output_backend = "coreaudio";\n'
                ' ignore_volume_control = "yes";\n'
                "};\n"
                'sessioncontrol = { allow_session_interruption = "no"; };\n',
                encoding="utf-8",
            )
            with tempfile.TemporaryFile() as log:
                process = subprocess.Popen(
                    [str(BINARY), f"--configfile={config}"],
                    cwd=directory,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                forced = False
                terminated = False
                try:
                    with connect_receiver(process, port) as connection:
                        with connection.makefile("rwb") as stream:
                            self.assertEqual(
                                rtsp_request(stream, "OPTIONS", 1, port), 200
                            )
                            sdp = (
                                "v=0\r\n"
                                "o=BridgeProtocolTest 1 1 IN IP4 127.0.0.1\r\n"
                                "s=BridgeProtocolTest\r\n"
                                "c=IN IP4 127.0.0.1\r\n"
                                "t=0 0\r\n"
                                "m=audio 0 RTP/AVP 96\r\n"
                                "a=rtpmap:96 L16/44100/2\r\n"
                            ).encode("ascii")
                            self.assertEqual(
                                rtsp_request(
                                    stream,
                                    "ANNOUNCE",
                                    2,
                                    port,
                                    body=sdp,
                                    content_type="application/sdp",
                                ),
                                200,
                            )
                            # atof также не превратит эти строки в допустимый уровень
                            for sequence, value in enumerate(
                                ("NaN", "inf", "-31", "1", "1x"), start=3
                            ):
                                with self.subTest(value=value):
                                    self.assertEqual(
                                        rtsp_request(
                                            stream,
                                            "SET_PARAMETER",
                                            sequence,
                                            port,
                                            body=f"volume: {value}\r\n".encode("ascii"),
                                            content_type="text/parameters",
                                        ),
                                        400,
                                    )
                            self.assertEqual(
                                rtsp_request(stream, "TEARDOWN", 8, port), 200
                            )
                    self.assertIsNone(process.poll(), "Receiver завершился до SIGTERM")
                finally:
                    if process.poll() is None:
                        process.terminate()
                        terminated = True
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        forced = True
                        process.kill()
                        process.wait(timeout=5.0)
                self.assertTrue(terminated, "Receiver не дождался SIGTERM")
                self.assertFalse(forced, "Receiver потребовал принудительный SIGKILL")
                self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
