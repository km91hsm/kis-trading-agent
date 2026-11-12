"""계약 검사: Feature 선언 중복·README 존재, 그리고 import 경계(AST)."""

from __future__ import annotations

import ast
import importlib
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from trader.kernel.daemon.migrations import QUEUE
from trader.kernel.execution.migrations import EXECUTION, EXECUTION_V7, EXECUTION_V20
from trader.kernel.execution.types import ORIGIN_CHECK
from trader.kernel.lane.types import LANES
from trader.kernel.ledger.db import LedgerError
from trader.kernel.ledger.migrations import KERNEL, WEB, Migration
from trader.kernel.ledger.migrations import check as check_migrations
from trader.kernel.ops.types import RESERVED_AREA_KEYS
from trader.kernel.registry.types import Feature
from trader.kernel.session.migrations import RESEARCH, RESEARCH_FAILOVER, SESSION

# 다른 기능에서 import 할 수 있는 유일한 공개면.
GATE_NAMES = ("market", "monitor")
PUBLIC_MODULE = "api"
# 커널 중 기능 패키지를 동적으로 import 하는 유일한 파일.
KERNEL_EXEMPT = Path("registry") / "discover.py"


# ledger.migrations.KERNEL(v1)에 넣지 않는 커널 모듈 소유 마이그레이션
KERNEL_MODULES: list[Migration] = [
    SESSION,
    EXECUTION,
    EXECUTION_V7,
    EXECUTION_V20,
    QUEUE,
    WEB,
    RESEARCH,
    RESEARCH_FAILOVER,
]


def _dups(items: Iterable[str]) -> list[str]:
    return sorted(k for k, n in Counter(items).items() if n > 1)


def all_migrations(features: Iterable[Feature]) -> list[Migration]:
    """커널 + 커널 모듈 + 모든 기능 마이그레이션을 버전순으로 합친다 (기능 간 교차 배치 허용)."""
    return sorted(
        [*KERNEL, *KERNEL_MODULES, *(m for f in features for m in f.migrations)],
        key=lambda m: m.version,
    )


def _default_roots() -> list[Path]:
    package = importlib.import_module("trader.features")
    return [Path(p) for p in package.__path__]


def check(features: list[Feature], roots: Sequence[Path] | None = None) -> list[str]:
    """계약 위반 메시지 목록(빈 리스트면 통과). roots: 기능 폴더가 놓인 디렉터리들(README 검사)."""
    msgs: list[str] = []
    for label, values in (
        ("기능 id", [f.id for f in features]),
        ("잡 id", [j.id for f in features for j in f.jobs]),
        ("큐 kind", [q.kind for f in features for q in f.queue_kinds]),
        ("SSE 키", [s.key for f in features for s in f.sse_keys]),
        ("origin", [o.origin for f in features for o in f.origins]),
        ("에이전트 이름", [a.name for f in features for a in f.agents]),
        ("포트", [p.name for f in features for p in f.ports]),
        ("게이트", [g.name for f in features for g in f.gates]),
        ("nav id", [n.id for f in features for n in f.nav]),
        ("텔레그램 콜백 prefix", [c.prefix for f in features for c in f.telegram_callbacks]),
        ("health 영역 key", [a.key for f in features for a in f.health_areas]),
    ):
        msgs += [f"{label} 중복: {d}" for d in _dups(values)]
    msgs += [
        f"기능 {f.id}: 알 수 없는 게이트 {g.name}"
        for f in features
        for g in f.gates
        if g.name not in GATE_NAMES
    ]
    msgs += [
        f"기능 {f.id} 의 health 영역 key 는 '{f.id}' 여야 합니다: {a.key}"
        for f in features
        for a in f.health_areas
        if a.key != f.id
    ]
    msgs += [
        f"기능 {f.id} 의 health 영역 key '{a.key}' 는 커널 예약 key 입니다"
        for f in features
        for a in f.health_areas
        if a.key in RESERVED_AREA_KEYS
    ]
    tool_names = {t.name for f in features for t in f.tools}
    for f in features:
        for k in f.session_kinds:
            if k.tools is None:
                msgs.append(f"기능 {f.id} 세션 {k.kind}: 허용 도구(tools)를 선언해야 합니다")
                continue
            msgs += [
                f"기능 {f.id} 세션 {k.kind}: 등록되지 않은 도구 {name}"
                for name in k.tools
                if name not in tool_names
            ]
    try:
        check_migrations(all_migrations(features))
    except LedgerError as exc:
        msgs.append(str(exc))
    for f in features:
        if f.tier == "lane" and f.id not in LANES:
            msgs.append(f"기능 {f.id}: tier=lane 이지만 LANES 에 없음")
        if f.id in LANES and f.tier != "lane":
            msgs.append(f"LANES 의 {f.id} 가 tier=lane 이 아님")
        msgs += [
            f"기능 {f.id}: origin {o.origin} 는 orders.origin CHECK 밖"
            for o in f.origins
            if o.origin not in ORIGIN_CHECK
        ]
    search = list(roots) if roots is not None else _default_roots()
    for f in features:
        if not any((root / f.id / "README.md").is_file() for root in search):
            msgs.append(f"기능 {f.id}: README.md 가 없습니다")
    return msgs


