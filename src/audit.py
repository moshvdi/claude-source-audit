"""
claude-source-audit: enforce source traceability on any LLM-produced document.

Design split:
  - Claude extracts claims + proposed citations (good at structuring text).
  - Deterministic Python validates citations against raw source files (good at being correct).

Self-validation is circular; using an LLM to judge its own output propagates the
fabrication. The LLM proposes, Python disposes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL = "claude-opus-4-7"
MAX_TOKENS = 4096

EXTRACTION_INSTRUCTIONS = """You are a source-traceability auditor.

Read the DOCUMENT below. Extract every factual claim - specifically:
  - Numbers, percentages, counts, ratios, measurements, durations
  - Named entities tied to specific facts (e.g. "X shipped on date Y")
  - Causal statements ("A caused B")
  - Comparisons ("X is faster than Y")

For each claim, propose the most likely source file from the SOURCES list and the
most specific locator you can infer (line pattern, timestamp, page, section).

Output STRICT JSON, nothing else. No prose before or after:
{
  "claims": [
    {
      "id": "C1",
      "text": "...",
      "section": "...",
      "source_type": "log | manual | config | pcap | case | runbook | external | unknown",
      "source_file": "...",
      "locator": "..."
    }
  ]
}
"""

GATE_PASS = 0.90
GATE_REVIEW = 0.70

# Strip date/time compounds before extracting numeric tokens, so "2026-03-04"
# and "14:22:31" do not fragment into "2026", "03", "04" and compete for
# validation attention. Then extract whole numeric tokens bounded by non-word
# characters, so claim "80" cannot falsely match "8080" in the source.
_DATE_LIKE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}(?::\d{2})?")
_NUM_TOKEN_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)*(?:%|[a-zA-Z]{1,3})?)(?![\w.])")


@dataclass
class Claim:
    id: str
    text: str
    section: str
    source_type: str
    source_file: str
    locator: str
    outcome: str = "PENDING"
    validation_note: str = ""


class ExtractionError(RuntimeError):
    """Claude returned output that could not be parsed as the expected schema."""


def _extract_numbers(claim_text: str) -> list[str]:
    """Return the numeric tokens in a claim, excluding date/time compounds."""
    stripped = _DATE_LIKE_RE.sub(" ", claim_text)
    return _NUM_TOKEN_RE.findall(stripped)


def _source_contains_token(token: str, content: str) -> bool:
    """Whole-token match: `80` must not match `8080`; `1.6` must not match `1.62`."""
    pattern = re.compile(rf"(?<![\w.]){re.escape(token)}(?![\w.])")
    return pattern.search(content) is not None


def extract_claims(document: str, source_manifest: str, client: Any) -> list[Claim]:
    """Extract factual claims from a document using Claude.

    Sends the extraction instructions + source manifest as cached system content
    (prompt caching on the source corpus means repeat audits over the same doc
    set do not re-tokenise evidence). The document itself is the user turn.
    """
    system_blocks = [
        {"type": "text", "text": EXTRACTION_INSTRUCTIONS},
        {
            "type": "text",
            "text": f"SOURCES:\n{source_manifest}",
            "cache_control": {"type": "ephemeral"},
        },
    ]

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            messages=[{"role": "user", "content": f"DOCUMENT:\n{document}"}],
        )
    except Exception as exc:
        raise ExtractionError(f"Claude API call failed: {exc}") from exc

    if not resp.content:
        raise ExtractionError(
            f"Claude returned empty content (stop_reason={getattr(resp, 'stop_reason', 'unknown')}). "
            "This can indicate a safety refusal or max_tokens hit before text was emitted."
        )

    text = resp.content[0].text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ExtractionError(f"Response contained no JSON object. Raw: {text[:200]!r}")

    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Response JSON failed to parse: {exc}. Raw: {text[:200]!r}") from exc

    if "claims" not in data:
        raise ExtractionError(f"Response missing 'claims' key. Got: {list(data)}")

    valid_fields = set(Claim.__dataclass_fields__)
    return [Claim(**{k: v for k, v in c.items() if k in valid_fields}) for c in data["claims"]]


def validate(claim: Claim, source_root: Path) -> Claim:
    """Validate a single claim against its cited source file.

    Deterministic only. No LLM here. Whole-token numeric matching with
    date-fragment filtering. If a log uses a non-UTF-8 encoding, we surface
    the replacement instead of silently dropping bytes.
    """
    if not claim.source_file or claim.source_type == "unknown":
        claim.outcome = "UNVERIFIED"
        claim.validation_note = "no source cited"
        return claim

    path = source_root / claim.source_file
    if not path.exists():
        claim.outcome = "CONTRADICTED"
        claim.validation_note = f"cited source missing: {path}"
        return claim

    content = path.read_text(encoding="utf-8", errors="replace")
    if "\ufffd" in content:
        claim.validation_note = "(encoding replacement characters present in source) "

    numbers = _extract_numbers(claim.text)
    if not numbers:
        claim.outcome = "PARTIAL"
        claim.validation_note += "qualitative claim, source exists but not value-matched"
        return claim

    hits = [n for n in numbers if _source_contains_token(n, content)]
    if len(hits) == len(numbers):
        claim.outcome = "VERIFIED"
        claim.validation_note += f"matched all tokens: {hits}"
    elif hits:
        claim.outcome = "PARTIAL"
        claim.validation_note += f"matched {hits}, missing {sorted(set(numbers) - set(hits))}"
    else:
        claim.outcome = "CONTRADICTED"
        claim.validation_note += f"no numeric match in {path.name} for {numbers}"
    return claim


def gate(claims: list[Claim]) -> tuple[str, float]:
    """Apply the verification threshold. Contradictions always FAIL regardless of percentage."""
    total = len(claims)
    if not total:
        return "FAIL", 0.0
    verified = sum(1 for c in claims if c.outcome == "VERIFIED")
    contradicted = sum(1 for c in claims if c.outcome == "CONTRADICTED")
    pct = verified / total
    if contradicted:
        return "FAIL", pct
    if pct >= GATE_PASS:
        return "PASS", pct
    if pct >= GATE_REVIEW:
        return "REVIEW", pct
    return "FAIL", pct


def render_report(claims: list[Claim], result: str, pct: float) -> str:
    lines = ["# Source Traceability Map", "", "| # | Claim | Source | Outcome | Note |", "|---|-------|--------|---------|------|"]
    for c in claims:
        text = c.text.replace("|", "/")[:100]
        lines.append(f"| {c.id} | {text} | {c.source_file} | {c.outcome} | {c.validation_note} |")
    lines += [
        "",
        "## Summary",
        f"- Total Claims: {len(claims)}",
        f"- Verified: {sum(1 for c in claims if c.outcome == 'VERIFIED')}",
        f"- Partial: {sum(1 for c in claims if c.outcome == 'PARTIAL')}",
        f"- Unverified: {sum(1 for c in claims if c.outcome == 'UNVERIFIED')}",
        f"- Contradicted: {sum(1 for c in claims if c.outcome == 'CONTRADICTED')}",
        f"- Verification: {pct:.0%}",
        f"- Gate Result: **{result}**",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(prog="claude-source-audit")
    ap.add_argument("document", type=Path, help="path to the document to audit")
    ap.add_argument("--sources", type=Path, required=True, help="root directory of source files")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY environment variable not set", file=sys.stderr)
        return 3
    if not args.document.is_file():
        print(f"error: document not found: {args.document}", file=sys.stderr)
        return 3
    if not args.sources.is_dir():
        print(f"error: --sources must be a directory: {args.sources}", file=sys.stderr)
        return 3

    from anthropic import Anthropic

    client = Anthropic()
    document = args.document.read_text(encoding="utf-8")
    manifest = "\n".join(sorted(p.name for p in args.sources.rglob("*") if p.is_file()))
    if not manifest:
        print(f"error: --sources directory is empty: {args.sources}", file=sys.stderr)
        return 3

    try:
        claims = extract_claims(document, manifest, client)
    except ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    claims = [validate(c, args.sources) for c in claims]
    result, pct = gate(claims)
    print(render_report(claims, result, pct))
    return 0 if result == "PASS" else 1 if result == "REVIEW" else 2


if __name__ == "__main__":
    sys.exit(main())
