# Testing Standards

## The pyramid

Unit tests run in under five minutes on every commit; integration tests run
against real containers on every pull request; end-to-end suites run
nightly. A flaky test gets quarantined the day it flakes and fixed or
deleted within two weeks.

## Coverage

We do not chase a coverage number. New code needs tests that would fail if
the behavior regressed; reviewers block PRs whose tests merely execute code
without asserting anything meaningful.

## Golden datasets

Retrieval and ranking changes must run the golden dataset suite before
merge. Baseline numbers live next to the datasets and move only through a
reviewed pull request.
