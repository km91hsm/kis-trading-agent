import pytest


def test_pdf_original_is_hashed_and_kept_even_when_no_text(tmp_path):
    PdfWriter = pytest.importorskip("pypdf").PdfWriter
    from trader.features.saveticker.reports import save_pdf

    source = tmp_path / "download"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(source)
    root = tmp_path / "feed"
    result = save_pdf(source, root)
    assert (root / result.pdf_path).read_bytes() == source.read_bytes()
    assert result.size_bytes == source.stat().st_size and result.pages == 1
    assert len(result.sha256) == 64
    assert result.text_chars == 0 and result.text_path is None
    assert save_pdf(source, root) == result


def test_non_pdf_and_oversized_downloads_are_not_imported(tmp_path):
    pytest.importorskip("pypdf")
    from trader.features.saveticker.reports import save_pdf

    source = tmp_path / "download"
    source.write_text("<html>로그인 필요</html>", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_pdf"):
        save_pdf(source, tmp_path / "feed")
    source.write_bytes(b"%PDF-" + b"0" * 100)
    with pytest.raises(ValueError, match="pdf_size_limit"):
        save_pdf(source, tmp_path / "feed", max_bytes=20)
    assert not list((tmp_path / "feed").glob("**/*.pdf"))


def test_report_cards_are_not_mistaken_for_empty_news():
    from trader.features.saveticker.dom import extract

    html = """<h2>데일리 리포트</h2><div><div><span>SAVE REPORT</span><span>4일 전</span></div>
      <p>2026년 10월 20일 (화)</p><div><button aria-label="PDF 다운로드">PDF</button></div></div>"""
    rows = extract(html, "news_reports")
    assert len(rows) == 1
    assert rows[0].title == "2026년 10월 20일 (화)"
    assert (
        rows[0].kind == "report"
        and rows[0].report_date == "2026-10-20"
        and rows[0].published_at is None
    )
    assert rows[0].detail_status == "pending"
