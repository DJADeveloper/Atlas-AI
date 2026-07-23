"""Property tests for the shared token counter (M05 acceptance: the
tokenizer utility is property-tested; sizes are targets, the hard max is
the invariant, so the counter itself must be stable and additive)."""

from hypothesis import given
from hypothesis import strategies as st

from atlas.shared.tokens import count_tokens


class TestKnownCounts:
    def test_empty_is_zero(self) -> None:
        assert count_tokens("") == 0

    def test_whitespace_only_is_zero(self) -> None:
        assert count_tokens(" \n\t  ") == 0

    def test_words_and_punctuation_each_count(self) -> None:
        assert count_tokens("Hello, world!") == 4  # Hello , world !

    def test_unicode_words_count_as_words(self) -> None:
        assert count_tokens("café naïve 東京") == 3

    def test_markdown_symbols_count(self) -> None:
        assert count_tokens("# Heading") == 2


class TestProperties:
    @given(st.text())
    def test_non_negative_and_bounded_by_length(self, text: str) -> None:
        count = count_tokens(text)
        assert 0 <= count <= len(text)

    @given(st.text())
    def test_deterministic(self, text: str) -> None:
        assert count_tokens(text) == count_tokens(text)

    @given(st.text(), st.text())
    def test_additive_across_whitespace_joins(self, left: str, right: str) -> None:
        # Tokens never span whitespace, so budgeting block-by-block equals
        # budgeting the joined text — the chunker relies on this.
        assert count_tokens(f"{left} {right}") == count_tokens(left) + count_tokens(right)

    @given(st.text(min_size=1))
    def test_stripping_never_changes_count(self, text: str) -> None:
        assert count_tokens(text) == count_tokens(text.strip())
