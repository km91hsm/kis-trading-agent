import importlib

from trader.features.saveticker import api

ALLOWED_MODULES = {"trader.features.saveticker.reader", "trader.features.saveticker.paths",
                   "trader.features.saveticker.settings"}  # fmt: skip


def test_api_names_import_and_expose_only_public_modules():
    for name in api.__all__:
        obj = getattr(api, name)
        mod = getattr(obj, "__module__", None)
        if mod and mod.startswith("trader.features.saveticker"):
            assert mod in ALLOWED_MODULES, f"{name} 은 내부 모듈 {mod} 에서 왔다"
    for needed in ("read_news_items", "report_excerpt", "snapshot", "load_cfg", "home"):
        assert needed in api.__all__
    importlib.import_module("trader.features.saveticker.api")
