from trader.kernel.platform.fsutil import atomic_replace


def test_atomic_replace_moves_and_removes_source(tmp_path):
    src, dst = tmp_path / "a.tmp", tmp_path / "a.json"
    dst.write_text("old", encoding="utf-8")
    src.write_text("new", encoding="utf-8")
    atomic_replace(src, dst)
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()


def test_atomic_replace_retries_permission_error(tmp_path, monkeypatch):
    import pytest

    from trader.kernel.platform import fsutil

    src, dst = tmp_path / "a.tmp", tmp_path / "a"
    src.write_text("x", encoding="utf-8")
    real = fsutil.os.replace
    n = {"k": 0}

    def flaky(a, b):
        n["k"] += 1
        if n["k"] < 3:
            raise PermissionError("공유 위반")
        real(a, b)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    fsutil.atomic_replace(src, dst, delay_s=0)
    assert dst.read_text(encoding="utf-8") == "x" and n["k"] == 3
    src.write_text("y", encoding="utf-8")
    monkeypatch.setattr(fsutil.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        fsutil.atomic_replace(src, dst, attempts=2, delay_s=0)
