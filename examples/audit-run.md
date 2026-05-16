# Example audit run

This walkthrough is consistent with the actual behaviour of `src/audit.py`. The
numbers below are what the deterministic validator really produces, not a
hand-drawn illustration. (A source-traceability tool whose own example is not
source-accurate would be self-refuting.)

## Input document (`report.md`)

> The cutover moved 48 services with 0 dropped packets. Queue depth peaked at
> 12480 bytes on uplink ET1, below the 65536-byte hardware threshold. The PTP
> grandmaster ran on domain 7 throughout.

## Source files (staged under `--sources`)

`switch_counters.log`
```
2026-03-04T03:14:02 cutover services=48 drops=0
2026-03-04T03:14:09 ET1 queue_peak=12480
```

`network_runbook_v2.md`
```
## thresholds
Per-uplink hardware queue limit: 65536 bytes.
```

`ptp_sync.log`
```
2026-03-04T03:14:00 grandmaster locked on domain 0, 120 sync messages nominal
```

## Extracted claims (Claude Opus 4.7)

Claude proposes the claim list and the most likely source. It does not adjudicate.

| # | Claim | Proposed Source | source_type |
|---|-------|-----------------|-------------|
| C1 | 48 services moved | switch_counters.log | log |
| C2 | 0 dropped packets | switch_counters.log | log |
| C3 | Queue depth peaked at 12480 bytes on ET1 | switch_counters.log | log |
| C4 | 65536-byte hardware threshold | network_runbook_v2.md | manual |
| C5 | PTP grandmaster ran on domain 7 | ptp_sync.log | log |

## Validation (deterministic Python, no LLM)

`_extract_numbers` strips date/time compounds first (so `2026-03-04` and
`03:14:02` do not fragment), then matches whole numeric tokens with word
boundaries.

| # | Numbers extracted | Outcome | Note |
|---|-------------------|---------|------|
| C1 | `['48']` | VERIFIED | matched all tokens: `['48']` (`services=48`) |
| C2 | `['0']` | VERIFIED | matched all tokens: `['0']` (`drops=0`) |
| C3 | `['12480']` | VERIFIED | matched all tokens: `['12480']` |
| C4 | `['65536']` | VERIFIED | matched all tokens: `['65536']` |
| C5 | `['7']` | **CONTRADICTED** | no numeric match in ptp_sync.log for `['7']` |

C5 is the point. The report says the grandmaster ran on **domain 7**. The raw
log says **domain 0**. The token `7` does not appear in `ptp_sync.log` as a
whole token (`120` is one token; `0` is the only standalone digit, and the
claim does not contain `0`), so `validate()` returns CONTRADICTED.

## Gate

```
Total Claims:  5
Verified:      4 (80%)
Contradicted:  1
Gate Result:   FAIL
```

`gate()` treats any single contradiction as a hard veto, regardless of the
verified percentage. 4 of 5 verified would otherwise be 80% (a REVIEW), but the
one contradicted claim forces FAIL. Process exit code: `2`. The document does
not ship.

## What happened

One number was wrong. The LLM was confident. The gate held the release before
`domain 7` propagated into a customer update. A human now reconciles C5 against
`ptp_sync.log`, corrects the report to `domain 0`, and re-runs the audit, which
then returns PASS.

This is the whole value: **one number, wrong, caught by deterministic code
rather than by the model that produced it.**
