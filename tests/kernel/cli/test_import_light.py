"""CLI 경량 기동 회귀 가드: `trader --help` 가 uvicorn·sklearn 을 로드하지 않는다.

pandas·fastapi 는 Feature 선언 지연 로딩이 필요해 이연(T13-R2)."""

import os
import subprocess
import sys

HEAVY = {"uvicorn", "sklearn"}

CODE = (
    "import sys\n"
    "from trader.kernel.cli.main import app\n"
    "try:\n"
    "    app(['--help'])\n"
    "except SystemExit:\n"
    "    pass\n"
    "print('MODULES=' + ','.join(sorted({m.split('.')[0] for m in sys.modules})))\n"
)


def test_cli_import_does_not_load_heavy_modules(tmp_path):
    env = dict(os.environ)
    env["TRADER_HOME"] = str(tmp_path)
    r = subprocess.run(
        [sys.executable, "-c", CODE],
        shell=False,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    line = next(x for x in r.stdout.splitlines() if x.startswith("MODULES="))
    loaded = set(line.removeprefix("MODULES=").split(","))
    assert "trader" in loaded and "typer" in loaded
    assert loaded & HEAVY == set()
