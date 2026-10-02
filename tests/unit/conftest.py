"""Shared fixtures: repo settings, a temp ledger, FakeLLM and a call context."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from r2r.contracts import set_contract_caps
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import CallContext
from r2r.settings import Settings, load_settings
from r2r.store.ledger import Ledger

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings() -> Settings:
    return load_settings(REPO / "tests" / "fixtures" / "config", env_file=None)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite")


@pytest.fixture
def fake() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def ctx(settings: Settings, ledger: Ledger, fake: FakeLLM) -> CallContext:
    return CallContext(
        run_id="run-test",
        milestone_id="M1",
        settings=settings,
        ledger=ledger,
        client=fake,
        prompts_dir=REPO / "prompts",
        retry_wait_max_s=0,
    )


@pytest.fixture(autouse=True)
def _reset_caps() -> Iterator[None]:
    yield
    set_contract_caps(None)
