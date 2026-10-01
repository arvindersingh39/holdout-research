"""Token estimates: model tokenizer when given, len/4 otherwise or on failure."""

from r2r.llm.tokens import estimate_tokens


def test_fallback_is_len_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 25) == 25
    assert estimate_tokens("abcde") == 2  # rounds up


def test_model_tokenizer_used() -> None:
    n = estimate_tokens("hello world " * 50, model="anthropic/claude-sonnet-5-5")
    assert 50 <= n <= 300


def test_unknown_model_falls_back() -> None:
    text = "x" * 400
    assert estimate_tokens(text, model="nobody/no-such-model-xyz") > 0
