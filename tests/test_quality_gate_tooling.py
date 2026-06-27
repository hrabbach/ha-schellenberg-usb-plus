"""Regression guards for the Phase 5 quality-gate tooling invariants.

These tests lock in the outcomes of Phase 5 (TOOL-01, TOOL-02): the unused
pre-commit config stays removed, codespell stays configured in pyproject.toml,
and CONTRIBUTING.md keeps documenting the manual gate. They protect the
"single documented quality path" from silent regression in later phases.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pre_commit_config_removed() -> None:
    """TOOL-01: .pre-commit-config.yaml must not be reintroduced."""
    assert not (REPO_ROOT / ".pre-commit-config.yaml").exists()


def test_pre_commit_dependency_removed() -> None:
    """TOOL-01: the orphaned pre-commit lint dependency stays gone."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    lint = pyproject["dependency-groups"]["lint"]
    assert not any(dep.startswith("pre-commit") for dep in lint)


def test_codespell_configured() -> None:
    """TOOL-02: codespell config lives in pyproject [tool.codespell]."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    codespell = pyproject["tool"]["codespell"]
    assert codespell["ignore-words-list"]
    lint = pyproject["dependency-groups"]["lint"]
    assert any(dep.startswith("codespell") for dep in lint)


def test_contributing_documents_full_gate() -> None:
    """TOOL-01/TOOL-02: CONTRIBUTING.md documents all four gate tools."""
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for tool in ("pytest", "ruff", "mypy", "codespell"):
        assert tool in text, f"CONTRIBUTING.md must document {tool}"
