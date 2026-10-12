# self-grading-demo

**A tiny agent pipeline that grades its own output — runs in under a second, no API keys.**

```bash
git clone https://github.com/viceatlas/self-grading-demo && cd self-grading-demo
python demo.py
```

Most LLM demos show a model that talks. This one shows the part that matters in production:
**an adversarial judge that checks every claim against the evidence, under a frozen rubric,
with the results appended to a hash-chained ledger** — so the pipeline keeps score on itself.

## What a run looks like

```
SELF-GRADING PIPELINE -- replay mode (no keys; --live uses the Anthropic API)
rubric v1 (sha256 732186cb...) frozen before judging

SYNTHESIZE  article.txt -> brief: 'Northwind Robotics posts a record quarter' (5 claims, every quote verbatim)
JUDGE       every claim vs. its quoted evidence, evidence-only, exact-number rule

  [PASS] c1  revenue $8.4M, up 21% YoY                          (0.97)
  [PASS] c2  shipped 1,240 Atlas units                          (0.95)
  [PASS] c3  99.2% fleet uptime across 312 sites                (0.93)
  [PASS] c4  headcount grew 61 -> 86                            (0.96)
  [FAIL] c5  Voltcraft deal worth $4.2M/yr
         judge: Evidence quote states "$2.4M per year"; the claim says $4.2M. Exact-number rule: a quantitative claim must show its exact figure in the evidence. Unsupported.

VERDICT     4/5 claims supported
LEDGER      row appended to ledger.jsonl (rubric hash, article hash, prev-row hash); chain intact across 1 row(s); history: 4/5
```

That failing claim is deliberate: the bundled fixture contains a transposed number, because a
judge you've only ever seen approve things is a judge you know nothing about.

## The mechanisms (each ~20 lines — read `demo.py` top to bottom)

| Mechanism | Where | The point |
|---|---|---|
| Strict JSON contract | `validate_brief` / `validate_verdicts` | Model output is parsed fail-loud: wrong shape = crash with the offending payload, never a silent shrug |
| Grounded quotes | `validate_brief(obj, article)` | Every `evidence_quote` must be a verbatim excerpt of the article. The judge only ever sees the quote, so an invented quote would be the one way to smuggle a wrong claim past it |
| Frozen rubric | `rubric.md` + sha256 | The judge's rules are versioned and hashed into every ledger row; a one-byte edit shows in the record |
| Adversarial judge | `JUDGE_PROMPT` | Evidence-only (no outside knowledge), exact-number rule, confidence caps |
| Hash-chained ledger | `ledger.jsonl`, `verify_ledger` | Each row carries its own sha256 and the previous row's; `python demo.py --verify` names the first row that was edited, reordered or removed |

## Live mode

```bash
pip install anthropic
set ANTHROPIC_API_KEY=sk-ant-...   # export on macOS/Linux
python demo.py --live
```

Live mode runs the same two stages against the Anthropic API (`claude-opus-5-5`, structured
outputs via `output_config` JSON schema; refusals and `max_tokens` truncation stop the run with a
named reason; token usage is printed per stage). The fail-loud validator still runs on top —
schema enforcement at the API layer and contract enforcement at the boundary are different
layers, and production systems want both. Replay mode exercises the identical code path from
bundled fixtures (the recorded model outputs), which is why the demo needs no keys: **the
pipeline's honesty machinery is deterministic even when the model isn't.**

## Why this exists

It's a minimal public extraction of the discipline I run in production: multi-stage LLM
pipelines on the Anthropic SDK where an LLM-as-judge scores every synthesized claim against
fetched evidence, a prompt-version gate exits nonzero when a new prompt regresses the prior
version's graded record, and every decision is frozen before its outcome lands. Those systems
are private; this repo is the two-minute version of the mechanism, not the record. Live
product: [viceatlas.com](https://viceatlas.com).

Everything here is fictional sample data (Northwind Robotics does not exist). MIT license.

## Tests

```bash
python -m unittest discover tests -v     # 18 tests, no network, no keys
```

The tests cover the contract layer (shape, enum, bounds, a boolean posing as a confidence, a
non-object verdict, a fabricated quote), the replay pipeline end to end, the rubric hash, and the
ledger chain (an edited row and a removed row are both detected).
