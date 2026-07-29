"""core.eqcommands — the curated slash-command and token completion list."""

from nparseplus.core import eqcommands
from nparseplus.core.eqcommands import COMMANDS, COMPLETIONS, TOKENS


def test_commands_are_unique_and_well_formed() -> None:
    names = [c.command for c in COMMANDS]
    assert len(names) == len(set(names))
    for command in COMMANDS:
        assert command.command.startswith("/")
        assert command.description and not command.description.endswith(".")
        assert command.category


def test_tokens_are_unique_and_percent_prefixed() -> None:
    names = [token for token, _ in TOKENS]
    assert len(names) == len(set(names))
    assert all(token.startswith("%") for token in names)


def test_completions_cover_commands_and_tokens() -> None:
    assert "/pet attack" in COMPLETIONS
    assert "%T" in COMPLETIONS
    assert len(COMPLETIONS) == len(COMMANDS) + len(TOKENS)


def test_describe_known_and_unknown() -> None:
    assert "target" in eqcommands.describe("/assist").lower()
    assert eqcommands.describe("  %T  ") == "Your current target's name"
    assert eqcommands.describe("%t") == "Your current target's name"  # case-insensitive
    assert eqcommands.describe("/nonsense") == ""


def test_category_lookup() -> None:
    assert eqcommands.category("/pet attack") == "Pet"
    assert eqcommands.category("/unknown") == ""


def test_matching_is_prefix_based_and_case_insensitive() -> None:
    assert eqcommands.matching("/pet g") == [
        "/pet guard here",
        "/pet guard me",
        "/pet get lost",
    ]
    assert eqcommands.matching("/PET AT") == ["/pet attack"]
    assert eqcommands.matching("/zzz") == []


def test_matching_empty_prefix_returns_everything() -> None:
    assert eqcommands.matching("") == COMPLETIONS
    assert len(eqcommands.matching("", limit=3)) == 3


def test_matching_honours_the_limit() -> None:
    assert len(eqcommands.matching("/pet", limit=2)) == 2
