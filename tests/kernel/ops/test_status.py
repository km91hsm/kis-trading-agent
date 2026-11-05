"""kernel/ops/status: STATUS.md·텔레그램 /status 커널 절 렌더."""

from dataclasses import replace
from datetime import UTC, datetime

from trader.kernel.ledger import runs
from trader.kernel.notify.commands import CommandContext
from trader.kernel.ops import health, status
from trader.kernel.registry.types import Feature, StatusSection
from trader.kernel.session import calls

NOW = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)  # 09:00 KST

TODO = """# 체크리스트
## Phase 0 골격
- [x] 하나
- [ ] 둘
- [ ] 완료 기준: 셋
## Phase 1 브로커
- [ ] 넷
"""


class _Sent:
    def send(self, text, *, escape=True):
        return True


def _ctx(conn, project, features=()):
    return CommandContext(
        conn=conn,
        settings=None,
        state=project / "state",
        now=lambda: NOW,
        notifier=_Sent(),
        features=list(features),
    )


def _feat(*sections):
    return Feature(id="demo", tier="service", status_sections=tuple(sections))


def test_todo_progress_counts(project):
    p = project / "docs" / "TODO.md"
    p.write_text(TODO, encoding="utf-8")
    assert status.todo_progress(p) == {"Phase 0 골격": (1, 3), "Phase 1 브로커": (0, 1)}
    assert status.next_todos(p) == ["둘", "완료 기준: 셋", "넷"]
    assert status.next_todos(p, limit=1) == ["둘"]


def test_todo_progress_missing_file(project):
    missing = project / "docs" / "없음.md"
    assert status.todo_progress(missing) == {} and status.next_todos(missing) == []


def test_build_status_sections(conn, project):
    p = project / "docs" / "TODO.md"
    p.write_text(TODO, encoding="utf-8")
    rid = runs.start(conn, "analyze", "KR")
    runs.finish(conn, rid, "ok")
    for model, cost in (("opus", "0.25"), ("opus", "0.10"), (calls.FAKE_MODEL, "9")):
        conn.execute(
            "INSERT INTO agent_calls(agent, model, status, cost_usd, created_at) "
            "VALUES ('orchestrator', ?, 'ok', ?, ?)",
            (model, cost, NOW.isoformat()),
        )
    conn.commit()
    health.store(
        conn,
        {"level": "warning", "label": "주의", "at": NOW.isoformat(), "areas": {},
         "items": [{"key": "jobs.x", "level": "warning", "text": f"항목{i}"} for i in range(5)],
         "actions": []},
    )  # fmt: skip
    feat = _feat(StatusSection("계좌", lambda c: ["실행 중"]), StatusSection("빈 절", lambda c: []))
    text = status.render_markdown(_ctx(conn, project, [feat]), todo_path=p)
    assert text.startswith("# 시스템 상태 (생성: 2026-10-09 09:00 KST)")
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert heads == [
        "## 운영 점검", "## 데몬", "## 마지막 실행", "## 오늘 LLM 호출", "## 계좌",
        "## 구현 진행도", "## 다음 할 일",
    ]  # fmt: skip
    assert "점검 주의 · 5건 · 09:00" in text and "- 항목2" in text and "항목3" not in text
    assert "데몬: 꺼짐" in text
    assert "analyze KR" in text and "→ ok" in text
    assert "- 2건, $0.35" in text  # fake 제외, Decimal 합
    assert "실행 중" in text
    assert "Phase 0 골격: 1/3" in text and "- 둘" in text
    assert len(text) <= status.MAX_CHARS


def test_build_status_truncates(conn, project):
    p = project / "docs" / "TODO.md"
    p.write_text("## Phase 0 x\n" + "- [ ] " + "가" * 9000 + "\n", encoding="utf-8")
    text = status.render_markdown(_ctx(conn, project), todo_path=p)
    assert len(text) <= status.MAX_CHARS and text.endswith("\n")


def test_write_status_default_path(project):
    out = status.write_status("안녕\n")
    assert out == project / "state" / "STATUS.md" and out.read_text(encoding="utf-8") == "안녕\n"


def test_sections_isolate_failures(conn, project):
    feat = _feat(
        StatusSection("A", lambda c: ["- a"]),
        StatusSection("B", lambda c: 1 / 0),
        StatusSection("C", lambda c: ["- c"]),
    )
    out = status.sections(_ctx(conn, project, [feat]), kernel=())
    assert out[0] == ("A", ["- a"]) and out[2] == ("C", ["- c"])
    assert out[1][0] == "B" and out[1][1][0].startswith("조회 실패(ZeroDivisionError: ")


def test_telegram_lines_have_kernel_first(conn, project):
    assert [s.title for s in status.KERNEL_SECTIONS] == [
        "운영 점검", "데몬", "마지막 실행", "오늘 LLM 호출",
    ]  # fmt: skip
    assert status.TELEGRAM_KERNEL_SECTIONS == status.KERNEL_SECTIONS[:2]
    feat = _feat(
        StatusSection("계좌", lambda c: ["실행 중 · 계좌 모드: p 자동"]),
        StatusSection("빈 절", lambda c: []),
        StatusSection("B", lambda c: 1 / 0),
    )
    lines = status.telegram_lines(_ctx(conn, project, [feat]))
    # 비어 있지 않은 절마다 `[제목]` 한 줄을 앞세운다(실패 절 포함) — T7-R4
    assert lines[:4] == ["[운영 점검]", "점검 기록 없음", "[데몬]", "데몬: 꺼짐"]
    assert lines[4:6] == ["[계좌]", "실행 중 · 계좌 모드: p 자동"]
    assert lines[6] == "[B]" and lines[7].startswith("- B: 조회 실패(ZeroDivisionError")
    assert len(lines) == 8  # 빈 절 생략, 마지막 실행·LLM 호출 절은 텔레그램에 없음


def test_markdown_includes_feature_sections(conn, project):
    """discover() 전체 기능의 절이 예외 없이 STATUS.md 에 들어간다(빈 원장·빈 state)."""
    import yaml

    from trader.kernel.cli.boot import build_registry
    from trader.kernel.config.loader import load
    from trader.kernel.registry import discover

    (project / "config" / "accounts.yaml").write_text(
        yaml.safe_dump({"accounts": [
            {"id": "paper-main", "env": "paper", "cano": "0", "markets": ["KR"], "mode": "auto"},
        ]}),
        encoding="utf-8",
    )  # fmt: skip
    features = discover()
    settings = load("settings", build_registry(features), path=project / "config" / "settings.yaml")
    ctx = replace(_ctx(conn, project, features), settings=settings)
    text = status.render_markdown(ctx, todo_path=None)
    assert "조회 실패" not in text
    for head in ("## 최근 결정", "## 오늘 스캔", "## ML 모델", "## 국면", "## 계좌"):
        assert head in text, head
    assert "## 미승인 주문" not in text  # 미승인 없음 → 절 생략
    assert "- paper-main · paper · KR · auto" in text
