import importlib.util
from datetime import date
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("append-log.py")
SPEC = importlib.util.spec_from_file_location("append_log", SCRIPT)
APPEND_LOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APPEND_LOG)


class AppendLogTests(unittest.TestCase):
    def test_appends_structured_entry_without_rewriting_existing_text(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "LOG.md"
            log.write_text("# Log\n", encoding="utf-8")

            APPEND_LOG.append_entry(log, "Validation passed", ["Result: PASS", "Next action: Continue."], date(2026, 10, 7))

            self.assertEqual(
                log.read_text(encoding="utf-8"),
                "# Log\n\n## 2026-10-07 — Validation passed\n\n"
                "- Result: PASS\n- Next action: Continue.\n",
            )

    def test_refuses_non_log_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.md"
            path.write_text("unchanged", encoding="utf-8")

            with self.assertRaises(ValueError):
                APPEND_LOG.append_entry(path, "Title", ["Item"], date.today())

            self.assertEqual(path.read_text(encoding="utf-8"), "unchanged")


if __name__ == "__main__":
    unittest.main()
