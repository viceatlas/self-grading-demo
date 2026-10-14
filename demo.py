#!/usr/bin/env python3
"""self-grading-demo — a tiny agent pipeline that grades its own output.

    python demo.py            replay mode: bundled fixtures, no keys, deterministic
    python demo.py --live     live mode: Anthropic API (pip install anthropic,
                              ANTHROPIC_API_KEY set); same code path, real model
    python demo.py --verify   walk ledger.jsonl and exit nonzero if any row was
                              edited, reordered or removed

Two stages: SYNTHESIZE turns an article into a brief with quoted evidence per
claim; JUDGE scores every claim strictly against its quoted evidence under the
frozen rubric in rubric.md. Every run appends one row to ledger.jsonl carrying
the rubric's sha256, the article's sha256, the row's own sha256 and the sha256
of the previous row, so the graded record is tamper-evident: edit a verdict,
a rubric byte or a row's position and --verify names the row where the chain
breaks.

Stdlib only in replay mode. Fail-loud everywhere: malformed model output is a
crash with the offending payload, never a silent default. An evidence quote
that is not a verbatim excerpt of the article is a contract violation too: the
judge only ever sees the quote, so an invented quote would be the one way to
smuggle a wrong claim past it.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUBRIC = HERE / "rubric.md"
ARTICLE = HERE / "fixtures" / "article.txt"
REPLAY = HERE / "fixtures" / "replay"
LEDGER = HERE / "ledger.jsonl"

MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000  # thinking tokens count against this on current Opus models; 4k truncates


def _c(code, text):
    """ANSI color only when stdout is a terminal; piped output (and the tests) stay plain."""
    if not sys.stdout.isatty():
        return text
    if os.name == "nt":
        os.system("")  # enable VT processing on legacy Windows consoles; a no-op elsewhere
    return "\033[%sm%s\033[0m" % (code, text)
VERDICTS = ("supported", "unsupported")

SYNTHESIZE_PROMPT = """You are the SYNTHESIZE stage of a research pipeline.
Turn the article below into a brief: a headline and 4-6 factual claims.
For each claim, include evidence_quote: the exact sentence from the article
that supports it, quoted verbatim. Respond with JSON only:
{"headline": str, "claims": [{"id": "c1", "text": str, "evidence_quote": str}]}

ARTICLE:
%s"""

JUDGE_PROMPT = """You are an adversarial fact-checking JUDGE. Grade each claim
STRICTLY against its evidence_quote under this frozen rubric (v%s):

%s

Respond with JSON only:
{"verdicts": [{"claim_id": str, "verdict": "supported"|"unsupported",
               "confidence": float 0..1, "reason": str}]}

CLAIMS:
%s"""


# ---------------------------------------------------------------------------
# the strict contract layer — model output either matches or the run dies loud
# ---------------------------------------------------------------------------

class ContractError(Exception):
    """Model output violated the JSON contract; carries the offending payload."""


def _fail(msg, payload):
    raise ContractError("%s\n--- offending payload ---\n%s" % (msg, json.dumps(payload, indent=2)[:2000]))


def _normspace(text):
    return " ".join(text.split())


def parse_json(text):
    """Fail-loud JSON extraction: tolerate a fenced block, nothing else."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    try:
        return json.loads(stripped)
    except ValueError as e:
        raise ContractError("model output is not JSON (%s):\n%s" % (e, text[:2000]))


def validate_brief(obj, article=None):
    """Shape check, plus grounding: every evidence_quote must be a verbatim excerpt of the article."""
    if not isinstance(obj, dict):
        _fail("brief must be an object", obj)
    if not isinstance(obj.get("headline"), str) or not obj["headline"].strip():
        _fail("brief.headline must be a non-empty string", obj)
    claims = obj.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 8:
        _fail("brief.claims must be a list of 1-8 claims", obj)
    seen = set()
    haystack = _normspace(article) if article is not None else None
    for c in claims:
        if not isinstance(c, dict):
            _fail("each claim must be an object", c)
        for field in ("id", "text", "evidence_quote"):
            if not isinstance(c.get(field), str) or not c[field].strip():
                _fail("claim.%s must be a non-empty string" % field, c)
        if c["id"] in seen:
            _fail("duplicate claim id %r" % c["id"], obj)
        seen.add(c["id"])
        if haystack is not None and _normspace(c["evidence_quote"]) not in haystack:
            _fail("claim.evidence_quote is not a verbatim excerpt of the article", c)
    return obj


