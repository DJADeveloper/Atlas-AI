# Platform Architecture Overview

## Service topology

The Meridian platform is a modular monolith called Atlas Core surrounded by
three satellite services: Ledger for billing events, Semaphore for feature
flags, and Outrider for webhook delivery. All internal traffic uses mTLS.

## Data stores

PostgreSQL is the system of record; Redis handles queues and caching.
Analytical workloads replicate into ClickHouse with a fifteen-minute lag,
and nothing reads ClickHouse on a user-facing path.

## Golden rules

Services own their data — no cross-service database reads. Every API is
versioned, and breaking changes require a six-week deprecation window
announced in the changelog.
