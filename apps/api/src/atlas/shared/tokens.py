"""Deterministic token counting for chunk budgeting.

One counter for the whole system (M05 risk note in docs/60): the chunker,
the cache key, and every consumer budget with the same numbers, so a chunk
sized here is never resized differently elsewhere.

This is a word-and-punctuation approximation, not the embedding model's
own tokenizer. That is deliberate: nomic-embed-text uses a WordPiece
vocabulary that would require shipping model assets to count exactly, and
spine §10 makes sizes *targets* — the 1,024 hard max is the only invariant.
English prose runs ≈1.3 WordPiece tokens per word, so a 1,024-token chunk
here stays comfortably inside the model's 2,048-token context window.
"""

import re

# A token is a maximal run of word characters or one non-space symbol —
# never spanning whitespace, so counts are additive across joins.
_TOKEN = re.compile(r"\w+|[^\w\s]")


def count_tokens(text: str) -> int:
    """Count budget tokens in ``text``. Deterministic, locale-independent."""
    return len(_TOKEN.findall(text))
