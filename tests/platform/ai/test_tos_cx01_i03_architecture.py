"""TOS-CX01-I03 architecture guards for Groq provider isolation."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.dbutil import REPO_ROOT

SRC = REPO_ROOT / "src" / "aieos"
GATEWAY = SRC / "platform" / "ai" / "gateway.py"
GROQ_PKG = SRC / "platform" / "ai" / "providers" / "groq"
EDUCATION = SRC / "domains" / "education"
TEACHING = SRC / "domains" / "teaching"
CONTENT = SRC / "domains" / "content"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_groq_sdk_not_added() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "groq>=" not in pyproject
    assert '"groq"' not in pyproject
    assert "'groq'" not in pyproject


def test_domain_code_does_not_import_groq_or_openai_sdk() -> None:
    offenders: list[str] = []
    for root in (EDUCATION, TEACHING, CONTENT):
        for path in root.rglob("*.py"):
            imports = _import_roots(path)
            if "openai" in imports or "groq" in imports:
                offenders.append(str(path.relative_to(REPO_ROOT)))
            text = path.read_text(encoding="utf-8")
            if "aieos.platform.ai.providers.groq" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_gateway_contract_has_no_provider_types() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "groq" not in source.lower()
    assert "openai" not in source.lower()


def test_no_new_migration() -> None:
    assert list(MIGRATIONS.glob("tosd100002*.py")) == []
    versions = sorted(MIGRATIONS.glob("tosd10*.py"))
    assert versions[-1].name.startswith("tosd100001_")


def test_groq_adapter_does_not_stream_or_use_tools() -> None:
    source = (GROQ_PKG / "adapter.py").read_text(encoding="utf-8")
    assert "chat.completions.create" in source
    assert "max_completion_tokens" in source
    assert "json_schema" in source
    assert "stream=True" not in source
    assert "tools=" not in source
