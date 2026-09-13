import os
import shutil
from pathlib import Path

import pytest

from data_agents.system import store
from data_agents.web import pacing

REGISTRY = Path("registry")  # the suite runs from the repository root, like every command (README)


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="Tier 2: call the model and re-record the replay tapes (needs OPENAI_API_KEY).")


@pytest.fixture
def live(request) -> bool:
    return request.config.getoption("--live")


def pytest_configure(config):
    # Replays must never fall through to the real API: the package loads .env, so a missing key is not enough. A bogus key fails loudly.
    if not config.getoption("--live"):
        os.environ["OPENAI_API_KEY"] = "sk-no-live-calls-in-replay-tests"
    # A Turn in a test carries the same rows a real one does, and one CLI command configures Logfire for the whole process.
    # Without this a run exports the suite's spans to whatever project the machine's credentials happen to name.
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"


@pytest.fixture(autouse=True)
def fresh_pace(monkeypatch):
    """Pacing counts per process; a test must not be refused for what the tests before it asked."""
    monkeypatch.setattr(pacing, "_recent", {})


@pytest.fixture(autouse=True)
def system_store(tmp_path, monkeypatch) -> Path:
    """Every test gets a System Store of its own, seeded from the committed YAML, over the real data files and Schema Documents.

    So nothing an admin does in the drawer, or a test does to a grant, can reach the suite, which once broke five tests
    when a click revoked a grant an eval case assumed.
    """
    registry_dir = tmp_path / "registry"
    shutil.copytree(REGISTRY / "schemas", registry_dir / "schemas")
    for name in ("databases.yaml", "users.yaml"):
        shutil.copy(REGISTRY / name, registry_dir / name)
    (registry_dir / "data").symlink_to((REGISTRY / "data").resolve())  # the databases themselves; large, read-only, shared
    monkeypatch.setattr(store, "DIR", registry_dir)
    return registry_dir
