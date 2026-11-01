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
