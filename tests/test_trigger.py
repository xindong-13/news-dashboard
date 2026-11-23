"""出刊觸發：外部觸發（--target）、GitHub 排程備援（--due）、手動，以及 edition.yml 的設定。"""

import os
import shutil
import subprocess

import pytest
import yaml

import pipeline.main as m
from pipeline.config import ROOT
from pipeline.models import Edition, TimeWindow


@pytest.fixture
def run(tmp_path, monkeypatch):
    """跑 main()，把真正的抓取換掉；built 記錄實際出了哪些刊。"""
    built: list[str] = []

    def fake_build(plan, settings, sources, data_dir, **kw):
        built.append(plan.edition_id)
        return Edition(
            edition_id=plan.edition_id,
            edition_type=plan.edition_type,
            published_at=plan.now,
            window=TimeWindow(from_=plan.window_from, to=plan.now),
        )

    monkeypatch.setattr(m, "build_edition", fake_build)
    monkeypatch.setattr(m, "load_dotenv", lambda *a, **k: None)

    def _run(now: str, *args: str) -> int:
        return m.main(["--now", f"{now}:00+08:00", "--data-dir", str(tmp_path), *args])

    _run.built = built
    _run.files = lambda: sorted(p.name for p in (tmp_path / "editions").glob("*.json"))
    _run.path = lambda eid: tmp_path / "editions" / f"{eid}.json"
    return _run


def test_repeated_triggers_publish_once(run):
    for now, args in [
        ("2026-11-24T11:50", ["--target", "2026-11-24-noon"]),
        ("2026-11-24T11:51", ["--target", "2026-11-24-noon"]),  # 同一個排程被觸發兩次
        ("2026-11-24T12:05", ["--target", "2026-11-24-noon"]),  # 補觸發
        ("2026-11-24T13:20", ["--due"]),  # GitHub 排程
    ]:
        assert run(now, *args) == 0
    assert run.built == ["2026-11-24-noon"]


@pytest.mark.parametrize("args", [["--target", "2026-11-24-noon"], ["--due"]])
def test_auto_trigger_never_touches_published_file(run, args):
    path = run.path("2026-11-24-noon")
    path.parent.mkdir(parents=True)
    path.write_text("原本的內容", encoding="utf-8")
    assert run("2026-11-24T12:05", *args) == 0
    assert run.built == []
    assert path.read_text(encoding="utf-8") == "原本的內容"


@pytest.mark.parametrize(
    ("now", "target"),
    [
        ("2026-11-24T20:01", "2026-11-24-noon"),  # 午報拖到晚上，晚報時段前
        ("2026-11-24T21:43", "2026-11-24-noon"),  # 午報拖到晚報時段
        ("2026-11-25T00:05", "2026-11-24-noon"),  # 午報拖過午夜
        ("2026-11-25T00:05", "2026-11-24-night"),  # 晚報拖過午夜
        ("2026-11-25T12:00", "2026-11-24-noon"),  # 拖了一整天，正好落在隔天午報時段
        ("2026-11-25T12:00", "2026-11-24-night"),
        ("2026-11-24T09:00", "2026-11-24-noon"),  # 太早
        ("2026-11-24T12:00", "2026-11-25-noon"),  # 指定未來的刊
    ],
)
def test_target_outside_its_window_is_skipped(run, now, target):
    assert run(now, "--target", target) == 0
    assert run.built == []


def test_target_wins_over_edition_choice(run):
    assert run("2026-11-24T21:00", "--edition", "noon", "--target", "2026-11-24-night") == 0
    assert run.built == ["2026-11-24-night"]


def test_late_github_schedule_fills_the_missing_edition(run):
    # 11/23 實際發生：午報排程 21:43 才跑。晚報還沒出就補晚報，日期正確
    assert run("2026-11-23T21:43", "--due") == 0
    assert run.built == ["2026-11-23-night"]


@pytest.mark.parametrize("now", ["2026-11-23T20:01", "2026-11-24T00:05", "2026-11-24T03:59"])
def test_github_schedule_outside_windows_does_nothing(run, now):
    assert run(now, "--due") == 0
    assert run.built == []


