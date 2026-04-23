"""Tests for audit.gate - the release-decision function.

gate() maps a list of validated claims to one of three outcomes:
  - PASS    when verified% >= 90 and no contradictions
  - REVIEW  when 70 <= verified% < 90 and no contradictions
  - FAIL    when verified% < 70, OR any single contradiction regardless of %

A contradiction is a hard veto. This matters: a document can have 99 verified
claims and one contradicted claim and still FAIL. That is the intended
behaviour - a single fabricated number poisons the release.
"""

from audit import Claim, gate


def _claim(outcome: str, cid: str = "C1") -> Claim:
    """Build a minimal Claim with only the outcome field populated.

    The other fields are irrelevant to gate() - it only inspects .outcome.
    """
    return Claim(
        id=cid,
        text="irrelevant",
        section="irrelevant",
        source_type="log",
        source_file="x.log",
        locator="",
        outcome=outcome,
    )


def test_gate_pass_at_or_above_90_percent():
    # 9 verified out of 10 = 90.0% exactly - the PASS threshold is inclusive.
    claims = [_claim("VERIFIED", f"C{i}") for i in range(9)] + [_claim("PARTIAL", "C10")]
    result, pct = gate(claims)
    assert result == "PASS"
    assert pct == 0.9


def test_gate_review_between_70_and_90_percent():
    # 8 verified out of 10 = 80% - falls in the REVIEW band.
    claims = [_claim("VERIFIED", f"C{i}") for i in range(8)] + [
        _claim("PARTIAL", "C9"),
        _claim("UNVERIFIED", "C10"),
    ]
    result, pct = gate(claims)
    assert result == "REVIEW"
    assert pct == 0.8


def test_gate_fail_below_70_percent_threshold():
    # 5 verified out of 10 = 50% - below the REVIEW floor, hard FAIL.
    claims = [_claim("VERIFIED", f"C{i}") for i in range(5)] + [
        _claim("UNVERIFIED", f"C{i}") for i in range(5, 10)
    ]
    result, pct = gate(claims)
    assert result == "FAIL"
    assert pct == 0.5


def test_gate_fail_on_any_contradiction_even_at_99_percent():
    # 99 verified + 1 contradicted. Verified% would be 99% (a PASS by
    # threshold), but a single contradiction is a hard veto.
    claims = [_claim("VERIFIED", f"C{i}") for i in range(99)]
    claims.append(_claim("CONTRADICTED", "C100"))
    result, pct = gate(claims)
    assert result == "FAIL"
    assert pct == 0.99
