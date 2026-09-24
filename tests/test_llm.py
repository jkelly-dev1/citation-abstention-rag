"""Provider selection and the tolerant parser.

Provider selection is a safety property: a stray environment variable must not
be able to send a demo run to a paid API, and a key alone must not select a
provider whose name was never asked for.
"""

from __future__ import annotations

import pathlib
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

from app.config import Settings
from app.llm import (
    AnthropicProvider,
    MockProvider,
    OpenAIProvider,
    get_provider,
    parse_model_answer,
    rejects_output_config,
)
from app.llm import HostileProvider


def test_default_provider_is_the_offline_mock():
    assert get_provider(Settings(agent_provider="mock")).name == "mock"


def test_provider_name_without_a_key_falls_back_to_mock():
    settings = Settings(agent_provider="anthropic", anthropic_api_key=None)
    assert get_provider(settings).name == "mock"


def test_key_without_the_matching_provider_name_stays_on_mock():
    settings = Settings(agent_provider="mock", anthropic_api_key="sk-ant-test")
    assert get_provider(settings).name == "mock"


def test_keys_do_not_cross_match_between_providers():
    settings = Settings(agent_provider="openai", anthropic_api_key="sk-ant-test")
    assert get_provider(settings).name == "mock"


def test_model_override_wins_for_whichever_provider_runs():
    settings = Settings(agent_model="pinned-model")
    assert settings.model_for("anthropic") == "pinned-model"
    assert settings.model_for("openai") == "pinned-model"


def test_parser_accepts_plain_json():
    answer = parse_model_answer(
        '{"claims": [{"text": "a", "citations": [{"chunk_id": "c#s01", "quote": "q"}]}],'
        ' "declined": false}'
    )
    assert len(answer.claims) == 1
    assert answer.claims[0].citations[0].chunk_id == "c#s01"


def test_parser_strips_code_fences_and_surrounding_prose():
    raw = (
        "Sure, here is the answer:\n```json\n"
        '{"claims": [{"text": "a", "citations": []}], "declined": false}\n'
        "```\nLet me know if you need more."
    )
    answer = parse_model_answer(raw)
    assert len(answer.claims) == 1


def test_unparseable_output_abstains_instead_of_raising():
    answer = parse_model_answer("I am afraid I cannot help with that.")
    assert answer.claims == []
    assert answer.parse_error is True


def test_an_unreadable_reply_is_not_recorded_as_the_model_declining():
    """The two must not be the same flag, or they cannot be the same triage.

    A length cap that truncates the JSON and a model that read the context and
    said no are different events with different fixes. Set both from one flag
    and the audit log gives them one reason code, so an operator cannot tell a
    broken integration from a working guardrail.

    Mutation check: set `model_declined=True` on the parse-failure paths in
    `parse_model_answer` and this goes red on the first assertion.
    """
    unreadable = parse_model_answer('{"claims": [{"text": "a",')
    assert unreadable.parse_error is True
    assert unreadable.model_declined is False

    honest = parse_model_answer('{"claims": [], "declined": true}')
    assert honest.model_declined is True
    assert honest.parse_error is False


def test_malformed_citation_entries_are_dropped_not_fatal():
    answer = parse_model_answer(
        '{"claims": [{"text": "a", "citations": ["not-an-object", {"quote": "no id"}]}],'
        ' "declined": false}'
    )
    assert answer.claims[0].citations == []


def test_mock_quotes_only_text_that_appears_in_its_context():
    context = (
        "[block_id: doc#s01] (document: Doc / Heading)\n"
        "Trade confirmations are retained for 7 years. Correspondence is kept for 3 years."
    )
    raw = MockProvider().complete(
        question="How long are trade confirmations retained?", context=context
    )
    answer = parse_model_answer(raw)
    assert answer.claims
    for claim in answer.claims:
        for citation in claim.citations:
            assert citation.quote in context


def test_every_unreadable_shape_abstains_rather_than_raising():
    """All the ways a real model's output fails to parse, not just one.

    The docstring on `parse_model_answer` promises this for every shape, and
    the README row says the same. A truncated reply is the most ordinary
    real-model failure there is: a length cap lands mid-object, and the
    JSONDecodeError branch is what keeps the pipeline answering with an
    abstention.

    Mutation check against the parser: make the JSONDecodeError branch raise
    instead of returning, and this goes red.

    The `not isinstance(payload, dict)` branch is not exercised here and no
    input can reach it: anything arriving at `json.loads` either starts with
    `{` or was sliced to start with `{`, and valid JSON beginning with `{` is
    an object. It is defensive and unreachable, and a test asserting otherwise
    would be asserting a shape that cannot occur.
    """
    shapes = {
        "prose, no JSON at all": "I am afraid I cannot help with that.",
        "truncated mid-object": '{"claims": [{"text": "a", "citations": [',
        "truncated after the opening brace": "{",
        "braces around invalid JSON": "{not valid json at all}",
        "fenced block wrapping invalid JSON": "```json\n{nope}\n```",
        "prose with an opening brace and no close": "here you go: {claims: ",
        "empty string": "",
    }
    for label, raw in shapes.items():
        answer = parse_model_answer(raw)
        assert answer.claims == [], f"{label}: invented claims"
        assert answer.parse_error is True, f"{label}: did not flag a parse error"
        # It must not claim the model declined either. An assertion that the
        # answer is empty would pass for either flag, and would let the two
        # events collapse into one code.
        assert answer.model_declined is False, f"{label}: impersonated a decline"


