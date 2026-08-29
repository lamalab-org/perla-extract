"""Contract tests for the Linux cron installer and its generated wrapper."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "setup-papersbot-cron.sh"


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell is unavailable")
def test_cron_setup_and_generated_wrapper_are_valid_shell():
    """Catch shell errors before an administrator runs the privileged installer."""

    shell = shutil.which("sh")
    assert shell is not None
    subprocess.run([shell, "-n", str(SCRIPT)], check=True)

    text = SCRIPT.read_text()
    marker = "cat >\"$TMP_DIR/run-perla-papersbot\" <<'EOF'\n"
    wrapper = text.split(marker, 1)[1].split("\nEOF\n", 1)[0]
    subprocess.run([shell, "-n"], input=wrapper, text=True, check=True)
    assert "--config -" in wrapper
    assert "curl -q" in wrapper
    assert "--retry-max-time 30" in wrapper
    assert "&& ! has_value ZOTERO_GROUP_ID" in text

    help_result = subprocess.run(
        [shell, str(SCRIPT), "--help"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "--release VERSION" in help_result.stdout
    assert "--checkout PATH" in help_result.stdout
