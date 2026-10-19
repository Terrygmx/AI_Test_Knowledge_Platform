"""Phase 5 CLI 入口的离线测试。"""

from __future__ import annotations

import sys

import pytest

from scripts import ingest


def test_rejects_missing_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["ingest.py", "table", str(tmp_path / "missing.csv")],
    )
    with pytest.raises(SystemExit) as exc:
        ingest.main()
    assert exc.value.code == 2
