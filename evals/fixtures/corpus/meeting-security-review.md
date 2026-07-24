# Security Review Board — March Minutes

## Findings

The quarterly review found two medium findings: webhook signing keys were
rotated manually and irregularly, and staging environments retained
production data snapshots older than the 30-day policy allows.

## Decisions

Webhook keys move to automated 90-day rotation through the secrets manager
by end of April. Staging snapshot cleanup becomes a scheduled job with an
audit trail, owned by the platform team.

## Follow-ups

A tabletop exercise for a data-exfiltration scenario is scheduled for May,
run jointly with the incident response guild.
