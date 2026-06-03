from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import read_feedback, record_feedback


def _patch_settings(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("clawhum_core.settings.get_settings", lambda: _S())
    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb


def _seed(fb):
    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t2", 0.4, -1)


def test_feedback_delete_fail_on_empty_exits_2_when_no_match(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    res = runner.invoke(
        app,
        ["feedback-delete", "--query-id", "q-nope", "--yes", "--fail-on-empty"],
    )
    assert res.exit_code == 2, res.output
    # feedback log untouched
    assert len(read_feedback(fb)) == 2


def test_feedback_delete_fail_on_empty_short_flag(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    res = runner.invoke(
        app,
        ["feedback-delete", "--track-id", "t-nope", "--yes", "-E"],
    )
    assert res.exit_code == 2, res.output
    assert len(read_feedback(fb)) == 2


def test_feedback_delete_fail_on_empty_zero_when_match(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    res = runner.invoke(
        app,
        ["feedback-delete", "--query-id", "q1", "--yes", "--fail-on-empty"],
    )
    assert res.exit_code == 0, res.output
    assert len(read_feedback(fb)) == 1


def test_feedback_delete_fail_on_empty_default_off(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    res = runner.invoke(
        app,
        ["feedback-delete", "--query-id", "q-nope", "--yes"],
    )
    # no --fail-on-empty: empty match remains a successful no-op
    assert res.exit_code == 0, res.output
    assert len(read_feedback(fb)) == 2


def test_feedback_delete_fail_on_empty_honoured_for_dry_run(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    res = runner.invoke(
        app,
        ["feedback-delete", "--query-id", "q-nope", "--dry-run", "--fail-on-empty"],
    )
    assert res.exit_code == 2, res.output
    assert len(read_feedback(fb)) == 2
