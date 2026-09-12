from __future__ import annotations

import pytest

from willem import cli


def test_resync_command_reports_synced_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "bootstrap", lambda: ("fake-config", "fake-texts"))
    monkeypatch.setattr(cli, "resync_owner_unsynced", lambda config: 3)

    cli.main(["resync"])

    assert "Синхронизировано операций: 3" in capsys.readouterr().out


def test_main_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        cli.main([])
