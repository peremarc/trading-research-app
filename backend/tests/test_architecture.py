from __future__ import annotations

import ast
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
LEGACY_BRIDGE_DIRS = {
    APP_DIR / "services",
    APP_DIR / "schemas",
    APP_DIR / "db" / "repositories",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "app.services",
    "app.schemas",
    "app.db.repositories",
)


def _is_legacy_bridge_file(path: Path) -> bool:
    return any(parent == bridge_dir for bridge_dir in LEGACY_BRIDGE_DIRS for parent in [path.parent])


def _find_legacy_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    violations.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                violations.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno} imports from {node.module}")

    return violations


def test_internal_code_does_not_import_legacy_bridges() -> None:
    violations: list[str] = []

    for path in sorted(APP_DIR.rglob("*.py")):
        if _is_legacy_bridge_file(path):
            continue
        violations.extend(_find_legacy_imports(path))

    assert not violations, "Legacy bridge imports found:\n" + "\n".join(violations)


def test_legacy_bridge_packages_are_removed() -> None:
    violations: list[str] = []

    for bridge_dir in sorted(LEGACY_BRIDGE_DIRS):
        if not bridge_dir.exists():
            continue
        for path in sorted(bridge_dir.rglob("*.py")):
            violations.append(f"{path.relative_to(APP_DIR.parent)} still exists")

    assert not violations, "Legacy bridge packages should be removed:\n" + "\n".join(violations)
