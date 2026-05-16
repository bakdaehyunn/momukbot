import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shell_scripts_pass_syntax_check() -> None:
    for script in (
        ROOT / "scripts/setup.sh",
        ROOT / "scripts/install_launch_agent.sh",
        ROOT / "scripts/uninstall_launch_agent.sh",
        ROOT / "scripts/preflight_public.sh",
    ):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_setup_script_supports_dry_run() -> None:
    env = os.environ.copy()
    env["MOMUK_SETUP_DRY_RUN"] = "1"

    proc = subprocess.run(
        [str(ROOT / "scripts/setup.sh"), "--help"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "Creating virtual environment" in proc.stdout
    assert "momuk" in proc.stdout


def test_launch_agent_install_script_supports_dry_run(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["MOMUK_LAUNCHD_DRY_RUN"] = "1"
    env["HOME"] = str(tmp_path)

    proc = subprocess.run(
        [str(ROOT / "scripts/install_launch_agent.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "com.hennei.momukbot" in proc.stdout
    assert "momuk" in proc.stdout


def test_launch_agent_uninstall_script_supports_dry_run(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["MOMUK_LAUNCHD_DRY_RUN"] = "1"
    env["HOME"] = str(tmp_path)

    proc = subprocess.run(
        [str(ROOT / "scripts/uninstall_launch_agent.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "would remove" in proc.stdout
