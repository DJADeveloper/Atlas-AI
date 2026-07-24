# Research Note: Why RAG Answers Fail

## Failure taxonomy

Across 340 sampled bad answers we found four dominant failures: retrieval
missed the relevant passage entirely (41%), the passage was retrieved but
truncated mid-fact by chunking (22%), the model ignored retrieved context
(19%), and the source itself was stale (18%).

## The chunking lesson

Fixed-size windows caused nearly all truncation failures by splitting
tables and splitting a heading from its section. Structure-aware chunking
eliminated 80% of that class in the follow-up sample.

## Abstention

When retrieval confidence was low, answering anyway produced fabrications;
an abstention threshold converted most of those into honest refusals that
users rated higher than wrong answers.
