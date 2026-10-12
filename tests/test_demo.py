import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import demo


def load(name):
    return json.loads((demo.REPLAY / name).read_text(encoding="utf-8"))


def article():
    return demo.ARTICLE.read_text(encoding="utf-8")


def quiet_run(mode="replay"):
    with contextlib.redirect_stdout(io.StringIO()):
        return demo.run(mode)


class TestContract(unittest.TestCase):
    def test_valid_brief_passes(self):
        brief = demo.validate_brief(load("synthesize.json"), article())
        self.assertEqual(len(brief["claims"]), 5)

    def test_brief_missing_evidence_fails_loud(self):
        bad = load("synthesize.json")
        del bad["claims"][0]["evidence_quote"]
        with self.assertRaises(demo.ContractError):
            demo.validate_brief(bad)

    def test_brief_duplicate_ids_fail_loud(self):
        bad = load("synthesize.json")
        bad["claims"][1]["id"] = bad["claims"][0]["id"]
        with self.assertRaises(demo.ContractError):
            demo.validate_brief(bad)

    def test_fabricated_quote_fails_loud(self):
        # the judge only ever sees the quote, so an invented quote is the one way past it
        bad = load("synthesize.json")
        bad["claims"][0]["evidence_quote"] = "Revenue reached $9.9M, a figure that is not in the article."
        with self.assertRaises(demo.ContractError):
            demo.validate_brief(bad, article())

    def test_quote_check_ignores_whitespace_only(self):
        ok = load("synthesize.json")
        ok["claims"][0]["evidence_quote"] = "  " + ok["claims"][0]["evidence_quote"].replace(" ", "  ") + "\n"
        demo.validate_brief(ok, article())

    def test_valid_verdicts_pass(self):
        ids = [c["id"] for c in load("synthesize.json")["claims"]]
        judged = demo.validate_verdicts(load("judge.json"), ids)
        self.assertEqual(len(judged["verdicts"]), 5)

    def test_verdict_enum_enforced(self):
        ids = [c["id"] for c in load("synthesize.json")["claims"]]
        bad = load("judge.json")
        bad["verdicts"][0]["verdict"] = "maybe"
        with self.assertRaises(demo.ContractError):
            demo.validate_verdicts(bad, ids)

    def test_missing_claim_verdict_fails_loud(self):
        ids = [c["id"] for c in load("synthesize.json")["claims"]]
        bad = load("judge.json")
        bad["verdicts"].pop()
        with self.assertRaises(demo.ContractError):
            demo.validate_verdicts(bad, ids)

    def test_non_dict_verdict_fails_loud(self):
        ids = [c["id"] for c in load("synthesize.json")["claims"]]
        bad = load("judge.json")
        bad["verdicts"].append("junk")
        with self.assertRaises(demo.ContractError):
            demo.validate_verdicts(bad, ids)

    def test_confidence_bounds(self):
        ids = [c["id"] for c in load("synthesize.json")["claims"]]
        bad = load("judge.json")
        bad["verdicts"][0]["confidence"] = 1.7
        with self.assertRaises(demo.ContractError):
            demo.validate_verdicts(bad, ids)

    def test_boolean_confidence_fails_loud(self):
        # bool is an int subclass in Python; True must not read as confidence 1.0
        ids = [c["id"] for c in load("synthesize.json")["claims"]]
        bad = load("judge.json")
        bad["verdicts"][0]["confidence"] = True
        with self.assertRaises(demo.ContractError):
            demo.validate_verdicts(bad, ids)

    def test_parse_json_fails_loud_on_prose(self):
        with self.assertRaises(demo.ContractError):
            demo.parse_json("Sure! Here's the JSON you asked for: {oops")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self._ledger = demo.LEDGER
        demo.LEDGER = Path(__file__).resolve().parent / "_test_ledger.jsonl"
        if demo.LEDGER.exists():
            demo.LEDGER.unlink()

    def tearDown(self):
        if demo.LEDGER.exists():
            demo.LEDGER.unlink()
        demo.LEDGER = self._ledger

    def test_replay_run_end_to_end(self):
        entry = quiet_run()
        self.assertEqual(entry["supported"], 4)
        self.assertEqual(entry["total"], 5)
        self.assertEqual(entry["rubric_sha256"],
                         demo.sha256(demo.RUBRIC.read_text(encoding="utf-8")))

    def test_ledger_appends_across_runs(self):
        quiet_run()
        quiet_run()
        lines = demo.LEDGER.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            entry = json.loads(line)
            self.assertIn("rubric_sha256", entry)
            self.assertIn("verdicts", entry)

    def test_rubric_change_changes_recorded_hash(self):
        tmp = Path(__file__).resolve().parent / "_test_rubric.md"
        tmp.write_text(demo.RUBRIC.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        saved = demo.RUBRIC
        demo.RUBRIC = tmp
        try:
            entry = quiet_run()
        finally:
            demo.RUBRIC = saved
            tmp.unlink()
        self.assertNotEqual(entry["rubric_sha256"], demo.sha256(saved.read_text(encoding="utf-8")))

    def test_ledger_chain_intact_after_runs(self):
        quiet_run()
        quiet_run()
        ok, checked, bad, why = demo.verify_ledger(demo.LEDGER)
        self.assertTrue(ok, why)
        self.assertEqual(checked, 2)
        second = json.loads(demo.LEDGER.read_text(encoding="utf-8").splitlines()[1])
        self.assertIsNotNone(second["prev_sha256"])

    def test_ledger_chain_detects_an_edited_row(self):
        quiet_run()
        quiet_run()
        lines = demo.LEDGER.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0])
        row["verdicts"][-1]["verdict"] = "supported"  # quietly re-grade the failed claim
        lines[0] = json.dumps(row)
        demo.LEDGER.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, checked, bad, why = demo.verify_ledger(demo.LEDGER)
        self.assertFalse(ok)
        self.assertEqual(bad, 1)

    def test_ledger_chain_detects_a_removed_row(self):
        quiet_run()
        quiet_run()
        quiet_run()
        lines = demo.LEDGER.read_text(encoding="utf-8").splitlines()
        del lines[1]
        demo.LEDGER.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, checked, bad, why = demo.verify_ledger(demo.LEDGER)
        self.assertFalse(ok)
        self.assertEqual(bad, 2)


if __name__ == "__main__":
    unittest.main()
