import json
import os
from datetime import UTC, datetime
from pathlib import Path

from trader.features.saveticker.paths import home as st_home


def test_home_follows_reader_config(project):
    """수집기가 쓰는 곳 = 리더가 읽는 곳(`saveticker.dir`). 설정이 없으면 기본 폴더."""
    assert st_home() == project / "state" / "external" / "saveticker"
    other = project / "elsewhere"
    src = Path(__file__).resolve().parents[3] / "templates" / "config" / "sources.yaml"
    text = src.read_text(encoding="utf-8").replace(
        "dir: state/external/saveticker", f"dir: {other}"
    )
    (project / "config" / "sources.yaml").write_text(text, encoding="utf-8")
    assert st_home() == other


def _capture(items, when, stream="news_all"):
    from trader.features.saveticker.models import Capture

    return Capture(
        stream=stream,
        page_url="https://www.saveticker.com/news",
        observed_at=when,
        items=items,
    )


def test_news_views_change_is_not_a_new_revision(tmp_path):
    """뉴스는 조회수만 바뀐 행을 새 버전으로 쓰지 않는다(하루 1만 줄 중 대부분이 조회수 변경이었다).
    커뮤니티·순위는 그대로 새 버전."""
    from trader.features.saveticker.store import Store

    store = Store(tmp_path)
    item = {"id": "n1", "title": "제목", "views": "10"}
    assert store.save(_capture([item], "2026-10-25T00:00:00+00:00"))["changed"] == 1
    assert (
        store.save(_capture([item | {"views": "99"}], "2026-10-25T00:01:00+00:00"))["changed"] == 0
    )
    assert (
        store.save(_capture([item | {"title": "정정"}], "2026-10-25T00:02:00+00:00"))["changed"]
        == 1
    )
    row = {"id": "c1", "title": "QQQ", "views": "1"}
    assert (
        store.save(_capture([row], "2026-10-25T00:00:00+00:00", "community_top5"))["changed"] == 1
    )
    assert (
        store.save(_capture([row | {"views": "2"}], "2026-10-25T00:01:00+00:00", "community_top5"))[
            "changed"
        ]
        == 1
    )


def test_prune_keeps_latest_and_recent(tmp_path):
    from trader.features.saveticker.store import Store

    store = Store(tmp_path)
    old, new = "2026-09-01T00:00:00+00:00", "2026-10-25T00:00:00+00:00"
    store.save(_capture([{"id": "a", "title": "옛"}, {"id": "b", "title": "b1"}], old))
    store.save(_capture([{"id": "b", "title": "b2"}], new))
    (tmp_path / "attachments").mkdir(exist_ok=True)
    pdf, txt = tmp_path / "attachments" / "x.pdf", tmp_path / "attachments" / "x.txt"
    pdf.write_bytes(b"%PDF")
    txt.write_text("발췌", encoding="utf-8")
    stamp = datetime(2026, 9, 1, tzinfo=UTC).timestamp()
    os.utime(pdf, (stamp, stamp))
    out = store.prune(today=datetime(2026, 10, 27, tzinfo=UTC).date(), keep_days=30)
    assert out["revisions"] == 2  # 09-01 의 a·b1 (같은 기사가 다시 보이면 새 버전 한 줄로 돌아온다)
    assert out["captures"] == 1 and out["day_dirs"] == 1 and out["pdfs"] == 1
    assert not (tmp_path / "2026-09-01").exists() and (tmp_path / "2026-10-25").exists()
    assert txt.exists() and not pdf.exists()  # 추출 텍스트는 작아서 남긴다
    assert json.loads((tmp_path / "latest" / "news_all.json").read_text(encoding="utf-8"))["items"]
    assert store.prune(today=datetime(2026, 10, 27, tzinfo=UTC).date(), keep_days=30) == {
        "revisions": 0,
        "captures": 0,
        "day_dirs": 0,
        "pdfs": 0,
    }


def test_prune_keeps_last_good_capture_and_latest_files(tmp_path):
    """마지막 캡처가 실패여도 그 전의 마지막 성공 화면은 남긴다 — latest/<stream>.json 의 원본."""
    from trader.features.saveticker.models import Capture
    from trader.features.saveticker.store import Store

    store = Store(tmp_path)
    store.save(_capture([{"id": "a", "title": "성공"}], "2026-09-01T00:00:00+00:00"))
    store.save(_capture([{"id": "b", "title": "더 옛 성공"}], "2026-09-01T00:00:01+00:00"))
    failed = Capture(
        stream="news_all",
        page_url="https://www.saveticker.com/news",
        observed_at="2026-09-02T00:00:00+00:00",
        items=[],
        status="error",
        reason="TimeoutError",
    )
    store.save(failed)
    out = store.prune(today=datetime(2026, 10, 27, tzinfo=UTC).date(), keep_days=30)
    assert out["captures"] == 1  # 첫 성공만 지운다(마지막 성공·마지막 캡처는 남김)
    store.export(incremental=True)
    latest = json.loads((tmp_path / "latest" / "news_all.json").read_text(encoding="utf-8"))
    assert latest["items"][0]["title"] == "더 옛 성공"
    assert store.status()["streams"]["news_all"]["status"] == "error"
