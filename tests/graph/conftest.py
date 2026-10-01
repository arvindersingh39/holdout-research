"""Graph test fixtures."""

from collections.abc import Iterator

import pytest
from graph_helpers import BRIEF

from r2r.contracts import set_contract_caps
from r2r.llm.fake import FakeLLM


@pytest.fixture
def fake() -> FakeLLM:
    f = FakeLLM()
    f.script("retriever", BRIEF)
    return f


@pytest.fixture(autouse=True)
def _reset_caps() -> Iterator[None]:
    yield
    set_contract_caps(None)
