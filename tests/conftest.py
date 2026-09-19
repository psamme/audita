"""Keep the onboarding tests from leaving company directories behind.

They create real companies under data/, because the point is to exercise the real code path
rather than a mock. Anything they create is removed afterwards, and the two simulated clients are
never touched.
"""
import shutil

import pytest

from shadow import db

KEEP = {"A", "B", "benchrec"}


def _company_dirs() -> set[str]:
    if not db.DATA.exists():
        return set()
    return {p.name for p in db.DATA.iterdir() if p.is_dir() and p.name not in KEEP}


@pytest.fixture(scope="session", autouse=True)
def clean_up_test_companies():
    before = _company_dirs()
    config = db.DATA / "company.json"
    had_config = config.read_bytes() if config.exists() else None
    yield
    for name in _company_dirs() - before:
        shutil.rmtree(db.DATA / name, ignore_errors=True)
    if had_config is None:
        config.unlink(missing_ok=True)
    else:
        config.write_bytes(had_config)
