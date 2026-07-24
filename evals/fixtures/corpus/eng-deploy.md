# Deployment Process for the Casimir Cluster

## Pipeline stages

Every merge to main triggers the Casimir deployment pipeline: build, unit
tests, integration tests against ephemeral environments, canary rollout to
5% of traffic, then full rollout over 30 minutes with automatic rollback on
error-budget burn.

## Deployment freeze

A deployment freeze, internally called the Zephyr window, applies during
Black Friday week and the last two business days of each quarter. Only
severity-1 hotfixes ship during a Zephyr window, and they need VP approval.

## Rollbacks

Rollbacks are one click in the Casimir console and complete in under four
minutes. Every rollback automatically opens an incident review ticket.