def validate_verdicts(obj, claim_ids):
    if not isinstance(obj, dict) or not isinstance(obj.get("verdicts"), list):
        _fail("judge output must be {'verdicts': [...]}", obj)
    verdicts = obj["verdicts"]
    for v in verdicts:
        if not isinstance(v, dict):
            _fail("each verdict must be an object", v)
    got_ids = [v.get("claim_id") for v in verdicts]
    if sorted(got_ids, key=str) != sorted(claim_ids):
        _fail("judge must return exactly one verdict per claim (want %s, got %s)"
              % (sorted(claim_ids), sorted(got_ids, key=str)), obj)
    for v in verdicts:
        if v.get("verdict") not in VERDICTS:
            _fail("verdict must be one of %s" % (VERDICTS,), v)
        conf = v.get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
            _fail("confidence must be a number in [0, 1]", v)
        if not isinstance(v.get("reason"), str) or not v["reason"].strip():
            _fail("reason must be a non-empty string", v)
    return obj


# ---------------------------------------------------------------------------
# model backends: replay (fixtures) or live (Anthropic API)
# ---------------------------------------------------------------------------

def replay_backend(stage, _prompt, _schema):
    path = REPLAY / ("%s.json" % stage)
    return json.loads(path.read_text(encoding="utf-8"))


def live_backend(stage, prompt, schema):
    try:
        import anthropic
    except ImportError:
        sys.exit("live mode needs the SDK: pip install anthropic")
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        sys.exit("model declined the %s request (stop_reason=refusal)" % stage)
    if response.stop_reason == "max_tokens":
        sys.exit("model hit max_tokens before finishing the %s stage; raise MAX_TOKENS" % stage)
    usage = getattr(response, "usage", None)
    if usage is not None:
        print("  usage       %s: %s input / %s output tokens" % (stage, usage.input_tokens, usage.output_tokens))
    text = next(b.text for b in response.content if b.type == "text")
    return parse_json(text)


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "text": {"type": "string"},
                           "evidence_quote": {"type": "string"}},
            "required": ["id", "text", "evidence_quote"],
            "additionalProperties": False}},
    },
    "required": ["headline", "claims"],
    "additionalProperties": False,
}

VERDICTS_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {"type": "array", "items": {
            "type": "object",
            "properties": {"claim_id": {"type": "string"},
                           "verdict": {"type": "string", "enum": list(VERDICTS)},
                           "confidence": {"type": "number"},
                           "reason": {"type": "string"}},
            "required": ["claim_id", "verdict", "confidence", "reason"],
            "additionalProperties": False}},
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# the ledger: append-only by convention, tamper-evident by construction
# ---------------------------------------------------------------------------

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rubric_version(rubric_text):
    for line in rubric_text.splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return "unversioned"


def entry_digest(entry):
    """sha256 of the row's contents (every field but its own digest), canonical JSON."""
    body = {k: v for k, v in entry.items() if k != "sha256"}
    return sha256(json.dumps(body, sort_keys=True))


def _rows(path):
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_ledger(path=None):
    """Walk the ledger. Returns (ok, rows_checked, first_bad_row_or_None, reason)."""
    path = path or LEDGER
    rows = _rows(path)
    prev = ""
    for i, line in enumerate(rows, 1):
        try:
            entry = json.loads(line)
        except ValueError:
            return False, i, i, "row is not JSON"
        want_prev = sha256(prev) if prev else None
        if entry.get("prev_sha256") != want_prev:
            return False, i, i, "prev_sha256 does not match the previous row (reordered or removed?)"
        if entry.get("sha256") != entry_digest(entry):
            return False, i, i, "row digest does not match its contents (edited?)"
        prev = line
    return True, len(rows), None, "chain intact"


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------

