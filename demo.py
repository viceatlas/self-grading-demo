#!/usr/bin/env python3
"""self-grading-demo — a tiny agent pipeline that grades its own output.

    python demo.py            replay mode: bundled fixtures, no keys, deterministic
    python demo.py --live     live mode: Anthropic API (pip install anthropic,
                              ANTHROPIC_API_KEY set); same code path, real model

Two stages: SYNTHESIZE turns an article into a brief with quoted evidence per
claim; JUDGE scores every claim strictly against its quoted evidence under the
frozen rubric in rubric.md. Verdicts are appended to ledger.jsonl with the
rubric's sha256, so the graded record is tamper-evident: re-grading history
would change hashes, and the rubric can't drift without the ledger showing it.

Stdlib only in replay mode. Fail-loud everywhere: malformed model output is a
crash with the offending payload, never a silent default.
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

MODEL = "claude-opus-5"
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


def validate_brief(obj):
    if not isinstance(obj, dict):
        _fail("brief must be an object", obj)
    if not isinstance(obj.get("headline"), str) or not obj["headline"].strip():
        _fail("brief.headline must be a non-empty string", obj)
    claims = obj.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 8:
        _fail("brief.claims must be a list of 1-8 claims", obj)
    seen = set()
    for c in claims:
        if not isinstance(c, dict):
            _fail("each claim must be an object", c)
        for field in ("id", "text", "evidence_quote"):
            if not isinstance(c.get(field), str) or not c[field].strip():
                _fail("claim.%s must be a non-empty string" % field, c)
        if c["id"] in seen:
            _fail("duplicate claim id %r" % c["id"], obj)
        seen.add(c["id"])
    return obj


def validate_verdicts(obj, claim_ids):
    if not isinstance(obj, dict) or not isinstance(obj.get("verdicts"), list):
        _fail("judge output must be {'verdicts': [...]}", obj)
    verdicts = obj["verdicts"]
    got_ids = [v.get("claim_id") for v in verdicts if isinstance(v, dict)]
    if sorted(got_ids) != sorted(claim_ids):
        _fail("judge must return exactly one verdict per claim (want %s, got %s)"
              % (sorted(claim_ids), sorted(got_ids)), obj)
    for v in verdicts:
        if v.get("verdict") not in VERDICTS:
            _fail("verdict must be one of %s" % (VERDICTS,), v)
        conf = v.get("confidence")
        if not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
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
        max_tokens=4096,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        sys.exit("model declined the %s request (stop_reason=refusal)" % stage)
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
# the pipeline
# ---------------------------------------------------------------------------

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rubric_version(rubric_text):
    for line in rubric_text.splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return "unversioned"


def run(mode):
    backend = live_backend if mode == "live" else replay_backend
    article = ARTICLE.read_text(encoding="utf-8")
    rubric = RUBRIC.read_text(encoding="utf-8")
    version = rubric_version(rubric)
    rubric_hash = sha256(rubric)

    mode_note = "replay mode (no keys; --live uses the Anthropic API)" if mode == "replay" else "live mode (%s)" % MODEL
    print("SELF-GRADING PIPELINE -- %s" % mode_note)
    print("rubric v%s (sha256 %s...) frozen before judging\n" % (version, rubric_hash[:8]))

    brief = validate_brief(backend("synthesize", SYNTHESIZE_PROMPT % article, BRIEF_SCHEMA))
    print("SYNTHESIZE  %s -> brief: %r (%d claims)" % (ARTICLE.name, brief["headline"], len(brief["claims"])))

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
            print("  [PASS] %-3s %-50s (%.2f)" % (v["claim_id"], claim["text"][:50], v["confidence"]))
        else:
            print("  [FAIL] %-3s %s" % (v["claim_id"], claim["text"]))
            print("         judge: %s" % v["reason"])
    total = len(judged["verdicts"])

    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "rubric_version": version,
        "rubric_sha256": rubric_hash,
        "article_sha256": sha256(article),
        "headline": brief["headline"],
        "supported": supported,
        "total": total,
        "verdicts": judged["verdicts"],
    }
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    history = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
    hist_supported = sum(e["supported"] for e in history)
    hist_total = sum(e["total"] for e in history)
    print("\nVERDICT     %d/%d claims supported" % (supported, total))
    print("LEDGER      run appended to %s (rubric hash + verdicts frozen); history: %d/%d across %d run(s)"
          % (LEDGER.name, hist_supported, hist_total, len(history)))
    return entry


def main():
    parser = argparse.ArgumentParser(description="a tiny agent pipeline that grades its own output")
    parser.add_argument("--live", action="store_true", help="call the Anthropic API instead of replaying fixtures")
    args = parser.parse_args()
    try:
        run("live" if args.live else "replay")
    except ContractError as e:
        print("\nCONTRACT VIOLATION (fail-loud by design):\n%s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
