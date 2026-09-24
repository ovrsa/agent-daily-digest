"""The same input must normalize to the same bytes, run after run.

Dictionary and set iteration order depends on `PYTHONHASHSEED` for strings, so
the driver runs under several seeds in a separate interpreter and the output is
compared byte for byte.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

DRIVER = Path(__file__).parent / "_determinism_driver.py"
SEEDS = ["0", "1", "424242"]


def run(seed: str) -> bytes:
    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8")
    completed = subprocess.run(
        [sys.executable, str(DRIVER)], capture_output=True, env=env, check=False
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return completed.stdout


@pytest.fixture(scope="module")
def outputs() -> list[bytes]:
    return [run(seed) for seed in SEEDS]


def test_the_output_is_not_empty(outputs: list[bytes]) -> None:
    assert outputs[0].strip()


def test_every_hash_seed_produces_the_same_bytes(outputs: list[bytes]) -> None:
    assert len(set(outputs)) == 1


def test_a_second_run_under_one_seed_produces_the_same_bytes() -> None:
    assert run(SEEDS[0]) == run(SEEDS[0])


def test_the_output_carries_a_normalized_article_and_a_state_file(outputs: list[bytes]) -> None:
    text = outputs[0].decode("utf-8")
    assert '"content_hash"' in text
    assert '"last_decision": "included"' in text