# --- the real-provider adapters, exercised WITHOUT the optional SDKs ---------
#
# `anthropic` and `openai` are optional dependencies (requirements.txt keeps
# both commented out), so a test that imported either would be skipped exactly
# where the coverage is thinnest. These build the provider with
# `object.__new__`, bypassing the __init__ that imports the SDK, and drive it
# with a fake client. No network, no key, no optional package.


class _FakeStatusError(Exception):
    """Shaped like an SDK HTTP error: both SDKs put `status_code` on theirs."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _RecordingMessages:
    def __init__(self, fail_first_with=None):
        self.calls = []
        self._fail_first_with = fail_first_with

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_first_with is not None and len(self.calls) == 1:
            raise self._fail_first_with
        return types.SimpleNamespace(
            stop_reason="end_turn",
            content=[types.SimpleNamespace(type="text", text='{"claims": []}')],
        )


def _anthropic_with(messages):
    provider = object.__new__(AnthropicProvider)
    provider._client = types.SimpleNamespace(messages=messages)
    provider.model = "test-model"
    provider.max_tokens = 64
    return provider


def test_rejects_output_config_covers_the_sdk_and_the_model_but_not_an_auth_failure():
    """The helper decides whether a retry can possibly repair the call.

    Mutation check: make it `return isinstance(exc, TypeError)` and the 400
    case goes red.
    """
    assert rejects_output_config(TypeError("unexpected keyword"))
    assert rejects_output_config(_FakeStatusError(400))
    # A retry cannot repair any of these, and retrying would send a second
    # doomed request and pay for it.
    assert not rejects_output_config(_FakeStatusError(401))
    assert not rejects_output_config(_FakeStatusError(429))
    assert not rejects_output_config(_FakeStatusError(500))
    assert not rejects_output_config(TimeoutError("read timeout"))


def test_a_model_that_refuses_output_config_falls_back_instead_of_failing():
    """A model that rejects the parameter gets the documented fallback.

    A stale SDK raises TypeError client-side. A model that will not take the
    parameter answers 400 over the wire, which is not a TypeError, and the
    fallback has to cover that case too.

    Mutation check: narrow the handler to `except TypeError` and this goes red,
    because the 400 propagates.
    """
    messages = _RecordingMessages(fail_first_with=_FakeStatusError(400))
    provider = _anthropic_with(messages)

    raw = provider.complete(question="q", context="c")

    assert raw == '{"claims": []}'
    # Two calls: the schema-constrained attempt, then the fallback.
    assert len(messages.calls) == 2
    assert "output_config" in messages.calls[0]
    # The fallback is the same request without the rejected parameter. That
    # is asserted as an absence, because "it retried" is not the claim.
    assert "output_config" not in messages.calls[1]


def test_an_auth_failure_is_not_retried_as_though_it_were_a_bad_parameter():
    messages = _RecordingMessages(fail_first_with=_FakeStatusError(401))
    provider = _anthropic_with(messages)

    # Name the failure. `pytest.raises(Exception)`, or any parent that a typo,
    # an AttributeError or an import error also satisfies, would pass for
    # reasons that have nothing to do with the retry rule this test is about.
    with pytest.raises(_FakeStatusError, match="HTTP 401") as caught:
        provider.complete(question="q", context="c")
    assert caught.value.status_code == 401

    # One call, not two. A retry here would be a second charged request that
    # cannot succeed.
    assert len(messages.calls) == 1


def test_an_empty_choices_list_abstains_rather_than_raising_indexerror():
    """A legitimate response shape the adapter must not index blindly.

    Mutation check: delete the `if not response.choices` guard and this goes
    red with IndexError instead of returning "".
    """
    provider = object.__new__(OpenAIProvider)
    provider.model = "test-model"
    provider.max_tokens = 64
    provider._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(
                create=lambda **kw: types.SimpleNamespace(choices=[])
            )
        )
    )

    raw = provider.complete(question="q", context="c")
    assert raw == ""
    # The rest of the system files the empty string as an unreadable reply,
    # not a crash.
    answer = parse_model_answer(raw)
    assert answer.parse_error is True
    assert answer.claims == []


def test_provider_selection_is_case_insensitive_and_rejects_an_unknown_name(
    monkeypatch,
):
    """The provider name is case-insensitive, and a typo is refused.

    Without case folding, `AGENT_PROVIDER=Anthropic` would silently resolve to
    the mock, and the same spelling rule would hand a real provider to an
    operator who typed "Mock". A name nobody recognizes is a configuration
    error, not a reason to guess.

    The anthropic SDK is optional and CI does not install it, so a stand-in
    module takes its place; building the provider sends nothing.

    Mutation check: drop the `.lower()` and this goes red on the first
    assertion; drop the membership test and it goes red on the second.
    """
    import sys

    stub = types.ModuleType("anthropic")
    stub.Anthropic = lambda api_key: types.SimpleNamespace(api_key=api_key)
    monkeypatch.setitem(sys.modules, "anthropic", stub)

    provider = get_provider(
        Settings(agent_provider="Anthropic", anthropic_api_key="sk-test")
    )
    assert provider.name == "anthropic"

    with pytest.raises(ValueError) as caught:
        get_provider(Settings(agent_provider="anthropci"))
    # The message must name what was actually set, or the operator cannot see
    # their own typo in it.
    assert "anthropci" in str(caught.value)

    # A known name without its key still falls back to the mock.
    assert get_provider(Settings(agent_provider="anthropic")).name == "mock"


def test_env_file_is_read_per_construction_not_once_at_import(tmp_path, monkeypatch):
    """ENV_FILE is resolved each time Settings is built.

    As a class-body expression, `env_file=_env_file()` would run once, when
    app.config is first imported, and bake in the ENV_FILE in force at that
    moment. A long-lived process that switched credentials files would keep
    reading the first.

    Mutation check: put `env_file=_env_file()` into SettingsConfigDict and drop
    the __init__, and this goes red, because the second Settings() returns
    model-from-A.
    """
    (tmp_path / "a.env").write_text("ANTHROPIC_MODEL=model-from-A\n", encoding="utf-8")
    (tmp_path / "b.env").write_text("ANTHROPIC_MODEL=model-from-B\n", encoding="utf-8")

    monkeypatch.setenv("ENV_FILE", str(tmp_path / "a.env"))
    assert Settings().model_for("anthropic") == "model-from-A"

    monkeypatch.setenv("ENV_FILE", str(tmp_path / "b.env"))
    assert Settings().model_for("anthropic") == "model-from-B"


def test_neither_sdk_is_imported_in_mock_mode():
    """README says both SDKs are imported lazily and not loaded in mock mode.

    That property is why this repository can say it runs fully offline with
    the optional providers installed, and a stray module-level import would
    break it silently.

    Mutation check: add `import anthropic` at the top of `app/llm.py` and this
    goes red.
    """
    import subprocess
    import sys as _sys

    # A subprocess, because this test suite may already have imported the SDK
    # for another test and `sys.modules` is process-wide. Asserting inside
    # this process would pass or fail on test order.
    probe = (
        "import sys;"
        "from app.config import Settings;"
        "from app.llm import get_provider;"
        "p = get_provider(Settings(agent_provider='mock'));"
        "print(p.name, 'anthropic' in sys.modules, 'openai' in sys.modules)"
    )
    out = subprocess.run([_sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    name, anthropic_loaded, openai_loaded = out.stdout.split()
    assert name == "mock"
    assert anthropic_loaded == "False", "the anthropic SDK was imported in mock mode"
    assert openai_loaded == "False", "the openai SDK was imported in mock mode"


def test_the_hostile_provider_cannot_get_an_unsupported_claim_served():
    """The central claim, against something actively attacking it.

    Mutation check: make `HostileProvider.complete` return the honest mock's
    output and this goes red, because nothing is refused.
    """
    from app.audit import AuditLog
    from app.pipeline import answer_question

    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as directory:
        log = AuditLog(_Path(directory) / "h.jsonl")
        result = answer_question(
            "Who approves an expense above 5,000 USD?", {"internal"},
            Settings(corpus_dir="corpus",
                     audit_log_path=str(_Path(directory) / "h.jsonl")),
            provider=HostileProvider(), audit=log,
        )
    # Nothing unsupported may be served.
    assert all(claim.supported for claim in result.claims)
    # The attacks must actually have been attempted, or this test would pass
    # against a provider that did nothing.
    refused = {reason for claim in result.dropped for reason in claim.reasons}
    assert "negation_mismatch" in refused
    assert "ungrounded_number" in refused
    assert "no_verified_citation" in refused
