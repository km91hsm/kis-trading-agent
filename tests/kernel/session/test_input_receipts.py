"""원문 없이 최종 컨텍스트와 일치하는 전달용 응답 기록을 남긴다."""

import hashlib
import json


def test_receipt_describes_exact_context_without_copying_text(tmp_path):
    from trader.kernel.session import input_receipts

    context = {
        "news": {
            "data": {
                "stock": [
                    {
                        "id": 8,
                        "title": "합성 제목",
                        "body": "원문을 복제하지 않는다",
                        "url": "https://example.test/8",
                    }
                ]
            }
        },
        "truncation": {"truncated": True},
    }
    expected = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert input_receipts.record(
        tmp_path,
        context,
        stage="researcher:r0:bull",
        provider=None,
        recorded_at="2026-11-04T01:00:00Z",
    )
    files = list((tmp_path / "input_receipts").glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text())
    assert body["context_hash"] == expected
    assert body["provider"] is None
    assert body["groups"][0]["count"] == 1
    assert body["groups"][0]["refs"] == ["news:8", "https://example.test/8"]
    assert body["truncated"] is True
    assert "합성 제목" not in files[0].read_text()
    assert "원문을 복제" not in files[0].read_text()
    before = files[0].read_bytes()
    assert input_receipts.record(
        tmp_path / "other", {}, stage="trader", provider=None, recorded_at="2026-11-05T01:00:00Z"
    )
    assert files[0].read_bytes() == before


def test_receipt_failure_is_nonfatal_and_visible(tmp_path, caplog):
    from trader.kernel.session import input_receipts

    (tmp_path / "input_receipts").write_text("blocked")
    assert (
        input_receipts.record(
            tmp_path, {}, stage="trader", provider=None, recorded_at="2026-11-04T01:00:00Z"
        )
        is False
    )
    assert "input_receipt_unrecorded" in caplog.text


def test_receipt_keeps_per_source_counts_without_source_text(tmp_path):
    from trader.kernel.session import input_receipts

    context = {
        "news": {
            "data": {
                "stock": [
                    {"id": 1, "source": "fixture-a", "title": "합성 제목 A"},
                    {"id": 2, "source": "fixture-b", "title": "합성 제목 B"},
                    {"id": 3, "source": "fixture-a", "title": "합성 제목 C"},
                ]
            }
        }
    }
    input_receipts.record(
        tmp_path, context, stage="trader", provider=None, recorded_at="2026-11-04T01:00:00Z"
    )
    receipt = json.loads(next((tmp_path / "input_receipts").glob("*.json")).read_text())
    assert receipt["groups"][0]["source_counts"] == {"fixture-a": 2, "fixture-b": 1}
