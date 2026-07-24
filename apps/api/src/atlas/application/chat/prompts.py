"""Pinned prompt texts for M07 chat.

Plain constants with version tags recorded on every persisted message
(`messages.prompt_version`). The M08 prompt registry replaces this
module with versioned `prompts`/`prompt_versions` rows and an FK — the
version STRINGS here are chosen to survive that migration unchanged.
"""

CHAT_SYSTEM_PROMPT = """You are Atlas, a local-first assistant. Answer from the \
conversation and the provided context sections. Be direct and concise. If the \
context sections do not contain what you need, say so plainly instead of guessing."""
CHAT_PROMPT_VERSION = "chat_system.v1"

SUMMARIZE_PROMPT = """Fold the conversation transcript below into one running \
summary. Preserve concrete facts, names, numbers, decisions, and open questions - \
they must survive verbatim. Drop pleasantries and repetition. Write plain prose, \
at most 300 words, no preamble. If an existing summary is provided, merge it with \
the new turns into a single coherent summary."""
SUMMARIZE_PROMPT_VERSION = "conversation.summarize.v1"

TITLE_PROMPT = """Name this conversation. Reply with only the title: at most six \
words, no quotes, no trailing punctuation."""
TITLE_PROMPT_VERSION = "conversation.title.v1"
