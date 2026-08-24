"""Every symbol imported from `config` must exist in the config production reads.

There are two config.py files. `webhook-server/config.py` is what production
imports. The repo-root `config.py` shadows it under pytest and any tooling run
from the repo root, because the root is first on sys.path. They differ by
roughly 120 lines and that is fine — they are not meant to be identical.

What is not fine is a module doing `from config import X` where X exists only
in the root copy. Tests pass, production raises ImportError. When the import is
lazy — inside a function, as most of them are — nothing fails at boot either;
the feature just stops working whenever it next runs.

That is exactly how `apartmentscom_ingestion.ingest_date()` shipped broken:
`BIGQUERY_APARTMENTSCOM_DAILY_TABLE` lived only in the root config.

This test reads the imports out of the source rather than trusting a list, so
it covers modules that do not exist yet.
"""

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEBHOOK_SERVER = REPO / "webhook-server"

sys.path.insert(0, str(WEBHOOK_SERVER))


# Symbols that legitimately differ, or that are known-broken and awaiting a
# decision. Nothing belongs here without a reason and an owner — the point of
# the list is that drift stays visible, not that it becomes permitted.
KNOWN_DRIFT = {
    "VIDEO_PROVIDER_DEFAULT":
        "root defaults to 'creatify', production to 'heygen', and "
        "video_providers.get_provider() adds a third fallback of 'creatify'. "
        "VIDEO_PROVIDER_DEFAULT is not set in the environment, so a video "
        "enrolled without an explicit provider goes to HeyGen in production "
        "and Creatify in every test. Needs a product call on which provider is "
        "the default (Kyle) — see feature/property-ai-video, PR #29.",
}


def _config_symbols(path: Path) -> set:
    """Top-level assigned names in a config.py, read statically.

    Parsed rather than imported so a missing env var or a side effect in one
    copy cannot affect the other.
    """
    tree = ast.parse(path.read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _imports_from_config():
    """[(module_path, symbol, lineno)] for every `from config import ...`.

    Includes imports nested inside functions — those are the dangerous ones,
    because they fail at call time rather than at boot.
    """
    found = []
    for py in sorted(WEBHOOK_SERVER.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue  # a file this Python can't parse is not this test's problem
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "config":
                for alias in node.names:
                    if alias.name != "*":
                        found.append((py.relative_to(REPO), alias.name, node.lineno))
    return found


def test_there_are_two_config_files():
    # If this ever stops being true the rest of the file is obsolete, and that
    # would be good news worth noticing rather than silently skipping.
    assert (REPO / "config.py").exists()
    assert (WEBHOOK_SERVER / "config.py").exists()


def test_every_imported_symbol_exists_in_production_config():
    prod = _config_symbols(WEBHOOK_SERVER / "config.py")
    missing = [
        f"{path}:{lineno} imports {sym}"
        for path, sym, lineno in _imports_from_config()
        if sym not in prod
    ]
    assert not missing, (
        "These symbols are imported from `config` but are absent from "
        "webhook-server/config.py, which is the file production imports. "
        "Tests pass because the repo-root config.py shadows it under pytest:\n  "
        + "\n  ".join(missing)
    )


def test_the_apartmentscom_regression_specifically():
    """Named case, so the fix has a test that says what it was."""
    prod = _config_symbols(WEBHOOK_SERVER / "config.py")
    root = _config_symbols(REPO / "config.py")
    for sym in ("BIGQUERY_APARTMENTSCOM_DAILY_TABLE",
                "BIGQUERY_APARTMENTSCOM_MAP_TABLE"):
        assert sym in root
        assert sym in prod, f"{sym} is back to root-config-only — prod ImportError"


def test_shared_symbols_have_matching_defaults():
    """Where both files define the same name, the default must be the same.

    Two configs with the same symbol and different fallbacks is worse than a
    missing symbol: nothing errors, production just quietly uses a different
    value than every test asserted against.
    """
    root_src = ast.parse((REPO / "config.py").read_text())
    prod_src = ast.parse((WEBHOOK_SERVER / "config.py").read_text())

    def literals(tree):
        out = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if isinstance(t, ast.Name):
                    try:
                        out[t.id] = ast.unparse(node.value)
                    except AttributeError:      # Python < 3.9 has no unparse
                        pytest.skip("ast.unparse unavailable")
        return out

    root, prod = literals(root_src), literals(prod_src)
    drift = {
        name for name in set(root) & set(prod)
        if root[name] != prod[name]
    }

    unexpected = sorted(drift - set(KNOWN_DRIFT))
    assert not unexpected, (
        "config defaults have drifted between the two files:\n  "
        + "\n  ".join(f"{n}: root={root[n]!r} prod={prod[n]!r}" for n in unexpected)
        + "\n\nMake them match, or add to KNOWN_DRIFT with the reason and who decides."
    )

    healed = sorted(set(KNOWN_DRIFT) - drift)
    assert not healed, (
        "These no longer drift — delete them from KNOWN_DRIFT so the list stays "
        f"an accurate account of what is broken: {healed}"
    )
