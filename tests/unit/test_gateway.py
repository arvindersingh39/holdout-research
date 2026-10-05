"""call_structured with FakeLLM: zero tokens, deterministic."""

import json

import pytest
from samples import research_brief, roadmap

from r2r.contracts import Roadmap, set_contract_caps
from r2r.llm.client import TransientLLMError
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import BudgetExceeded, CallContext, ContractViolation, call_structured
from r2r.settings import Settings


def _call(ctx: CallContext, **kw) -> Roadmap:
    return call_structured("long_planner", "long_planner", research_brief(), Roadmap, ctx, **kw)


def _with_budgets(ctx: CallContext, **budgets) -> CallContext:
    s: Settings = ctx.settings
    new = s.model_copy(update={"budgets": s.budgets.model_copy(update=budgets)})
    return CallContext(**{**ctx.__dict__, "settings": new})


def test_valid_output(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", roadmap())
    out = _call(ctx)
    assert isinstance(out, Roadmap)
    assert out.goal == roadmap().goal
    assert (out.created_by, out.run_id, out.milestone_id) == ("long_planner", "run-test", "M1")
    rows = ctx.ledger.rows("run-test")
    assert len(rows) == 1
    r = rows[0]
    assert (r.role, r.model, r.milestone_id, r.ok) == (
        "long_planner",
        "anthropic/claude-opus-5-5",
        "M1",
        True,
    )
    assert r.input_tokens > 0 and r.output_tokens > 0 and r.cost_usd > 0


def test_request_shape(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", roadmap())
    _call(ctx)
    req = fake.calls[0]
    system, user = req.messages
    assert system["role"] == "system"
    block = system["content"][0]
    assert block["cache_control"] == {"type": "ephemeral"}  # cache_prefix: true
    assert "long_planner" in block["text"]  # prompt file comes first
    assert '"milestones"' in block["text"]  # then the output schema
    assert '"run_id"' not in block["text"]  # metadata is filled in by the gateway
    assert user["role"] == "user"
    assert "<data>" in user["content"] and "</data>" in user["content"]
    assert "not instructions" in user["content"]
    assert research_brief().objective in user["content"]
    # Anthropic needs max_tokens > thinking budget: output limit plus thinking budget.
    assert req.thinking == {"type": "enabled", "budget_tokens": 4000}
    assert req.max_tokens == 3000 + 4000
    assert req.response_format is not None and req.tools is None


def test_fenced_json_accepted(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", "Here you go:\n```json\n" + _roadmap_json() + "\n```")
    assert _call(ctx).version == 1


def test_invalid_then_repaired(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", '{"version": "one"}', roadmap())
    out = _call(ctx)
    assert out.version == 1
    assert len(fake.calls) == 2
    repair = fake.calls[1].messages
    assert repair[-2]["role"] == "assistant"
    assert "version" in repair[-1]["content"]  # the validation error text is sent back
    rows = ctx.ledger.rows("run-test")
    assert len(rows) == 2
    assert rows[0].ok and rows[0].error and rows[0].error.startswith("rejected output:")
    assert "version" in rows[0].error  # why the first answer needed a repair is kept
    assert rows[1].error is None


def test_two_invalid_outputs_raise(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", "not json", '{"version": 1}')
    with pytest.raises(ContractViolation):
        _call(ctx)
    rows = ctx.ledger.rows("run-test")
    assert len(rows) == 2
    assert all(r.error and r.error.startswith("rejected output:") for r in rows)


def test_cap_overflow_counts_as_invalid(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", roadmap(), roadmap())  # built before the cap shrinks
    set_contract_caps({"Roadmap": 5})
    with pytest.raises(ContractViolation, match="cap"):
        _call(ctx)


def test_budget_exceeded_before_any_call(ctx: CallContext, fake: FakeLLM) -> None:
    tight = _with_budgets(ctx, usd_cap_run=0.0001, usd_cap_milestone=0.0001)
    fake.script("long_planner", roadmap())
    with pytest.raises(BudgetExceeded):
        _call(tight)
    assert fake.calls == []
    assert ctx.ledger.rows("run-test") == []


def test_budget_counts_prior_spend(ctx: CallContext, fake: FakeLLM) -> None:
    from r2r.store.ledger import CallRecord

    ctx.ledger.record(
        CallRecord(run_id="run-test", milestone_id="M1", role="coder", model="x/y", cost_usd=2.9999)
    )
    fake.script("long_planner", roadmap())
    with pytest.raises(BudgetExceeded, match="milestone"):
        _call(ctx)


def test_transient_errors_retried(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", TransientLLMError("429"), TransientLLMError("503"), roadmap())
    assert _call(ctx).version == 1
    rows = ctx.ledger.rows("run-test")
    assert [r.ok for r in rows] == [False, False, True]
    assert rows[0].error and "429" in rows[0].error


def test_retries_give_up_after_four(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", *[TransientLLMError("429")] * 4)
    with pytest.raises(TransientLLMError):
        _call(ctx)
    assert len(fake.calls) == 4


def test_forced_tool_fallback(ctx: CallContext) -> None:
    fake = FakeLLM(native_schema=False)
    ctx = CallContext(**{**ctx.__dict__, "client": fake})
    fake.script("long_planner", roadmap())
    assert _call(ctx).version == 1
    req = fake.calls[0]
    assert req.response_format is None
    assert [t["function"]["name"] for t in req.tools] == ["submit_roadmap"]
    assert req.tool_choice == {"type": "function", "function": {"name": "submit_roadmap"}}


def test_price_overrides_win(ctx: CallContext, fake: FakeLLM) -> None:
    s = ctx.settings
    from r2r.settings import PriceOverride

    override = PriceOverride(input_per_mtok=1_000_000.0, output_per_mtok=0.0)
    models = s.models.model_copy(
        update={"price_overrides": {"anthropic/claude-opus-5-5": override}}
    )
    ctx = CallContext(**{**ctx.__dict__, "settings": s.model_copy(update={"models": models})})
    ctx = _with_budgets(ctx, usd_cap_run=1e12, usd_cap_milestone=1e12)
    fake.script("long_planner", roadmap())
    _call(ctx)
    row = ctx.ledger.rows("run-test")[0]
    assert row.cost_usd == pytest.approx(row.input_tokens * 1.0)


def test_model_override_limited_to_escalation(ctx: CallContext, fake: FakeLLM) -> None:
    from samples import milestone_spec

    fake.script("coder", roadmap())
    out = call_structured(
        "coder", "coder", milestone_spec(), Roadmap, ctx, model="anthropic/claude-opus-5-5"
    )
    assert out.version == 1
    assert fake.calls[0].model == "anthropic/claude-opus-5-5"
    with pytest.raises(ValueError, match="not configured"):
        call_structured("coder", "coder", milestone_spec(), Roadmap, ctx, model="x/other")


def test_unknown_role_rejected(ctx: CallContext) -> None:
    with pytest.raises(KeyError):
        call_structured("stranger", "x", research_brief(), Roadmap, ctx)


def _roadmap_json() -> str:
    data = roadmap().model_dump(exclude={"created_by", "run_id", "milestone_id"})
    return json.dumps(data)


# --- embeddings ---------------------------------------------------------------------


def test_embed_local_model_is_ledgered_at_zero_cost(ctx: CallContext, fake: FakeLLM) -> None:
    from r2r.llm.gateway import embed

    vectors = embed(["monte carlo pricing", "control variates"], ctx)
    assert len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0
    assert fake.embed_calls == [("local/all-MiniLM-L6-v2", 2)]
    (row,) = ctx.ledger.rows("run-test")
    assert (row.role, row.model, row.cost_usd, row.ok) == (
        "embeddings",
        "local/all-MiniLM-L6-v2",
        0.0,
        True,
    )
    assert row.input_tokens > 0


def test_embed_empty_makes_no_call(ctx: CallContext, fake: FakeLLM) -> None:
    from r2r.llm.gateway import embed

    assert embed([], ctx) == []
    assert fake.embed_calls == []
    assert ctx.ledger.rows("run-test") == []


def test_embed_batches(ctx: CallContext, fake: FakeLLM) -> None:
    from r2r.llm.gateway import EMBED_BATCH, embed

    assert len(embed(["t"] * (EMBED_BATCH + 3), ctx)) == EMBED_BATCH + 3
    assert [n for _, n in fake.embed_calls] == [EMBED_BATCH, 3]
    assert len(ctx.ledger.rows("run-test")) == 2


def test_fake_embeddings_are_lexical(fake: FakeLLM) -> None:
    import math

    a, b, c = fake.embed(
        "local/x", ["option pricing monte carlo", "monte carlo option", "protein folding"]
    ).vectors

    def cos(u, v):
        return sum(x * y for x, y in zip(u, v, strict=True)) / (
            math.sqrt(sum(x * x for x in u)) * math.sqrt(sum(y * y for y in v))
        )

    assert cos(a, b) > cos(a, c)


# --- tool-loop conversation caching (D31) ----------------------------------------------


def test_tool_loop_caches_newest_message_for_anthropic(ctx: CallContext, fake: FakeLLM) -> None:
    from pydantic import BaseModel
    from samples import milestone_spec

    from r2r.llm.fake import tool_call
    from r2r.llm.gateway import run_tool_loop
    from r2r.llm.tools import Tool

    class NoArgs(BaseModel):
        pass

    tools = [Tool(name="ping", description="ping", input_model=NoArgs, handler=lambda _: "pong")]
    fake.script("coder", [tool_call("ping")], "done")
    run_tool_loop("coder", "coder", milestone_spec(), tools, 5, lambda s: False, ctx)
    second = fake.calls[1].messages
    marked = [
        m
        for m in second
        if isinstance(m.get("content"), list) and any("cache_control" in b for b in m["content"])
    ]
    assert [m["role"] for m in marked] == ["system", "tool"]  # system prefix + newest message
    assert second[-1]["content"][0]["text"] == "pong"
    # earlier requests are not mutated after the fact
    assert not any(
        isinstance(m.get("content"), list)
        and m["role"] == "user"
        and "cache_control" in m["content"][0]
        for m in second
    )


def test_tool_loop_no_cache_point_for_other_providers(ctx: CallContext, fake: FakeLLM) -> None:
    from pydantic import BaseModel
    from samples import milestone_spec

    from r2r.llm.fake import tool_call
    from r2r.llm.gateway import run_tool_loop
    from r2r.llm.tools import Tool

    class NoArgs(BaseModel):
        pass

    tools = [Tool(name="ping", description="ping", input_model=NoArgs, handler=lambda _: "pong")]
    fake.script("optimizer", [tool_call("ping")], "done")
    run_tool_loop("optimizer", "optimizer", milestone_spec(), tools, 5, lambda s: False, ctx)
    assert all(
        isinstance(m["content"], str) or m["role"] == "system" or m["content"] is None
        for m in fake.calls[1].messages
    )


# --- reasoning budgets per provider (Q6.1) ---------------------------------------------


def test_openrouter_reasoning_budget_format(ctx: CallContext, fake: FakeLLM) -> None:
    """Through OpenRouter, Anthropic-style `thinking` is ignored for Gemini (probe 2026-10-01);
    the budget must go in OpenRouter's own `reasoning.max_tokens`."""
    from samples import optimization_patch, profile_report

    from r2r.contracts import OptimizationPatch

    fake.script("optimizer", optimization_patch())
    call_structured("optimizer", "optimizer", profile_report(), OptimizationPatch, ctx)
    req = fake.calls[0]
    assert req.model.startswith("openrouter/")
    assert req.thinking is None
    assert req.extra == {"extra_body": {"reasoning": {"max_tokens": 2000}}}
    assert req.max_tokens == 4000 + 2000  # thinking never crowds out the answer


def test_anthropic_reasoning_budget_unchanged(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("long_planner", roadmap())
    _call(ctx)
    req = fake.calls[0]
    assert req.thinking == {"type": "enabled", "budget_tokens": 4000}
    assert req.extra == {}


# --- finalize inside the repair loop (found by pilot-1, M3) ----------------------------


def test_finalize_overflow_gets_one_repair(ctx: CallContext, fake: FakeLLM) -> None:
    """A draft that fits but overflows its cap once code adds fields must get the normal
    repair round (with the cap message), not crash the run."""
    from r2r.contracts import ContractTooLarge, Roadmap

    calls = []

    def finalize(draft: Roadmap) -> Roadmap:
        calls.append(draft)
        if len(calls) == 1:
            raise ContractTooLarge("MilestoneSpec", 1579, 1500)
        return draft

    fake.script("long_planner", roadmap(), roadmap())
    out = call_structured(
        "long_planner", "long_planner", research_brief(), Roadmap, ctx, finalize=finalize
    )
    assert isinstance(out, Roadmap) and len(calls) == 2
    repair = fake.calls[1].messages[-1]["content"]
    assert "cap is 1500" in repair
    rows = ctx.ledger.rows("run-test")
    assert rows[0].error and "cap is 1500" in rows[0].error


def test_finalize_overflow_twice_is_contract_violation(ctx: CallContext, fake: FakeLLM) -> None:
    from r2r.contracts import ContractTooLarge, Roadmap

    def finalize(draft: Roadmap) -> Roadmap:
        raise ContractTooLarge("MilestoneSpec", 1579, 1500)

    fake.script("long_planner", roadmap(), roadmap())
    with pytest.raises(ContractViolation, match="cap"):
        call_structured(
            "long_planner", "long_planner", research_brief(), Roadmap, ctx, finalize=finalize
        )


# --- strict schema providers (study-1: OpenAI rejected the MilestoneSpecDraft schema) -----------


@pytest.mark.parametrize(
    "message",
    [
        "BadRequestError: Invalid schema for response_format 'Roadmap': In context=(), "
        "'required' is required to be supplied and to be an array including every key",
        # study-3 M9: Anthropic's native structured output refused the MathDesign grammar
        'BadRequestError: litellm.BadRequestError: AnthropicException - {"type":"error",'
        '"error":{"type":"invalid_request_error","message":"Schema is too complex."}}',
    ],
    ids=["openai-strict", "anthropic-too-complex"],
)
def test_schema_rejection_falls_back_to_forced_tool_call(ctx: CallContext, message: str) -> None:
    from r2r.llm import gateway
    from r2r.llm.client import LLMCallError

    fake = FakeLLM(native_schema=True)
    ctx = CallContext(**{**ctx.__dict__, "client": fake})
    rejection = LLMCallError(message)
    fake.script("long_planner", rejection, roadmap(), roadmap())
    model = ctx.settings.models.roles["long_planner"].model
    gateway._SCHEMA_REJECTED.discard(model)
    assert _call(ctx).goal == roadmap().goal
    first, second = fake.calls
    assert first.response_format is not None and first.tools is None
    assert second.response_format is None and second.tool_choice["function"]["name"]
    _call(ctx)  # the model is remembered: no second rejection round-trip
    assert fake.calls[2].response_format is None
    gateway._SCHEMA_REJECTED.discard(model)


def test_other_bad_requests_are_not_swallowed(ctx: CallContext) -> None:
    from r2r.llm.client import LLMCallError

    fake = FakeLLM(native_schema=True)
    ctx = CallContext(**{**ctx.__dict__, "client": fake})
    fake.script("long_planner", LLMCallError("BadRequestError: context length exceeded"))
    with pytest.raises(LLMCallError, match="context length"):
        _call(ctx)


def test_structured_attempts_is_configurable(ctx: CallContext, fake: FakeLLM) -> None:
    """Owner request (study-3): more repair attempts before a ContractViolation."""
    s = ctx.settings
    three = s.model_copy(
        update={"budgets": s.budgets.model_copy(update={"structured_attempts": 3})}
    )
    ctx3 = CallContext(**{**ctx.__dict__, "settings": three})
    fake.script("long_planner", "not json", "still not json", roadmap())
    assert _call(ctx3).goal == roadmap().goal
    assert len([c for c in fake.calls if c.role == "long_planner"]) == 3
    fake.script("long_planner", "bad", "bad")
    with pytest.raises(ContractViolation):
        _call(ctx)  # the pinned test config keeps 2


def test_grammar_schema_has_no_item_counts_but_the_prompt_and_validation_keep_them(
    ctx: CallContext, fake: FakeLLM
) -> None:
    """study-3 M9 (probed live): Google's structured output rejects a schema whose nested list
    size limits add up (INVALID_ARGUMENT). The provider grammar drops maxItems/minItems; the
    model still reads them in the prompt's schema text, and validation still enforces them."""
    fake.script("long_planner", roadmap())
    _call(ctx)
    req = fake.calls[0]
    grammar = json.dumps(req.response_format)
    assert "maxItems" not in grammar and "minItems" not in grammar
    assert "maxLength" in grammar  # other constraints stay in the grammar
    assert "maxItems" in req.messages[0]["content"][0]["text"]  # the limits are still stated
    too_many = roadmap().model_dump(exclude={"created_by", "run_id", "milestone_id"})
    too_many["milestones"] = too_many["milestones"] * 11  # > 20 milestones
    fake.script("long_planner", json.dumps(too_many), json.dumps(too_many), json.dumps(too_many))
    with pytest.raises(ContractViolation):
        _call(ctx)


def test_forced_tool_refusal_falls_back_to_an_offered_tool(ctx: CallContext) -> None:
    """study-3 M9-M11: Anthropic refuses the big schema natively, and LiteLLM refuses to force a
    tool for Sonnet 5.5 (locally, before any API call). The last step offers the submit tool with
    tool_choice="auto": every provider accepts it, with or without thinking."""
    from r2r.llm import gateway
    from r2r.llm.client import LLMCallError

    fake = FakeLLM(native_schema=True)
    ctx = CallContext(**{**ctx.__dict__, "client": fake})
    too_complex = LLMCallError("AnthropicException - Schema is too complex.")
    no_forced = LLMCallError(
        "UnsupportedParamsError: claude-sonnet-5-5 does not support forced tool use "
        "(tool_choice='required' or a named tool)"
    )
    model = ctx.settings.models.roles["long_planner"].model
    gateway._SCHEMA_REJECTED.discard(model)
    gateway._TOOL_AUTO.discard(model)
    fake.script("long_planner", too_complex, no_forced, roadmap(), roadmap())
    assert _call(ctx).goal == roadmap().goal
    native, forced, offered = fake.calls
    assert native.response_format is not None
    assert forced.tool_choice == {"type": "function", "function": {"name": "submit_roadmap"}}
    assert offered.tools and offered.tool_choice == "auto" and offered.response_format is None
    assert "submit_roadmap" in offered.messages[-1]["content"]  # told to call it
    _call(ctx)  # remembered for this model: straight to the offered tool
    assert fake.calls[3].tool_choice == "auto"
    gateway._SCHEMA_REJECTED.discard(model)
    gateway._TOOL_AUTO.discard(model)


def test_offered_tool_answered_in_text_is_still_parsed(ctx: CallContext) -> None:
    from r2r.llm import gateway

    fake = FakeLLM(native_schema=True)
    ctx = CallContext(**{**ctx.__dict__, "client": fake})
    model = ctx.settings.models.roles["long_planner"].model
    gateway._TOOL_AUTO.add(model)
    fake.script("long_planner", "Here it is:\n```json\n" + roadmap().model_dump_json() + "\n```")
    try:
        assert _call(ctx).goal == roadmap().goal
    finally:
        gateway._TOOL_AUTO.discard(model)


def test_cut_off_replies_say_so_and_stop_after_two(ctx: CallContext, fake: FakeLLM) -> None:
    """study-3 M11: six designer replies hit the output limit before any answer; each retry was
    identical. A cut-off reply is named as such in the repair, and two in a row end the call."""
    from r2r.llm.client import LLMResponse

    cut = LLMResponse(model="m", text="", input_tokens=100, output_tokens=7000, truncated=True)
    fake.script("long_planner", cut, roadmap())  # cut once, then a valid answer
    assert _call(ctx).goal == roadmap().goal
    repair = fake.calls[1].messages[-1]["content"]
    assert "output limit" in repair and "shorter" in repair
    fake.script("long_planner", cut, cut, roadmap())
    with pytest.raises(ContractViolation, match="output limit"):
        _call(ctx)
    assert fake.pending("long_planner") == 1  # the third call was never made


def test_long_outputs_get_a_long_enough_timeout(ctx: CallContext, fake: FakeLLM) -> None:
    """study-3 M11: a 32,000-token design cannot be written within the default 300 s."""
    from r2r.llm.gateway import timeout_for

    assert timeout_for(4000) == 300
    assert timeout_for(32000) >= 900
    fake.script("long_planner", roadmap())
    _call(ctx)
    assert fake.calls[0].timeout_s == timeout_for(fake.calls[0].max_tokens)


def test_nested_objects_sent_as_json_strings_are_decoded(ctx: CallContext, fake: FakeLLM) -> None:
    """study-3 M11: with an offered tool the designer sent `spec` as a JSON string."""
    data = roadmap().model_dump(mode="json", exclude={"created_by", "run_id", "milestone_id"})
    data["milestones"] = json.dumps(data["milestones"])  # a list, stringified
    fake.script("long_planner", json.dumps(data))
    assert _call(ctx).milestones[0].id == "M1"
