"""Volume controls exercised without sockets, against real persisted scan state."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


class VolumeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("relay", Path(__file__).with_name("telemetry-relay.py"))
        self.relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.relay)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.relay.SINK_DIR = str(self.root)
        self.relay.STATE_PATH = str(self.root / "seen.json")
        self.relay.WEBHOOK_URL = "http://test.invalid/private"
        self.sent = []
        self.relay.MAX_AGE = 300
        self.relay.GROUP_WINDOW = 900
        self.relay.MAX_PER_HOUR = 8

    def write(self, messages, received=None):
        records = [{"timeUnixNano": str(i + 1), "severityNumber": 17,
                    "body": {"stringValue": message}} for i, message in enumerate(messages)]
        envelope = {"received": time.time() if received is None else received,
                    "body": {"resourceLogs": [{"scopeLogs": [{"logRecords": records}]}]}}
        (self.root / "logs-test.jsonl").write_text(json.dumps(envelope) + "\n")

    def scan(self):
        def receive(request, timeout):
            self.sent.append((request.headers["X-delivery-id"], json.loads(request.data)))
            response = contextlib.nullcontext()
            response.status = 202
            return contextlib.nullcontext(response)
        with patch.object(self.relay.urllib.request, "urlopen", receive), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.relay.scan(True), 0)

    def test_old_exports_never_create_work(self):
        self.write(["historical incident"], time.time() - 86400)
        self.scan()
        self.assertEqual(self.sent, [])

    def test_repeated_failure_and_export_retry_make_one_task(self):
        self.write(["request 123 failed", "request 456 failed"])
        self.scan()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1]["detail"]["occurrences"], 2)
        self.write(["request 123 failed", "request 456 failed", "request 789 failed"])
        self.scan()
        self.assertEqual(len(self.sent), 1)

    def test_budget_is_shared_across_scans_and_expires(self):
        self.write(["failure " + chr(65 + i) for i in range(12)])
        self.scan()
        self.assertEqual(len(self.sent), 8)
        self.scan()
        self.assertEqual(len(self.sent), 8)
        saved = json.loads(Path(self.relay.STATE_PATH).read_text())
        Path(self.relay.STATE_PATH).write_text(json.dumps({k: time.time() - 3601 for k in saved}))
        self.scan()
        self.assertEqual(len(self.sent), 12)

    def test_tool_failures_are_filtered_and_runtime_failure_survives(self):
        spans = [{"spanId": "tool", "name": "telemetry claim failed", "status": {"code": 2},
                  "attributes": [{"key": "agent.tool.name", "value": {"stringValue": "claim"}}]},
                 {"spanId": "runtime", "name": "model timeout", "status": {"code": 2}}]
        envelope = {"received": time.time(), "body": {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}}
        (self.root / "traces-test.jsonl").write_text(json.dumps(envelope) + "\n")
        self.scan()
        self.assertEqual([body["summary"] for _, body in self.sent], ["model timeout"])


if __name__ == "__main__":
    unittest.main()
