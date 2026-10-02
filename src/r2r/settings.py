"""Load `.env` and the YAML files in `config/` into one validated, immutable `Settings`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

ROLES = (
    "retriever",
    "long_planner",
    "short_planner",
    "coordinator",
    "math_designer",
    "coder",
    "optimizer",
    "digest",
    "writer",
    "scout",
    "overseer",
)

DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_ENV_FILE = Path(".env")

# LiteLLM model strings are always "<provider>/<model id>".
ModelId = Annotated[str, Field(pattern=r"^[\w.-]+/\S+$")]


class SettingsError(Exception):
    """A config file is missing, unparsable or invalid. The message names the file."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- models.yaml -----------------------------------------------------------------------


class RoleConfig(_Strict):
    model: ModelId
    max_tokens: PositiveInt
    thinking_budget: PositiveInt | None = None
    escalate_to: ModelId | None = None


class EmbeddingsConfig(_Strict):
    model: ModelId | None = None


class PriceOverride(_Strict):
    input_per_mtok: NonNegativeFloat
    output_per_mtok: NonNegativeFloat


class ModelsConfig(_Strict):
    roles: dict[str, RoleConfig]
    embeddings: EmbeddingsConfig = EmbeddingsConfig()
    cache_prefix: bool = True
    price_overrides: dict[str, PriceOverride] = {}

    @field_validator("roles")
    @classmethod
    def _exact_roles(cls, roles: dict[str, RoleConfig]) -> dict[str, RoleConfig]:
        missing = set(ROLES) - roles.keys()
        unknown = roles.keys() - set(ROLES)
        if missing or unknown:
            raise ValueError(f"roles mismatch: missing={sorted(missing)} unknown={sorted(unknown)}")
        return roles


# --- budgets.yaml ----------------------------------------------------------------------


class SandboxLimits(_Strict):
    cpus: PositiveFloat
    memory: Annotated[str, Field(pattern=r"^\d+[kmg]$")]
    pids: PositiveInt
    test_timeout_s: PositiveInt
    bench_timeout_s: PositiveInt


class IngestLimits(_Strict):
    chunk_tokens: PositiveInt
    chunk_overlap_tokens: Annotated[int, Field(ge=0)]
    paper_card_input_tokens: PositiveInt

    @model_validator(mode="after")
    def _overlap_below_chunk(self) -> IngestLimits:
        if self.chunk_overlap_tokens >= self.chunk_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_tokens")
        return self


class ContextPackLimits(_Strict):
    k: PositiveInt  # chunks retrieved per query
    max_refs: PositiveInt  # context refs kept in a MilestoneSpec
    ref_summary_chars: Annotated[int, Field(ge=20, le=300)]


class BaselineLimits(_Strict):
    enabled: bool
    max_queries: PositiveInt
    max_pages: PositiveInt
    page_tokens: PositiveInt


class OverseerLimits(_Strict):
    """Phase 7c (D64): the advisor that writes evidence-cited briefings for the other agents."""

    enabled: bool
    max_calls_milestone: PositiveInt
    briefing_tokens: PositiveInt  # cap per briefing
    max_briefings: PositiveInt  # per report
    flags_shown: PositiveInt  # newest flags shown at each gate
    evidence_chars: PositiveInt  # cap per evidence item the overseer reads


class ReferenceDataLimits(_Strict):
    """D71: the owner's recorded values, from the proposal's data/ folder."""

    dir: str  # folder inside the proposal
    max_file_kb: PositiveInt
    agent_chars: PositiveInt  # cap per file shown to the designer and overseer


class PublishLimits(_Strict):
    """Phase 8 (D67): paper and package output."""

    modes: list[Literal["paper", "package"]]
    fix_attempts: Annotated[int, Field(ge=0, le=3)]  # writer fixes after a failed LaTeX compile
    log_tail_lines: PositiveInt  # compile-log lines sent back to the writer
    compile_timeout_s: PositiveInt


class ContextPackTokens(_Strict):
    short_planner: PositiveInt
    math_designer: PositiveInt
    writer_section: PositiveInt


