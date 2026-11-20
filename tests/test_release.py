import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "nt", "Windows installation")
class ReleaseTests(unittest.TestCase):
    def test_install_start_and_reinstall_preserve_existing_files(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="build-loop-install-") as directory:
            root = Path(directory)
            command = ["powershell.exe", "-NoProfile", "-File", str(source / "install.ps1"), "-Root", str(root)]
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            launcher = root / "system/build-loop.cmd"
            started = subprocess.run([str(launcher), "start", "--root", str(root), "--name", "example",
                                      "--idea", "minimal example"], capture_output=True, timeout=20)
            self.assertEqual(started.returncode, 0, started.stderr.decode(errors="replace"))
            view = json.loads(started.stdout.decode("utf-8"))
            self.assertEqual(view["stage"], "brief")
            self.assertFalse(view["development_allowed"])
            self.assertIn("default_mode_request_user_input = true",
                          (root / "projects/example/.codex/config.toml").read_text(encoding="utf-8"))
            self.assertTrue((root / "projects/example/.agents/skills/build-loop/SKILL.md").is_file())
            marker = root / "projects/example/code/keep.txt"
            marker.write_text("preserve", encoding="utf-8")
            again = subprocess.run(command, capture_output=True, timeout=30)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
