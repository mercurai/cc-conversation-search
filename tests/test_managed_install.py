"""Integration tests for the pinned managed-checkout installer mode."""

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path(r"C:\Program Files\Git\usr\bin\bash.exe")


def run_installer(tmp_path, *args):
    env = os.environ.copy()
    env["HOME"] = tmp_path.as_posix()
    env["USERPROFILE"] = str(tmp_path)
    return subprocess.run(
        [str(GIT_BASH), str(REPO_ROOT / "install.sh"), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_managed_checkout_dry_run_installs_exact_current_checkout_without_git_network(tmp_path):
    result = run_installer(tmp_path, "--managed-checkout", "--dry-run")

    assert result.returncode == 0, result.stderr
    output = result.stdout.replace("\\", "/").lower()
    expected_checkout = str(REPO_ROOT).replace("\\", "/").lower()
    assert f"managed checkout: {expected_checkout}" in output
    assert f"marketplace plugin source: {expected_checkout}" in output
    assert "uv tool install --force" in output
    assert expected_checkout in output
    assert "git clone" not in output
    assert "git fetch" not in output
    assert "git pull" not in output
    assert "would re-exec" not in output
    assert "codex plugin add cc-conversation-search@mercurai-local-plugins" in output
    assert "would install managed skill" not in output


def test_default_dry_run_keeps_canonical_checkout_redirect_behavior(tmp_path):
    result = run_installer(tmp_path, "--dry-run")

    assert result.returncode == 0, result.stderr
    output = result.stdout.lower()
    assert "installing repo into canonical plugin path" in output
    assert "git clone" in output
    assert "would re-exec" in output


def test_managed_checkout_leaves_direct_global_skill_paths_unmanaged(tmp_path):
    skill_target = tmp_path / ".agents" / "skills" / "claude-session-miner"
    skill_target.parent.mkdir(parents=True)
    skill_target.write_text("do not overwrite", encoding="utf-8")

    result = run_installer(tmp_path, "--managed-checkout", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert skill_target.read_text(encoding="utf-8") == "do not overwrite"
    assert "would install managed skill" not in result.stdout.lower()
