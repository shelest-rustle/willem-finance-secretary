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


class _FakeCreditConfig:
    credit_sheets = {"МТС Кредит Лики": "МТС Кредит Лики", "Tinkoff Кредит Лики": "Tinkoff Кредит Лики"}


class _FakeNoCreditConfig:
    credit_sheets: dict[str, str] = {}


def test_resync_credits_command_reports_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = []

    async def fake_resync(config) -> None:
        calls.append(config)

    monkeypatch.setattr(cli, "bootstrap", lambda: (_FakeCreditConfig(), "fake-texts"))
    monkeypatch.setattr(cli, "resync_credit_schedules", fake_resync)

    cli.main(["resync_credits"])

    assert len(calls) == 1
    assert "2 кредитов" in capsys.readouterr().out


def test_resync_credits_command_skips_when_no_credit_sheets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "bootstrap", lambda: (_FakeNoCreditConfig(), "fake-texts"))

    cli.main(["resync_credits"])

    assert "нечего синхронизировать" in capsys.readouterr().out
