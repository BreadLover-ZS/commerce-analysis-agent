"""Compatibility oracle for pruning expensive ignored evaluation result trees."""

import hashlib

from evaluations import diagnosis


def test_manifest_matches_legacy_membership_and_bytes():
    current, _ = diagnosis.implementation_manifest()
    legacy = {}
    roots = (
        "app",
        "evaluations",
        "migrations",
        "scripts",
        "tests",
        "frontend/src",
        "frontend/e2e",
        ".github/workflows",
        "deploy",
        "docker",
    )
    suffixes = {
        ".py",
        ".json",
        ".ts",
        ".tsx",
        ".css",
        ".md",
        ".yaml",
        ".yml",
        ".toml",
        ".conf",
        ".sql",
        ".Dockerfile",
    }
    for directory in roots:
        for p in (diagnosis.ROOT / directory).rglob("*"):
            if (
                p.is_file()
                and p.suffix in suffixes
                and "results" not in p.relative_to(diagnosis.ROOT).parts
                and "__pycache__" not in p.parts
            ):
                legacy[str(p.relative_to(diagnosis.ROOT))] = hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
    for name in (
        "pyproject.toml",
        "uv.lock",
        "alembic.ini",
        "frontend/package.json",
        "frontend/pnpm-lock.yaml",
        "frontend/vite.config.ts",
        "frontend/playwright.config.ts",
        "Dockerfile",
        "compose.yaml",
        ".env.example",
        "frontend/.env.example",
        ".dockerignore",
    ):
        p = diagnosis.ROOT / name
        if p.is_file():
            legacy[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    assert current == legacy
