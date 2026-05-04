import importlib.util
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-accel.py"
SPEC = importlib.util.spec_from_file_location("check_accel", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load check-accel script from {SCRIPT_PATH}")
CHECK_ACCEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK_ACCEL)
build_command = CHECK_ACCEL.build_command


class CheckAccelTests(unittest.TestCase):
    def test_nvidia_command_uses_nvidia_smi_image(self) -> None:
        command = build_command(
            "nvidia",
            {
                "NVIDIA_CHECK_IMAGE": "nvidia/cuda:test-image",
            },
        )

        self.assertEqual(
            command,
            [
                "docker",
                "run",
                "--rm",
                "--gpus",
                "all",
                "nvidia/cuda:test-image",
                "nvidia-smi",
                "-L",
            ],
        )

    @mock.patch.object(CHECK_ACCEL.platform, "system", return_value="Windows")
    def test_amd_command_requires_linux_host(self, _platform_system: mock.Mock) -> None:
        with self.assertRaisesRegex(RuntimeError, "Linux host"):
            build_command("amd", {})

    @mock.patch.object(CHECK_ACCEL.platform, "system", return_value="Windows")
    def test_vulkan_command_requires_linux_host(self, _platform_system: mock.Mock) -> None:
        with self.assertRaisesRegex(RuntimeError, "Linux host"):
            build_command("vulkan", {})


if __name__ == "__main__":
    unittest.main()