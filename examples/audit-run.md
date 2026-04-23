# Example audit run

## Input document (`report.md`)

> The deployment cutover completed at 03:14 with no dropped packets across the
> 48-port leaf. Queue depth peaked at 12,480 bytes on uplink ET1, well below the
> 64KB threshold. Loss events on domain 0 appeared in 3 of 120 PTP sync messages.

## Source manifest

```
capture_cutover.pcap
switch_counters.log
ptp_sync.log
network_runbook_v2.md
```

## Extracted claims (Claude 4.7)

| # | Claim | Proposed Source | Locator |
|---|-------|-----------------|---------|
| C1 | Cutover completed at 03:14 | switch_counters.log | timestamp pattern `03:14` |
| C2 | Zero dropped packets on 48-port leaf | switch_counters.log | line match "drops=0" |
| C3 | Queue depth peaked at 12,480 bytes on ET1 | switch_counters.log | line match "ET1" with queue value |
| C4 | 64KB threshold | network_runbook_v2.md | section "thresholds" |
| C5 | 3 of 120 PTP sync loss events on domain 0 | ptp_sync.log | domain filter |

## Validation

| # | Outcome | Note |
|---|---------|------|
| C1 | VERIFIED | matched: ['03:14'] |
| C2 | VERIFIED | matched: ['0'] in `drops=0` |
| C3 | VERIFIED | matched: ['12,480'] |
| C4 | VERIFIED | matched: ['64'] in runbook |
| C5 | **CONTRADICTED** | ptp_sync.log contains 7 of 120, not 3 of 120 |

## Gate

```
Verification: 80%  (4 of 5 VERIFIED)
Contradicted: 1
Gate Result: FAIL
```

Exit code 2. Document does not ship.

## What happened

The report claimed `3 of 120 PTP sync losses`. The raw log showed `7 of 120`. The gate
caught a fabricated (or stale) number before it reached the customer update email. A
human now reconciles C5 against the log, revises the report, and re-runs the audit.

This is the full value: **one number** was wrong, the LLM was confident, and the gate
held the release.
