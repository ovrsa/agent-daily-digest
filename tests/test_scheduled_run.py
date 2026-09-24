"""The scheduled entry point: `scripts/run-local.sh` and the launchd agent it runs under.

Each test copies the scripts into a temporary checkout, so nothing is written to
this repository's `logs/` and nothing is installed in `~/Library/LaunchAgents`.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "launchd").mkdir()
    shutil.copy2(ROOT / "scripts" / "run-local.sh", repo / "scripts" / "run-local.sh")
    for name in ("install.sh", "com.ovrsa.agent-daily-digest.plist.template"):
        shutil.copy2(ROOT / "launchd" / name, repo / "launchd" / name)
    return repo


def stub_python(tmp_path: Path, exit_code: int) -> Path:
    stub = tmp_path / "python"
    stub.write_text(f'#!/bin/sh\necho "pipeline args: $*"\nexit {exit_code}\n', encoding="utf-8")
    stub.chmod(0o755)
    return stub


@pytest.mark.parametrize("exit_code", [0, 1])
def test_the_run_script_passes_its_arguments_and_the_pipeline_exit_code(tmp_path: Path, exit_code: int) -> None:
    repo = checkout(tmp_path)
    env = {**os.environ, "DIGEST_PYTHON": str(stub_python(tmp_path, exit_code))}
    done = subprocess.run(
        ["/bin/bash", str(repo / "scripts" / "run-local.sh"), "--dry-run", "--max-articles", "3"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == exit_code
    expected = f"pipeline args: -m digest_pipeline --repo {repo} --dry-run --max-articles 3"
    assert expected in done.stdout
    (log,) = (repo / "logs").glob("run-*.log")
    assert expected in log.read_text(encoding="utf-8")


def test_the_run_script_stops_without_a_virtualenv(tmp_path: Path) -> None:
    repo = checkout(tmp_path)
    env = {**os.environ, "DIGEST_PYTHON": str(tmp_path / "missing" / "python")}
    done = subprocess.run(["/bin/bash", str(repo / "scripts" / "run-local.sh")], env=env, capture_output=True, text=True)
    assert done.returncode == 2 and "not found" in done.stderr


def test_the_run_script_keeps_logs_as_long_as_the_metrics(tmp_path: Path) -> None:
    repo = checkout(tmp_path)
    (repo / "logs").mkdir()
    old, recent = repo / "logs" / "run-2026-08-01.log", repo / "logs" / "run-2026-09-20.log"
    for path, days in ((old, 36), (recent, 5)):
        path.write_text("x\n", encoding="utf-8")
        stamp = path.stat().st_mtime - days * 86_400
        os.utime(path, (stamp, stamp))
    env = {**os.environ, "DIGEST_PYTHON": str(stub_python(tmp_path, 0))}
    subprocess.run(["/bin/bash", str(repo / "scripts" / "run-local.sh")], env=env, check=True, capture_output=True)
    assert not old.exists() and recent.exists()


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
def test_the_launchd_agent_runs_the_script_daily_with_an_explicit_environment(tmp_path: Path) -> None:
    repo = checkout(tmp_path)
    env = {**os.environ, "HOME": str(tmp_path / "home"), "DIGEST_PATH": "/opt/homebrew/bin:/usr/bin:/bin"}
    done = subprocess.run(["/bin/bash", str(repo / "launchd" / "install.sh"), "--print"], env=env, capture_output=True, check=True)
    assert re.search(rb"@[A-Z]+@", done.stdout) is None  # every placeholder was filled in
    plist = plistlib.loads(done.stdout)

    assert plist["Label"] == "com.ovrsa.agent-daily-digest"
    assert plist["ProgramArguments"] == ["/bin/bash", f"{repo}/scripts/run-local.sh"]
    assert plist["WorkingDirectory"] == str(repo)
    assert plist["StartCalendarInterval"] == {"Hour": 8, "Minute": 0}
    assert plist["RunAtLoad"] is False
    assert plist["EnvironmentVariables"] == {
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "USER": subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip(),
    }
    # The script logs for itself; launchd keeps only what fails before it can.
    assert plist["StandardOutPath"] == "/dev/null"
    assert plist["StandardErrorPath"] == f"{repo}/logs/launchd.log"
    # --print installs nothing.
    assert not (tmp_path / "home" / "Library").exists()


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
def test_paths_with_characters_special_to_sed_or_xml_are_written_as_they_are(tmp_path: Path) -> None:
    repo = checkout(tmp_path / "a b&c|d<e")
    home = tmp_path / "home & <away>|\\x"
    env = {**os.environ, "HOME": str(home)}
    done = subprocess.run(["/bin/bash", str(repo / "launchd" / "install.sh"), "--print"], env=env, capture_output=True, check=True)
    plist = plistlib.loads(done.stdout)
    assert plist["ProgramArguments"][1] == f"{repo}/scripts/run-local.sh"
    assert plist["WorkingDirectory"] == str(repo)
    assert plist["EnvironmentVariables"]["HOME"] == str(home)
    assert plist["StandardErrorPath"] == f"{repo}/logs/launchd.log"


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
def test_a_placeholder_the_installer_does_not_fill_stops_it(tmp_path: Path) -> None:
    repo = checkout(tmp_path)
    template = repo / "launchd" / "com.ovrsa.agent-daily-digest.plist.template"
    template.write_text(
        template.read_text(encoding="utf-8").replace("<key>RunAtLoad</key>", "<key>Extra</key>\n    <string>@NEW@</string>\n    <key>RunAtLoad</key>"),
        encoding="utf-8",
    )
    done = subprocess.run(["/bin/bash", str(repo / "launchd" / "install.sh"), "--print"], capture_output=True, text=True)
    assert done.returncode != 0 and "left unfilled" in done.stderr and done.stdout == ""
