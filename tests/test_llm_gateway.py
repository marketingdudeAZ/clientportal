"""Tests for skills.llm_gateway. No live API calls."""

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

from skills import llm_gateway as gw  # noqa: E402


def _fake_message(text="hello", *, stop_reason="end_turn", thinking=None,
                  in_tok=100, out_tok=50, cache_read=0):
    blocks = []
    if thinking is not None:
        blocks.append(types.SimpleNamespace(type="thinking", thinking=thinking))
    blocks.append(types.SimpleNamespace(type="text", text=text))
    return types.SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        stop_details=None,
        usage=types.SimpleNamespace(
            input_tokens=in_tok, output_tokens=out_tok,
            cache_read_input_tokens=cache_read),
    )


@pytest.fixture(autouse=True)
def _reset():
    gw._client = None
    gw._warned_aliases.clear()
    yield
    gw._client = None


class TestModelResolution:
    def test_stale_config_constants_map_forward(self):
        # config.py still carries these; 20 modules read them.
        assert gw.resolve_model("claude-sonnet-4-5") == "claude-sonnet-5"
        assert gw.resolve_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"

    def test_default_is_opus(self):
        assert gw.resolve_model(None) == "claude-opus-5"
        assert gw.DEFAULT_MODEL == "claude-opus-5"

    def test_shorthand_resolves(self):
        assert gw.resolve_model("haiku") == "claude-haiku-4-5"

    def test_current_ids_pass_through(self):
        assert gw.resolve_model("claude-opus-5") == "claude-opus-5"

    def test_no_current_id_carries_a_date_suffix(self):
        # Model ids are complete as-is; a date suffix is a 404 waiting to happen.
        for mid in list(gw.MODELS.values()) + list(gw.MODEL_ALIASES.values()):
            assert not mid[-9:].lstrip("-").isdigit(), mid

    def test_stale_alias_warns_once(self, caplog):
        with caplog.at_level("WARNING"):
            gw.resolve_model("claude-sonnet-4-5")
            gw.resolve_model("claude-sonnet-4-5")
        assert sum("out of date" in r.message for r in caplog.records) == 1


class TestCost:
    def test_opus_pricing(self):
        r = gw.Response(text="", model="claude-opus-5",
                        input_tokens=1_000_000, output_tokens=1_000_000)
        assert r.cost_usd == pytest.approx(30.00)

    def test_cache_reads_are_cheaper(self):
        plain = gw.Response(text="", model="claude-opus-5", input_tokens=1_000_000)
        cached = gw.Response(text="", model="claude-opus-5", cache_read_tokens=1_000_000)
        assert cached.cost_usd == pytest.approx(plain.cost_usd * 0.1)

    def test_unknown_model_costs_zero_rather_than_raising(self):
        assert gw.Response(text="", model="something-new", input_tokens=99).cost_usd == 0.0


class TestComplete:
    def test_returns_text_and_usage(self):
        client = MagicMock()
        client.messages.create.return_value = _fake_message("the answer")
        with patch.object(gw, "_get_client", return_value=client):
            r = gw.complete("q", purpose="test")
        assert r.text == "the answer"
        assert r.input_tokens == 100 and r.output_tokens == 50
        assert r.model == "claude-opus-5"

    def test_str_matches_text_so_old_callers_keep_working(self):
        client = MagicMock()
        client.messages.create.return_value = _fake_message("body")
        with patch.object(gw, "_get_client", return_value=client):
            assert str(gw.complete("q")) == "body"

    def test_adaptive_thinking_not_budget_tokens(self):
        client = MagicMock()
        client.messages.create.return_value = _fake_message()
        with patch.object(gw, "_get_client", return_value=client):
            gw.complete("q", thinking=True)
        sent = client.messages.create.call_args.kwargs
        assert sent["thinking"] == {"type": "adaptive"}
        assert "budget_tokens" not in str(sent), (
            "budget_tokens is rejected with a 400 on current models"
        )

    def test_effort_goes_inside_output_config(self):
        client = MagicMock()
        client.messages.create.return_value = _fake_message()
        with patch.object(gw, "_get_client", return_value=client):
            gw.complete("q", effort="high")
        sent = client.messages.create.call_args.kwargs
        assert sent["output_config"] == {"effort": "high"}
        assert "effort" not in {k for k in sent if k != "output_config"}

    def test_cache_system_marks_the_system_block(self):
        client = MagicMock()
        client.messages.create.return_value = _fake_message()
        with patch.object(gw, "_get_client", return_value=client):
            gw.complete("q", system="big context", cache_system=True)
        sys_param = client.messages.create.call_args.kwargs["system"]
        assert sys_param[0]["cache_control"] == {"type": "ephemeral"}

    def test_large_max_tokens_switches_to_streaming(self):
        client = MagicMock()
        stream_cm = MagicMock()
        stream_cm.__enter__.return_value.get_final_message.return_value = _fake_message()
        client.messages.stream.return_value = stream_cm
        with patch.object(gw, "_get_client", return_value=client):
            gw.complete("q", max_tokens=64_000)
        assert client.messages.stream.called
        assert not client.messages.create.called

    def test_thinking_text_is_captured(self):
        client = MagicMock()
        client.messages.create.return_value = _fake_message(thinking="reasoning here")
        with patch.object(gw, "_get_client", return_value=client):
            assert gw.complete("q").thinking == "reasoning here"


class TestFailuresAreLoud:
    def test_missing_key_raises_rather_than_returning_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(gw.LLMNotConfigured):
                gw.complete("q")

    def test_refusal_raises_with_category(self):
        msg = _fake_message(stop_reason="refusal")
        msg.stop_details = types.SimpleNamespace(category="cyber", explanation="no")
        client = MagicMock()
        client.messages.create.return_value = msg
        with patch.object(gw, "_get_client", return_value=client):
            with pytest.raises(gw.LLMRefusal) as exc:
                gw.complete("q")
        assert exc.value.category == "cyber"

    def test_api_error_becomes_llm_error(self):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("boom")
        with patch.object(gw, "_get_client", return_value=client):
            with pytest.raises(gw.LLMError):
                gw.complete("q")

    def test_never_returns_empty_string_on_failure(self):
        # The hyly_client lesson: absence and failure must not look alike.
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("down")
        with patch.object(gw, "_get_client", return_value=client):
            try:
                r = gw.complete("q")
            except gw.LLMError:
                return
            pytest.fail(f"returned {r!r} instead of raising")


class TestUsageAccounting:
    def test_metrics_failure_does_not_break_the_call(self):
        # A successful call has already cost money. Losing its usage row is bad;
        # throwing the answer away because we could not record the row is worse.
        client = MagicMock()
        client.messages.create.return_value = _fake_message("fine")
        with patch.object(gw, "_get_client", return_value=client), \
             patch.object(gw, "_emit_usage", side_effect=RuntimeError("BQ down")):
            assert gw.complete("q").text == "fine"

    def test_emit_usage_swallows_its_own_errors(self):
        with patch.dict(sys.modules, {"loop_writer": None}):
            gw._emit_usage(gw.Response(text="", model="claude-opus-5"), "t", 1)
