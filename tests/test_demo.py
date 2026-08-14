import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import demo


def load(name):
    return json.loads((demo.REPLAY / name).read_text(encoding="utf-8"))


class TestContract(unittest.TestCase):
    def test_valid_brief_passes(self):
        brief = demo.validate_brief(load("synthesize.json"))
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

    def test_confidence_bounds(self):
        ids = [c["id"] for c in load("synthesize.json")["claims"]]
        bad = load("judge.json")
        bad["verdicts"][0]["confidence"] = 1.7
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
        entry = demo.run("replay")
        self.assertEqual(entry["supported"], 4)
        self.assertEqual(entry["total"], 5)
        self.assertEqual(entry["rubric_sha256"],
                         demo.sha256(demo.RUBRIC.read_text(encoding="utf-8")))

    def test_ledger_appends_across_runs(self):
        demo.run("replay")
        demo.run("replay")
        lines = demo.LEDGER.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            entry = json.loads(line)
            self.assertIn("rubric_sha256", entry)
            self.assertIn("verdicts", entry)


if __name__ == "__main__":
    unittest.main()
