from trader.kernel.platform.fsutil import atomic_replace


def test_atomic_replace_moves_and_removes_source(tmp_path):
    src, dst = tmp_path / "a.tmp", tmp_path / "a.json"
    dst.write_text("old", encoding="utf-8")
    src.write_text("new", encoding="utf-8")
    atomic_replace(src, dst)
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()
