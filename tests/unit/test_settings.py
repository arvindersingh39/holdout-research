"""Settings load the repo's config and reject malformed or invalid YAML."""

import re
import shutil
from pathlib import Path

import pytest
import yaml

from r2r.settings import ROLES, Secrets, Settings, SettingsError, load_settings

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    dst = tmp_path / "config"
    shutil.copytree(CONFIG, dst)
    return dst


def _edit(path: Path, mutate) -> None:
    data = yaml.safe_load(path.read_text())
    mutate(data)
    path.write_text(yaml.safe_dump(data))


def test_repo_config_loads() -> None:
    s = load_settings(CONFIG, env_file=None)
    assert isinstance(s, Settings)
    assert set(s.models.roles) == set(ROLES)
    assert s.models.roles["coder"].escalate_to == "anthropic/claude-opus-5-5"
    assert s.models.roles["long_planner"].thinking_budget == 4000
    assert s.budgets.usd_cap_run == 20.0
    assert s.budgets.sandbox.memory == "2g"  # D25
    assert s.budgets.gates.channel == "cli"  # D49: terminal until WhatsApp is set up
    assert s.budgets.contract_token_caps["Roadmap"] == 2000
    assert s.budgets.ingest.chunk_tokens == 800
    assert s.budgets.ingest.chunk_overlap_tokens == 100
    assert s.budgets.ingest.paper_card_input_tokens == 12000
    assert s.budgets.baselines.enabled and s.budgets.baselines.max_queries == 4
    assert s.budgets.baselines.max_pages == 6 and s.budgets.baselines.page_tokens == 4000
    assert (s.budgets.context_pack.k, s.budgets.context_pack.max_refs) == (8, 5)
    assert s.budgets.context_pack.ref_summary_chars == 100
    assert s.models.roles["scout"].thinking_budget == 1024
    assert [c.id for c in s.rubric.criteria] == ["R1", "R2", "R3", "R4", "R5"]


def test_malformed_yaml_rejected(config_dir: Path) -> None:
    (config_dir / "budgets.yaml").write_text("usd_cap_run: [unclosed\n")
    with pytest.raises(SettingsError, match=re.escape("budgets.yaml")):
        load_settings(config_dir, env_file=None)


def test_missing_file_rejected(config_dir: Path) -> None:
    (config_dir / "rubric.yaml").unlink()
    with pytest.raises(SettingsError, match=re.escape("rubric.yaml")):
        load_settings(config_dir, env_file=None)


@pytest.mark.parametrize(
    ("file", "mutate"),
    [
        ("budgets.yaml", lambda d: d.update(usd_cap_run="lots")),
        ("budgets.yaml", lambda d: d.update(usd_cap_milestone=-1)),
        ("budgets.yaml", lambda d: d.update(usd_cap_milestone=50.0)),
        ("budgets.yaml", lambda d: d.update(unknown_key=1)),
        ("budgets.yaml", lambda d: d["gates"].update(channel="telegram")),
        ("budgets.yaml", lambda d: d["gates"].update(review_tests="sometimes")),
        ("budgets.yaml", lambda d: d["sandbox"].update(memory="lots")),
        ("budgets.yaml", lambda d: d["ingest"].update(chunk_overlap_tokens=800)),
        ("budgets.yaml", lambda d: d["ingest"].update(chunk_tokens=0)),
        ("budgets.yaml", lambda d: d["baselines"].update(max_queries=0)),
        ("budgets.yaml", lambda d: d["context_pack"].update(ref_summary_chars=500)),
        ("models.yaml", lambda d: d["roles"].pop("coder")),
        ("models.yaml", lambda d: d["roles"].update(stranger={"model": "x/y", "max_tokens": 1})),
        ("models.yaml", lambda d: d["roles"]["writer"].update(max_tokens=0)),
        ("models.yaml", lambda d: d["roles"]["writer"].update(model="no-provider-prefix")),
        ("rubric.yaml", lambda d: d.update(criteria=[])),
    ],
)
def test_invalid_values_rejected(config_dir: Path, file: str, mutate) -> None:
    _edit(config_dir / file, mutate)
    with pytest.raises(SettingsError, match=re.escape(file)):
        load_settings(config_dir, env_file=None)


def test_secrets_load_from_env_file_and_stay_hidden(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-test-123\nGEMINI_API_KEY=   # empty\n")
    s = load_settings(config_dir, env_file=env)
    assert s.secrets.anthropic_api_key is not None
    assert s.secrets.anthropic_api_key.get_secret_value() == "sk-ant-test-123"
    assert s.secrets.gemini_api_key is None
    assert "sk-ant-test-123" not in repr(s)
    assert "sk-ant-test-123" not in str(s.model_dump())


def test_process_env_overrides_env_file(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=from-file\n")
    monkeypatch.setenv("GEMINI_API_KEY", "from-process")
    s = load_settings(config_dir, env_file=env)
    assert s.secrets.gemini_api_key is not None
    assert s.secrets.gemini_api_key.get_secret_value() == "from-process"


def test_env_example_parses_to_no_secrets(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in Secrets.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    s = load_settings(config_dir, env_file=REPO / ".env.example")
    assert all(v is None for v in s.secrets.model_dump().values())