def _module_name(path: Path, root: Path, package: str) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join([package, *parts])


def _targets(tree: ast.AST, module: str, is_pkg: bool) -> list[tuple[str, tuple[str, ...]]]:
    """(import 대상 모듈, 가져온 이름들) 목록. 상대 import 는 절대 이름으로 풀어 준다."""
    out: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, ()) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                pkg_parts = module.split(".") if is_pkg else module.split(".")[:-1]
                pkg_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                base = ".".join([*pkg_parts, *([node.module] if node.module else [])])
            out.append((base, tuple(a.name for a in node.names)))
    return out


def _violations_in(
    tree: ast.AST, module: str, is_pkg: bool, owner: str, features_pkg: str
) -> list[str]:
    msgs: list[str] = []
    prefix = features_pkg + "."
    for target, names in _targets(tree, module, is_pkg):
        if target == features_pkg:
            # `from <features> import other` — 기능 패키지 자체를 가져온다.
            imported = [f"{features_pkg}.{n}" for n in names]
        elif target.startswith(prefix):
            imported = [target]
        else:
            continue
        for full in imported:
            rest = full[len(prefix) :].split(".")
            other = rest[0]
            if other == owner:
                continue
            if owner in LANES:
                msgs.append(f"{module}: 레인 {owner} 는 기능 {other} 를 import 불가 ({full})")
                continue
            if len(rest) == 1 and target != features_pkg:
                # `from <features>.other import api` — 이름이 모두 api 여야 한다.
                bad = [n for n in names if n != PUBLIC_MODULE]
                if bad:
                    msgs.append(
                        f"{module}: 기능 {owner} 가 {other} 의 비공개 {', '.join(bad)} 를 import "
                        f"({full}.{bad[0]}) — {other}.{PUBLIC_MODULE} 만 허용"
                    )
            elif rest[1:2] != [PUBLIC_MODULE]:
                msgs.append(
                    f"{module}: 기능 {owner} 가 {'.'.join(rest)} 를 import "
                    f"— {other}.{PUBLIC_MODULE} 만 허용"
                )
    return msgs


def import_violations(src_root: Path, package: str = "trader") -> list[str]:
    """AST 로 import 경계 위반을 찾는다.

    src_root 아래에 `features/` 가 있으면 프로젝트 루트(`<package>.features`, 커널 검사 포함),
    없으면 src_root 자체가 기능 폴더들의 루트(`package` 가 곧 기능 패키지)로 본다.
    (a) 다른 기능은 `api` 모듈로만 (b) 레인은 기능 import 금지 (c) kernel 은 기능 import 금지."""
    msgs: list[str] = []
    if (src_root / "features").is_dir():
        features_root, features_pkg = src_root / "features", f"{package}.features"
        kernel_root = src_root / "kernel"
    else:
        features_root, features_pkg, kernel_root = src_root, package, None

    for path in sorted(features_root.rglob("*.py")):
        rel = path.relative_to(features_root)
        if len(rel.parts) < 2:
            continue  # 기능 폴더 밖 파일
        module = _module_name(path, features_root, features_pkg)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        msgs += _violations_in(tree, module, path.name == "__init__.py", rel.parts[0], features_pkg)

    if kernel_root is not None and kernel_root.is_dir():
        for path in sorted(kernel_root.rglob("*.py")):
            if path.relative_to(kernel_root) == KERNEL_EXEMPT:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            module = _module_name(path, kernel_root, f"{package}.kernel")
            for target, names in _targets(tree, module, path.name == "__init__.py"):
                # `from pkg import features` 는 target 이 package 이고 names 에 features 가 있다.
                hit = target == features_pkg or target.startswith(features_pkg + ".")
                if not hit and target == package and "features" in names:
                    target = features_pkg
                    hit = True
                if hit:
                    msgs.append(f"{module}: kernel 이 {target} 를 import 합니다 (금지)")
    return msgs
