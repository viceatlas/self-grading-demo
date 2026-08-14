# self-grading-demo

**A tiny agent pipeline that grades its own output — runnable in under 2 minutes, no API keys.**

```bash
git clone https://github.com/viceatlas/self-grading-demo && cd self-grading-demo
python demo.py
```

Most LLM demos show a model that talks. This one shows the part that matters in production:
**an adversarial judge that checks every claim against the evidence, under a frozen rubric,
with the results appended to a tamper-evident ledger** — so the pipeline keeps score on itself.

## What a run looks like

```
SELF-GRADING PIPELINE -- replay mode (no keys; --live uses the Anthropic API)
rubric v1 (sha256 732186cb...) frozen before judging

SYNTHESIZE  fixtures/article.txt -> brief: "Northwind Robotics posts a record quarter" (5 claims)
JUDGE       every claim vs. its quoted evidence, evidence-only, exact-number rule

  [PASS] c1  revenue $8.4M, up 21% YoY                          (0.97)
  [PASS] c2  shipped 1,240 Atlas units                          (0.95)
  [PASS] c3  99.2% fleet uptime across 312 sites                (0.93)
  [PASS] c4  headcount grew 61 -> 86                            (0.96)
  [FAIL] c5  Voltcraft deal worth $4.2M/yr
         judge: evidence quote states "$2.4M per year"; the claim says $4.2M.
         Exact-number rule: a quantitative claim must show its exact figure
         in the evidence. Unsupported.

VERDICT     4/5 claims supported -- the writer got one number wrong and the judge caught it.
LEDGER      run appended to ledger.jsonl (rubric hash + verdicts frozen); history: 4/5 across 1 run(s)
```

That failing claim is deliberate: the bundled fixture contains a transposed number, because a
judge you've only ever seen approve things is a judge you know nothing about.

## The mechanisms (each ~20 lines — read `demo.py` top to bottom)

| Mechanism | Where | The point |
|---|---|---|
| Strict JSON contract | `validate_brief` / `validate_verdicts` | Model output is parsed fail-loud: wrong shape = crash with the offending payload, never a silent shrug |
| Frozen rubric | `rubric.md` + sha256 | The judge's rules are versioned and hashed into every ledger row — a graded record you can't quietly re-grade |
| Adversarial judge | `JUDGE_PROMPT` | Evidence-only (no outside knowledge), exact-number rule, confidence caps |
| Append-only ledger | `ledger.jsonl` | Every run's verdicts frozen with timestamps and rubric hash; the report shows the cross-run record |

## Live mode

```bash
pip install anthropic
set ANTHROPIC_API_KEY=sk-ant-...   # export on macOS/Linux
python demo.py --live
```

Live mode runs the same two stages against the Anthropic API (`claude-opus-5`, structured
outputs via `output_config` JSON schema, refusal-aware). The fail-loud validator still runs on
top — schema enforcement at the API layer and contract enforcement at the boundary are
different layers, and production systems want both. Replay mode exercises the identical code
path from bundled fixtures, which is why the demo needs no keys: **the pipeline's honesty
machinery is deterministic even when the model isn't.**

## Why this exists

It's a minimal public extraction of the discipline I run in production: multi-stage agent
pipelines on the Anthropic API where an LLM-as-judge scores every synthesized claim against
fetched evidence, prompt versions must match-or-beat their predecessor's graded record or the
run blocks, and every decision is frozen before its outcome lands. Live receipts:
[viceatlas.com](https://viceatlas.com).

Everything here is fictional sample data (Northwind Robotics does not exist). MIT license.

## Tests

```bash
python -m unittest discover tests -v
```
