"""A job that was running when the service restarted is marked failed, not left «reading» forever."""
import json
import os
import tempfile
import unittest
from pathlib import Path


class Orphans(unittest.TestCase):
    def test_running_jobs_become_failed(self):
        d = Path(tempfile.mkdtemp())
        os.environ["PL_DATA_DIR"] = str(d)
        import importlib
        import app
        importlib.reload(app)
        for jid, st in (("a", "reading"), ("b", "done"), ("c", "matching")):
            (app.DATA / jid).mkdir(parents=True)
            (app.DATA / jid / "job.json").write_text(json.dumps({"status": st}))
        self.assertEqual(app._orphans(), 2)
        got = {j: json.loads((app.DATA / j / "job.json").read_text())["status"] for j in "abc"}
        self.assertEqual(got, {"a": "failed", "b": "done", "c": "failed"})


if __name__ == "__main__":
    unittest.main()
