"""
claude-source-audit: enforce source traceability on any LLM-produced document.

Design split:
  - Claude 4.7 extracts claims + proposed citations (good at structuring text).
  - Deterministic Python validates citations against raw source files (good at being correct).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from anthropic import Anthropic

MODEL = "claude-opus-4-7"
EXTRACTION_PROMPT = """You are a source-traceability auditor.

Read the DOCUMENT below. Extract every factual claim - specifically:
  - Numbers, percentages, counts, ratios, measurements, durations
  - Named entities tied to specific facts (e.g. "X shipped on date Y")
  - Causal statements ("A caused B")
  - Comparisons ("X is faster than Y")

For each claim, propose the most likely source file from the SOURCES list and the
most specific locator you can infer (line pattern, timestamp, page, section).

Output STRICT JSON:
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

DOCUMENT:
<<<
{document}
>>>

SOURCES:
<<<
{sources}
>>>
"""

GATE_PASS = 0.90
GATE_REVIEW = 0.70


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


def extract_claims(document: str, source_manifest: str, client: Anthropic) -> list[Claim]:
    prompt = EXTRACTION_PROMPT.replace("{document}", document).replace("{sources}", source_manifest)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    data = json.loads(text[start:end])
    return [Claim(**c) for c in data["claims"]]


def validate(claim: Claim, source_root: Path) -> Claim:
    if not claim.source_file or claim.source_type == "unknown":
        claim.outcome = "UNVERIFIED"
        claim.validation_note = "no source cited"
        return claim

    path = source_root / claim.source_file
    if not path.exists():
        claim.outcome = "CONTRADICTED"
        claim.validation_note = f"cited source missing: {path}"
        return claim

    content = path.read_text(encoding="utf-8", errors="ignore")
    numbers = re.findall(r"\d[\d,.\-:]*", claim.text)
    if not numbers:
        claim.outcome = "PARTIAL"
        claim.validation_note = "qualitative claim, source exists but not value-matched"
        return claim

    hits = [n for n in numbers if n in content]
    if len(hits) == len(numbers):
        claim.outcome = "VERIFIED"
        claim.validation_note = f"matched: {hits}"
    elif hits:
        claim.outcome = "PARTIAL"
        claim.validation_note = f"matched {hits}, missing {set(numbers) - set(hits)}"
    else:
        claim.outcome = "CONTRADICTED"
        claim.validation_note = f"no numeric match in {path.name}"
    return claim


def gate(claims: list[Claim]) -> tuple[str, float]:
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
    ap = argparse.ArgumentParser()
    ap.add_argument("document", type=Path)
    ap.add_argument("--sources", type=Path, required=True, help="root directory of source files")
    args = ap.parse_args()

    client = Anthropic()
    document = args.document.read_text(encoding="utf-8")
    manifest = "\n".join(sorted(p.name for p in args.sources.rglob("*") if p.is_file()))

    claims = extract_claims(document, manifest, client)
    claims = [validate(c, args.sources) for c in claims]
    result, pct = gate(claims)
    print(render_report(claims, result, pct))
    return 0 if result == "PASS" else 1 if result == "REVIEW" else 2


if __name__ == "__main__":
    sys.exit(main())
