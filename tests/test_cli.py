from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_dry_run_does_not_require_audio_device(self) -> None:
        for argument, expected in (
            ("-15", {"volume": 0.5, "muted": False}),
            ("-144", {"volume": None, "muted": True}),
        ):
            with self.subTest(argument=argument):
                result = subprocess.run(
                    [str(Path("work/bin/mac-volume").resolve()), "--dry-run", argument],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertEqual(result.stderr, "")

    def test_invalid_arguments_fail_before_changing_volume(self) -> None:
        for arguments in ([], ["NaN"], ["1"], ["-31"], ["-15x"], ["--dry-run"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [str(Path("work/bin/mac-volume").resolve()), *arguments],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertNotEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
