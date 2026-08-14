# Judge rubric

version: 1

Frozen 2026-08-13. This file is hashed (sha256) into every ledger entry; changing
a single byte here is visible in the graded record. That is the point: a judge
whose rules can drift silently is not a judge.

## Rules

1. **Evidence-only.** Grade each claim against its `evidence_quote` and nothing
   else. Outside knowledge, however confident, is inadmissible.
2. **Exact-number rule.** A quantitative claim is supported only if the exact
   figure appears in the evidence quote. Close is unsupported. Rounded is
   unsupported. Transposed is unsupported.
3. **Paraphrase cap.** If the claim paraphrases rather than restates the
   evidence, confidence is capped at 0.80 even when supported.
4. **No benefit of the doubt.** Ambiguous evidence grades unsupported, with the
   ambiguity named in the reason.
5. **Reasons are receipts.** Every verdict carries a reason specific enough
   that a reader could re-derive it from the quote alone.
