"""Tests for audit.validate - the deterministic claim checker.

validate() is the non-LLM half of the tool. Given a Claim and a root directory
of source files, it assigns one of four outcomes by reading the cited file and
grepping for the numeric values quoted in the claim text.

The outcomes, in order of the branches exercised below:

  UNVERIFIED   - no source cited (empty source_file or source_type=unknown)
  CONTRADICTED - source cited but the file is missing, OR numbers quoted in
                 the claim do not appear in the file at all
  VERIFIED     - every number in the claim appears in the cited file
  PARTIAL      - either (a) some-but-not-all numbers match, or (b) no numbers
                 in the claim at all (qualitative claim, source exists, best
                 we can say is "plausible, not value-matched")

All tests use pytest's tmp_path fixture to stage real source files on disk,
so the file-system branch of validate() is exercised for real rather than
mocked.
"""

from pathlib import Path

from audit import Claim, validate


def _claim(text: str, source_file: str = "src.log", source_type: str = "log") -> Claim:
    return Claim(
        id="C1",
        text=text,
        section="Findings",
        source_type=source_type,
        source_file=source_file,
        locator="",
    )


def test_validate_no_source_cited_returns_unverified(tmp_path: Path):
    # source_file is empty - the claim has no citation at all.
    claim = _claim("80 errors were observed", source_file="", source_type="log")
    result = validate(claim, tmp_path)
    assert result.outcome == "UNVERIFIED"
    assert "no source" in result.validation_note.lower()


def test_validate_cited_file_missing_returns_contradicted(tmp_path: Path):
    # The claim cites a file that does not exist under source_root.
    claim = _claim("80 errors were observed", source_file="ghost.log")
    result = validate(claim, tmp_path)
    assert result.outcome == "CONTRADICTED"
    assert "missing" in result.validation_note.lower()


def test_validate_all_numbers_match_returns_verified(tmp_path: Path):
    # Stage a real log file that contains every number quoted in the claim.
    log = tmp_path / "src.log"
    log.write_text("2026-03-04T14:22:31 ERROR count=80 threshold=64\n", encoding="utf-8")
    claim = _claim("80 errors were observed against a threshold of 64")
    result = validate(claim, tmp_path)
    assert result.outcome == "VERIFIED"
    # Both numeric tokens should appear in the matched set.
    assert "80" in result.validation_note
    assert "64" in result.validation_note


def test_validate_partial_numeric_match_returns_partial(tmp_path: Path):
    # The log contains one of the quoted numbers (80) but not the other (64).
    log = tmp_path / "src.log"
    log.write_text("2026-03-04T14:22:31 ERROR count=80 threshold=999\n", encoding="utf-8")
    claim = _claim("80 errors were observed against a threshold of 64")
    result = validate(claim, tmp_path)
    assert result.outcome == "PARTIAL"
    assert "80" in result.validation_note
    assert "64" in result.validation_note  # reported as missing


def test_validate_qualitative_claim_with_source_returns_partial(tmp_path: Path):
    # No numbers in the claim text. Source exists, but validate() cannot
    # check a numeric match, so the best outcome is PARTIAL.
    log = tmp_path / "src.log"
    log.write_text("clock recovery failed repeatedly\n", encoding="utf-8")
    claim = _claim("clock recovery failed repeatedly")
    result = validate(claim, tmp_path)
    assert result.outcome == "PARTIAL"
    assert "qualitative" in result.validation_note.lower()


def test_validate_rejects_substring_collision(tmp_path: Path):
    # The whole reason this tool exists: a claim of "80 flows" must NOT be
    # verified against a log that only contains "8080" (a port number).
    # Naive substring matching would mark this VERIFIED and ship the fabrication.
    log = tmp_path / "src.log"
    log.write_text("listening on port 8080 for incoming traffic\n", encoding="utf-8")
    claim = _claim("80 flows were active")
    result = validate(claim, tmp_path)
    assert result.outcome == "CONTRADICTED", (
        "word-boundary matching must reject the substring collision 80 -> 8080"
    )


def test_validate_ignores_date_fragments_in_claim(tmp_path: Path):
    # A claim whose only digits are inside a date compound has no verifiable
    # numeric content and should fall through to the qualitative PARTIAL branch.
    log = tmp_path / "src.log"
    log.write_text("deployment record archived\n", encoding="utf-8")
    claim = _claim("The deployment completed on 2026-03-04 at 14:22:31")
    result = validate(claim, tmp_path)
    assert result.outcome == "PARTIAL"
    assert "qualitative" in result.validation_note.lower()