def run(mode):
    backend = live_backend if mode == "live" else replay_backend
    article = ARTICLE.read_text(encoding="utf-8")
    rubric = RUBRIC.read_text(encoding="utf-8")
    version = rubric_version(rubric)
    rubric_hash = sha256(rubric)

    mode_note = "replay mode (no keys; --live uses the Anthropic API)" if mode == "replay" else "live mode (%s)" % MODEL
    print("SELF-GRADING PIPELINE -- %s" % mode_note)
    print("rubric v%s (sha256 %s...) frozen before judging\n" % (version, rubric_hash[:8]))

    brief = validate_brief(backend("synthesize", SYNTHESIZE_PROMPT % article, BRIEF_SCHEMA), article)
    print("SYNTHESIZE  %s -> brief: %r (%d claims, every quote verbatim)" % (ARTICLE.name, brief["headline"], len(brief["claims"])))

    claims_json = json.dumps(brief["claims"], indent=2)
    judged = validate_verdicts(
        backend("judge", JUDGE_PROMPT % (version, rubric, claims_json), VERDICTS_SCHEMA),
        [c["id"] for c in brief["claims"]],
    )
    print("JUDGE       every claim vs. its quoted evidence, evidence-only, exact-number rule\n")

    by_id = {c["id"]: c for c in brief["claims"]}
    supported = 0
    for v in sorted(judged["verdicts"], key=lambda v: v["claim_id"]):
        claim = by_id[v["claim_id"]]
        if v["verdict"] == "supported":
            supported += 1
            print("  %s %-3s %-50s (%.2f)" % (_c("32", "[PASS]"), v["claim_id"], claim["text"][:50], v["confidence"]))
        else:
            print("  %s %-3s %s" % (_c("1;31", "[FAIL]"), v["claim_id"], claim["text"]))
            print("         judge: %s" % v["reason"])
    total = len(judged["verdicts"])

    prev_line = _rows(LEDGER)[-1] if _rows(LEDGER) else ""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "model": MODEL if mode == "live" else "replay-fixtures",
        "rubric_version": version,
        "rubric_sha256": rubric_hash,
        "article_sha256": sha256(article),
        "headline": brief["headline"],
        "supported": supported,
        "total": total,
        "verdicts": judged["verdicts"],
        "prev_sha256": sha256(prev_line) if prev_line else None,
    }
    entry["sha256"] = entry_digest(entry)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    history = [json.loads(line) for line in _rows(LEDGER)]
    hist_supported = sum(e["supported"] for e in history)
    hist_total = sum(e["total"] for e in history)
    ok, checked, _bad, why = verify_ledger(LEDGER)
    print("\n%s     %d/%d claims supported" % (_c("1", "VERDICT"), supported, total))
    print("LEDGER      row appended to %s (rubric hash, article hash, prev-row hash); %s across %d row(s); history: %d/%d"
          % (LEDGER.name, why, checked, hist_supported, hist_total))
    if not ok:
        sys.exit("LEDGER CHAIN BROKEN at row %d: %s" % (_bad, why))
    return entry


def main():
    parser = argparse.ArgumentParser(description="a tiny agent pipeline that grades its own output")
    parser.add_argument("--live", action="store_true", help="call the Anthropic API instead of replaying fixtures")
    parser.add_argument("--verify", action="store_true", help="verify the ledger chain and exit (0 intact, 2 broken)")
    args = parser.parse_args()
    if args.verify:
        ok, checked, bad, why = verify_ledger()
        print("LEDGER VERIFY  %s: %s (%d row(s)%s)" % (_c("32", "OK") if ok else _c("1;31", "BROKEN"), why, checked, "" if ok else ", first bad row %d" % bad))
        sys.exit(0 if ok else 2)
    try:
        run("live" if args.live else "replay")
    except ContractError as e:
        print("\nCONTRACT VIOLATION (fail-loud by design):\n%s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
