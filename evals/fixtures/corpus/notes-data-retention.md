# Data Retention Schedule

## Customer content

Customer documents and their derived search indexes are retained while the
subscription is active and for 60 days after termination, then hard
deleted. Customers can request earlier deletion, completed within 14 days.

## Telemetry

Product analytics events are kept for 13 months at user granularity, then
aggregated. Application logs live for 30 days, security logs for one year,
and audit trails for seven years to satisfy enterprise contracts.

## Backups

Encrypted backups rotate on a 7-daily, 4-weekly cadence. Deletion requests
propagate to backups at restore time via a tombstone list checked before
any restore completes.
