"""Short planner budget: compact context refs and an explicit token budget (pilot-1, M3)."""

import json

from samples import blob_ref, milestone, milestone_spec, roadmap

from r2r.agents.short_planner import MAX_REFS, REF_SUMMARY_CHARS, run_short_planner
from r2r.contracts import BlobRef, get_contract_caps
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import CallContext
from r2r.retrieval.context_pack import ContextPack

META = {"created_by", "run_id", "milestone_id", "context_refs"}


def _pack(n: int) -> ContextPack:
    refs = [
        BlobRef.model_validate({**blob_ref(i, "chunk").model_dump(), "summary": "s" * 300})
        for i in range(n)
    ]
    return ContextPack(refs=refs, text="<document>ctx</document>", tokens=10)


def test_refs_are_capped_and_trimmed(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("short_planner", milestone_spec().model_dump_json(exclude=META))
    spec = run_short_planner(roadmap(), milestone(1), _pack(8), None, [], ctx)
    assert len(spec.context_refs) == MAX_REFS
    assert all(len(r.summary) <= REF_SUMMARY_CHARS for r in spec.context_refs)


def test_planner_is_told_its_budget(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("short_planner", milestone_spec().model_dump_json(exclude=META))
    run_short_planner(roadmap(), milestone(1), _pack(8), None, [], ctx)
    payload = fake.calls[0].messages[1]["content"]
    payload = payload if isinstance(payload, str) else payload[0]["text"]
    data = json.loads(payload.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
    cap = get_contract_caps()["MilestoneSpec"]
    assert 600 < data["max_spec_tokens"] < cap
