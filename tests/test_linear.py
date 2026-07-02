import pytest

from tripwire.cli import state
from tripwire.cli.main import main
from tripwire.integrations import linear

TEAMS = {"teams": {"nodes": [{"id": "team-1", "key": "ENG", "name": "Engineering"}]}}


@pytest.fixture(autouse=True)
def in_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.delenv("LINEAR_TEAM", raising=False)


@pytest.fixture
def fake_api(monkeypatch):
    calls = []

    def _post(query, variables, api_key):
        calls.append((query, variables, api_key))
        if "teams" in query:
            return TEAMS
        return {
            "issueCreate": {
                "success": True,
                "issue": {"identifier": "ENG-42", "url": "https://linear.app/x/issue/ENG-42"},
            }
        }

    monkeypatch.setattr(linear, "_post", _post)
    return calls


def write_report(name="20260101-000000-boom"):
    reports = state.STATE_DIR / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{name}.md"
    path.write_text("# Checkout 500 on confirm\n\n## Steps to reproduce\n1. click Pay\n")
    return path


class TestResolveTeamId:
    def test_sole_team_needs_no_key(self, fake_api):
        assert linear.resolve_team_id("k") == "team-1"

    def test_matches_key_case_insensitive(self, fake_api):
        assert linear.resolve_team_id("k", "eng") == "team-1"

    def test_unknown_team_lists_options(self, fake_api):
        with pytest.raises(linear.LinearError, match="ENG"):
            linear.resolve_team_id("k", "OPS")

    def test_multiple_teams_require_choice(self, monkeypatch):
        nodes = [
            {"id": "t1", "key": "ENG", "name": "Engineering"},
            {"id": "t2", "key": "OPS", "name": "Operations"},
        ]
        monkeypatch.setattr(linear, "_post", lambda *a: {"teams": {"nodes": nodes}})
        with pytest.raises(linear.LinearError, match="--team"):
            linear.resolve_team_id("k")
        assert linear.resolve_team_id("k", "Operations") == "t2"


class TestFileCommand:
    def test_files_newest_report(self, fake_api, capsys):
        write_report("20260101-000000-old")
        write_report("20260102-000000-new")
        assert main(["file"]) == 0
        assert "ENG-42 https://linear.app/x/issue/ENG-42" in capsys.readouterr().out
        _, variables, api_key = fake_api[-1]
        assert api_key == "lin_api_test"
        assert variables["input"]["teamId"] == "team-1"
        assert variables["input"]["title"] == "Checkout 500 on confirm"
        assert variables["input"]["description"].startswith("## Steps to reproduce")

    def test_explicit_path_and_team(self, fake_api):
        path = write_report()
        assert main(["file", str(path), "--team", "ENG"]) == 0

    def test_missing_key(self, monkeypatch, capsys):
        monkeypatch.delenv("LINEAR_API_KEY")
        assert main(["file"]) == 1
        assert "LINEAR_API_KEY" in capsys.readouterr().err

    def test_no_reports(self, fake_api, capsys):
        assert main(["file"]) == 1
        assert "no saved reports" in capsys.readouterr().err

    def test_missing_report_path(self, fake_api, capsys):
        assert main(["file", "nope.md"]) == 1
        assert "report not found" in capsys.readouterr().err

    def test_api_error_is_reported(self, monkeypatch, capsys):
        write_report()

        def _boom(*args):
            raise linear.LinearError("Authentication required")

        monkeypatch.setattr(linear, "_post", _boom)
        assert main(["file"]) == 1
        assert "Authentication required" in capsys.readouterr().err