def test_manual_run_still_overwrites(run):
    assert run("2026-11-24T11:50", "--target", "2026-11-24-noon") == 0
    before = run.path("2026-11-24-noon").read_text(encoding="utf-8")
    assert run("2026-11-24T14:00", "--edition", "noon") == 0
    assert run("2026-11-24T14:30", "--edition", "auto") == 0
    assert run.built == ["2026-11-24-noon"] * 3
    assert run.files() == ["2026-11-24-noon.json"]
    assert run.path("2026-11-24-noon").read_text(encoding="utf-8") != before


def test_manual_run_after_midnight_does_not_block_todays_editions(run):
    run("2026-11-24T00:30", "--edition", "auto")
    run("2026-11-24T11:50", "--target", "2026-11-24-noon")
    run("2026-11-24T20:50", "--target", "2026-11-24-night")
    assert run.built == ["2026-11-23-night", "2026-11-24-noon", "2026-11-24-night"]


@pytest.mark.parametrize(
    "bad", ["noon", "2026-11-24", "2026-11-24-noon; rm -rf /", "../x-noon", "2026-11-24-noon\n"]
)
def test_bad_target_is_an_error(run, bad):
    with pytest.raises(SystemExit) as e:
        run("2026-11-24T12:00", "--target", bad)
    assert e.value.code == 2


# ---- edition.yml ----


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/edition.yml").read_text(encoding="utf-8"))


def _steps(workflow):
    return workflow["jobs"]["edition"]["steps"]


def test_workflow_checks_out_latest_branch(workflow):
    # 排隊中的執行，github.sha 是觸發當時的版本，看不到前一次剛出的刊
    checkout = _steps(workflow)[0]
    assert checkout["uses"].startswith("actions/checkout")
    assert checkout["with"]["ref"] == "${{ github.ref_name }}"
    assert workflow["concurrency"] == {"group": "edition", "cancel-in-progress": False}


def test_workflow_does_not_interpolate_inputs_into_shell(workflow):
    for step in _steps(workflow):
        assert "${{ inputs" not in step.get("run", "")


def test_line_push_only_when_a_new_edition_was_committed(workflow):
    for job in ("deploy", "notify"):
        assert workflow["jobs"][job]["if"] == "needs.edition.outputs.changed == 'true'"


def _find_bash() -> str | None:
    """Windows 上要用 Git Bash，System32 的 bash 是 WSL。"""
    if os.name == "nt":
        path = r"C:\Program Files\Git\bin\bash.exe"
        return path if os.path.exists(path) else None
    return shutil.which("bash")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"EVENT": "schedule"}, "--due"),
        ({"EVENT": "workflow_dispatch", "EDITION": "auto"}, "--edition auto"),
        ({"EVENT": "workflow_dispatch", "EDITION": "night"}, "--edition night"),
        (
            {"EVENT": "workflow_dispatch", "EDITION": "noon", "NO_LLM": "true"},
            "--edition noon --no-llm",
        ),
        (
            {"EVENT": "workflow_dispatch", "EDITION": "auto", "TARGET": "2026-11-24-noon"},
            "--target 2026-11-24-noon",
        ),
    ],
)
def test_workflow_arguments(workflow, env, expected):
    """實際執行 edition.yml 裡那段 shell，把 uv 換成印出參數。"""
    bash = _find_bash()
    if bash is None:
        pytest.skip("沒有 bash")
    step = next(s for s in _steps(workflow) if s.get("name") == "產生一刊")
    assert {"EVENT", "EDITION", "NO_LLM", "TARGET"} <= set(step["env"])
    script = 'uv() { echo "$*"; }\n' + step["run"]
    base = {**os.environ, "EVENT": "", "EDITION": "", "NO_LLM": "", "TARGET": ""}
    out = subprocess.run(
        [bash, "-e", "-c", script],
        env={**base, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert out.stdout.strip() == f"run python -m pipeline.main {expected}"
