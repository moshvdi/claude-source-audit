# claude-source-audit

A minimal Claude-powered evaluation framework that enforces **source traceability** on any technical document an LLM produces. Every factual claim must cite its source file, line, or page. Claims without sources are flagged. Documents below a verification threshold do not ship.

Built on the [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python). Designed to be dropped into any RAG, agent, or document-generation pipeline as a **gate** rather than a hope.

## Why this exists

LLMs fabricate numbers. Not occasionally - routinely. In production workflows where a single invented statistic can propagate into a customer email, a JIRA escalation, or a board report, "the model said so" is not an acceptable audit trail.

This tool treats factual grounding as a **testable property**, not a stylistic preference. It wraps any Claude output in a pre-release check that reads the raw sources, greps for the claimed values, and either passes the document, flags it for human review, or blocks it.

## How it works

```
document.md  ->  claim extractor  ->  source validator  ->  gate
                  (Claude 4.7)        (grep / read)        (PASS | REVIEW | FAIL)
```

1. **Claim extraction.** Claude 4.7 reads the target document and enumerates every factual claim (numbers, named entities, causal statements, comparisons) into a structured list with a suggested source citation.
2. **Source validation.** For each claim, the validator opens the cited file and greps for the value or semantic equivalent. No cite -> UNVERIFIED. Cite points to missing file -> CONTRADICTED.
3. **Gate.** The output is a verification percentage and one of three outcomes:
   - **PASS** (>= 90% verified, 0 contradictions) -> release
   - **REVIEW** (70 to 89% verified, 0 contradictions) -> human confirmation required
   - **FAIL** (< 70% OR any contradictions) -> revise

## Eval rubric

Every run produces a Source Traceability Map with this schema:

| # | Claim | Section | Source Type | Citation | Raw Validation | Outcome |
|---|-------|---------|-------------|----------|----------------|---------|
| C1 | (claim text) | (section) | log/manual/config/pcap | (file:line) | (grep result) | VERIFIED / UNVERIFIED / CONTRADICTED / PARTIAL |

And a summary block:

```
Total Claims:      N
Verified:          N (X%)
Unverified:        N (X%)
Contradicted:      N (X%)
Gate Result:       PASS | REVIEW | FAIL
```

See [examples/audit-run.md](examples/audit-run.md) for a full walked example.

## Installation

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
claude-source-audit path/to/document.md --sources path/to/sources/
```

## Design notes

- **Claude 4.7 for extraction, not validation.** The LLM proposes the claim list and the suggested cite. Validation is deterministic Python reading the raw files. This is the split that matters: LLMs are excellent at structuring unstructured text, terrible at being the source of truth for their own output.
- **Prompt caching on the source manifest.** The extraction call sends the source manifest in a cached system block (`cache_control: ephemeral`), so repeat audits over the same source corpus do not re-tokenise the manifest on every call. See `src/audit.py::extract_claims`.
- **Whole-token numeric matching.** The validator uses word-boundary matching and filters date-fragment tokens, so a claim of `"80"` will not be falsely verified against `"8080"` in the source. Substring matching is how naive grounding checks fail; the tool exists specifically to avoid that failure mode.
- **Threshold tuning.** The 90/70 gate is a default, not a truth. Safety-critical domains should tighten to 95/85. Marketing copy can loosen. The rubric is the product; the numbers are parameters.

### Roadmap (not yet implemented)

- **Back-propagation across document dependency graphs.** If an audit of a derivative document detects a corrected value, follow the dependency chain upstream and flag the source analysis document as stale. Currently the tool audits one document at a time; multi-document DAG traversal is the v0.2 target.
- **Tool-use validator.** Replace the regex validator with a Claude tool-use loop that reads files via an explicit `read_file` tool, producing an auditable trace of every check. Higher cost, stronger evidence chain.

## Tests

The deterministic half of the tool (gate thresholds, claim validation) is covered by a small pytest suite. The LLM extraction path is intentionally not mocked - that is integration surface, not unit surface.

```bash
pip install -e ".[tests]"
pytest tests/
```

Two test files, nine tests total: `tests/test_gate.py` covers the four gate branches (PASS, REVIEW, FAIL on threshold, FAIL on contradiction) and `tests/test_validate.py` covers the five validation branches (no source, missing file, full numeric match, partial numeric match, qualitative claim) using `tmp_path` fixtures to stage real source files.

## Status

This repository is a public distillation of an audit framework the author uses in a private enterprise workflow. It is deliberately small, deliberately opinionated, and deliberately focused on the one property that matters: **no number ships without a source**.

## Author

Michael Oshodi - London, UK.
Built as part of ongoing work on the Claude Developer Platform.
