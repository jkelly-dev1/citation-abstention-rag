"""Provider selection and the tolerant parser.

Provider selection is a safety property: a stray environment variable must not
be able to send a demo run to a paid API, and a key alone must not select a
provider whose name was never asked for.
"""

from __future__ import annotations

from app.config import Settings
from app.llm import MockProvider, get_provider, parse_model_answer


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


def test_unparseable_output_declines_instead_of_raising():
    answer = parse_model_answer("I am afraid I cannot help with that.")
    assert answer.claims == []
    assert answer.model_declined is True


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


def test_every_unreadable_shape_declines_rather_than_raising():
    """All the ways a real model's output fails to parse, not just one.

    THE DOCSTRING ON `parse_model_answer` promises this for every shape, "a
    malformed model response abstains instead of crashing", and the README row
    says the same. Only the no-brace path was exercised, so the branch that
    catches a JSONDecodeError could be made to raise with the suite green, and
    a TRUNCATED reply is the most ordinary real-model failure there is: a
    length cap lands mid-object and the whole pipeline dies rather than
    abstaining.

    Mutation check, executed against the parser: make the JSONDecodeError
    branch raise instead of returning, and this goes red.

    The `not isinstance(payload, dict)` branch is NOT exercised here and no
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
        assert answer.model_declined is True, f"{label}: did not decline"
