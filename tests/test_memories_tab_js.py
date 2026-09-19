"""The Memories tab's script, driven in node against a fake page.

The scenarios live in ``memories_tab_sim.js``: a first load that fails and
what a later click on the tab keeps, the status line while proposals are
being made, when typed text counts as unsaved, and what the status line says
with no memories. Nothing here touches the network; the dashboard's API is a
function in the simulation. Skipped where node is not installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).parent.parent / "src/job_scout/web/static"
SIMULATION = Path(__file__).parent / "memories_tab_sim.js"
NODE = shutil.which("node")


def _node_major() -> int:
    """Return the major version of the installed node.

    Returns:
        Such as 10 or 20.
    """
    assert NODE is not None
    version = subprocess.run(
        [NODE, "--version"], capture_output=True, text=True, check=True
    ).stdout
    return int(version.strip().lstrip("v").split(".")[0])


def _script_for_node(tmp_path: Path) -> Path:
    """Write the tab's script where node can read it, older syntax spelled out.

    Node before 14 does not read ``??`` or ``?.``; the script only uses them
    where ``||`` and ``.`` do the same for these scenarios.

    Args:
        tmp_path: Where to write the copy.

    Returns:
        The path of the script to run.
    """
    script = (STATIC / "memories.js").read_text(encoding="utf-8")
    if _node_major() < 14:
        script = script.replace("??", "||").replace("?.", ".")
    target = tmp_path / "memories.js"
    target.write_text(script, encoding="utf-8")
    return target


def test_the_capture_switch_starts_disabled_on_the_page() -> None:
    """Until the server has answered, the switch shows no guessed state."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    switch = re.search(r'<input type="checkbox" id="memory-auto"[^>]*>', page)
    assert switch is not None
    assert "disabled" in switch.group(0)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_tab_keeps_work_and_says_what_it_is_doing(tmp_path: Path) -> None:
    """Every scenario in the simulation passes."""
    assert NODE is not None
    result = subprocess.run(
        [NODE, str(SIMULATION), str(_script_for_node(tmp_path))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    assert result.stdout.count("PASS") >= 12