class WhatsAppGates(_Strict):
    gate_template: str
    template_lang: str
    batch_pending_gates: bool


class GatesConfig(_Strict):
    channel: Literal["cli", "whatsapp"]
    approve_first_roadmap: bool
    roadmap_changes: Literal["material_only", "always"]
    review_tests: Literal["always", "first_milestone", "never"]
    remind_after_h: PositiveFloat
    max_reminders: Annotated[int, Field(ge=0)]
    full_text: Literal["attach", "summary_only"]
    whatsapp: WhatsAppGates


class BudgetsConfig(_Strict):
    usd_cap_run: PositiveFloat
    usd_cap_milestone: PositiveFloat
    coordinator_max_revise_rounds: Annotated[int, Field(ge=0)]
    coder_max_turns: PositiveInt
    coder_escalate_after: PositiveInt
    coder_max_sessions: PositiveInt
    optimizer_max_attempts: Annotated[int, Field(ge=0)]
    optimizer_min_gain: Annotated[float, Field(ge=0, lt=1)]
    optimizer_patience: PositiveInt
    optimizer_target_runtime_s: PositiveFloat | None = None
    bench_repeats: PositiveInt
    sandbox: SandboxLimits
    ingest: IngestLimits
    context_pack_tokens: ContextPackTokens
    context_pack: ContextPackLimits
    baselines: BaselineLimits
    overseer: OverseerLimits
    publish: PublishLimits
    reference_data: ReferenceDataLimits
    gates: GatesConfig
    contract_token_caps: dict[str, PositiveInt]

    @model_validator(mode="after")
    def _milestone_within_run(self) -> BudgetsConfig:
        if self.usd_cap_milestone > self.usd_cap_run:
            raise ValueError("usd_cap_milestone must not exceed usd_cap_run")
        return self


# --- rubric.yaml -----------------------------------------------------------------------


class RubricCriterion(_Strict):
    id: Annotated[str, Field(pattern=r"^R\d+$")]
    text: Annotated[str, Field(min_length=1, max_length=300)]


class Rubric(_Strict):
    criteria: Annotated[list[RubricCriterion], Field(min_length=1)]


# --- .env ------------------------------------------------------------------------------


class Secrets(BaseModel):
    """API keys and tokens. `SecretStr` keeps them out of repr, logs and dumps."""

    model_config = ConfigDict(frozen=True)

    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None
    e2b_api_key: SecretStr | None = None
    semantic_scholar_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_owner_number: str | None = None
    public_webhook_url: str | None = None


# --- top level -------------------------------------------------------------------------


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_dir: Path
    models: ModelsConfig
    budgets: BudgetsConfig
    rubric: Rubric
    secrets: Secrets


def load_settings(
    config_dir: Path = DEFAULT_CONFIG_DIR,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> Settings:
    """Read and validate every config file. Process environment variables win over `.env`."""
    config_dir = Path(config_dir)
    return Settings(
        config_dir=config_dir,
        models=_load_yaml(config_dir / "models.yaml", ModelsConfig),
        budgets=_load_yaml(config_dir / "budgets.yaml", BudgetsConfig),
        rubric=_load_yaml(config_dir / "rubric.yaml", Rubric),
        secrets=_load_secrets(env_file),
    )


def _load_yaml[M: BaseModel](path: Path, model: type[M]) -> M:
    try:
        data: Any = yaml.safe_load(path.read_text())
    except FileNotFoundError as e:
        raise SettingsError(f"{path.name}: file not found at {path}") from e
    except yaml.YAMLError as e:
        raise SettingsError(f"{path.name}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise SettingsError(f"{path.name}: expected a mapping at the top level")
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise SettingsError(f"{path.name}: {e}") from e


def _load_secrets(env_file: Path | None) -> Secrets:
    file_values = dotenv_values(env_file) if env_file and Path(env_file).is_file() else {}
    values: dict[str, str] = {}
    for name in Secrets.model_fields:
        raw = os.environ.get(name.upper()) or file_values.get(name.upper())
        if raw and raw.strip():
            values[name] = raw.strip()
    return Secrets.model_validate(values)
