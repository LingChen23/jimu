from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud_upload import CloudUploader
from cloud_upload.transport import HttpTransport, TransportError


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


class MemoryTransport:
    def __init__(self):
        self.archived = set()
        self.calls = []
        self.failure = None
        self.entered = None
        self.release = None

    def state(self):
        return "connected"

    def close(self):
        if self.release:
            self.release.set()

    def archive(self, task_id, zip_path):
        self.calls.append((task_id, zip_path.read_bytes()))
        if self.entered:
            self.entered.set()
        if self.release:
            self.release.wait(2)
        if task_id in self.archived:
            return
        if self.failure:
            raise self.failure
        self.archived.add(task_id)


class CloudUploaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.tasks = [{"task_id": "T-OLD", "status": "completed", "end_time": old}]
        self.exports = []

    def tearDown(self):
        self.temporary.cleanup()

    def uploader(self, transport=None, diagnostic_sink=None):
        def export(task_id):
            self.exports.append(task_id)
            return ("zip:" + task_id).encode()
        return CloudUploader(
            list_tasks=lambda: list(self.tasks), export_zip=export,
            runtime_root=self.root, transport=transport,
            diagnostic_sink=diagnostic_sink,
        )

    def test_reuses_exporter_and_marks_uploaded(self):
        transport = MemoryTransport()
        uploader = self.uploader(transport)
        uploader.start()
        self.assertTrue(uploader.enqueue(["T-OLD"])["T-OLD"]["ok"])
        wait_for(lambda: uploader.statuses(["T-OLD"])["tasks"]["T-OLD"]["status"] == "uploaded")
        uploader.close()
        self.assertEqual(self.exports, ["T-OLD"])
        self.assertEqual(transport.calls, [("T-OLD", b"zip:T-OLD")])
        self.assertEqual(uploader.enqueue(["T-OLD"])["T-OLD"]["status"], "uploaded")
        self.assertEqual(list((self.root / "cloud_upload_cache").glob("*.zip")), [])

    def test_old_task_waits_but_new_finished_task_is_recovered(self):
        transport = MemoryTransport()
        first = self.uploader(None)
        first.start()
        first.close()
        self.assertEqual(self.exports, [])
        self.tasks.append({
            "task_id": "T-NEW", "status": "completed",
            "end_time": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        })
        restarted = self.uploader(transport)
        restarted.start()
        wait_for(lambda: "T-NEW" in transport.archived)
        restarted.close()
        self.assertNotIn("T-OLD", transport.archived)

    def test_failure_is_visible_and_manual_retry_rebuilds_zip(self):
        transport = MemoryTransport()
        transport.failure = TransportError("unauthorized", False)
        events = []
        uploader = self.uploader(transport, lambda *args, **kwargs: events.append((args, kwargs)))
        uploader.start()
        uploader.enqueue(["T-OLD"])
        wait_for(lambda: uploader.statuses(["T-OLD"])["tasks"]["T-OLD"]["status"] == "failed")
        self.assertEqual(
            uploader.statuses(["T-OLD"])["tasks"]["T-OLD"]["error_code"], "unauthorized"
        )
        self.assertEqual(events[0][0][:2], ("cloud_auth_failed", "error"))
        self.assertEqual(events[0][1]["reason_code"], "unauthorized")
        transport.failure = None
        uploader.enqueue(["T-OLD"])
        wait_for(lambda: uploader.statuses(["T-OLD"])["tasks"]["T-OLD"]["status"] == "uploaded")
        uploader.close()
        self.assertEqual(self.exports, ["T-OLD", "T-OLD"])

    def test_queue_survives_restart_and_response_loss(self):
        queued = self.uploader(None)
        queued.start()
        queued.enqueue(["T-OLD"])
        queued.close()
        transport = MemoryTransport()

        class LostResponseTransport(MemoryTransport):
            def archive(inner_self, task_id, zip_path):
                if task_id not in inner_self.archived:
                    inner_self.archived.add(task_id)
                    raise TransportError("cloud_connection_failed", True)
                inner_self.calls.append((task_id, zip_path.read_bytes()))

        transport = LostResponseTransport()
        restarted = self.uploader(transport)
        restarted.start()
        wait_for(lambda: restarted.statuses(["T-OLD"])["tasks"]["T-OLD"]["status"] == "failed")
        restarted.enqueue(["T-OLD"])
        wait_for(lambda: restarted.statuses(["T-OLD"])["tasks"]["T-OLD"]["status"] == "uploaded")
        restarted.close()
        self.assertEqual(self.exports, ["T-OLD", "T-OLD"])

    def test_uploading_task_cannot_be_deleted(self):
        transport = MemoryTransport()
        transport.entered = threading.Event()
        transport.release = threading.Event()
        uploader = self.uploader(transport)
        uploader.start()
        uploader.enqueue(["T-OLD"])
        self.assertTrue(transport.entered.wait(2))
        with self.assertRaisesRegex(ValueError, "正在上传"):
            uploader.prepare_delete("T-OLD")
        transport.release.set()
        wait_for(lambda: uploader.statuses(["T-OLD"])["tasks"]["T-OLD"]["status"] == "uploaded")
        uploader.close()

    def test_invalid_or_collecting_task_is_not_queued(self):
        self.tasks.append({"task_id": "T-LIVE", "status": "collecting", "end_time": None})
        uploader = self.uploader(None)
        result = uploader.enqueue(["missing", "T-LIVE"])
        self.assertEqual(result["missing"]["error_code"], "task_not_found")
        self.assertEqual(result["T-LIVE"]["error_code"], "task_not_finished")


class HttpTransportTests(unittest.TestCase):
    def test_queries_then_streams_existing_zip(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            archived = False
            def log_message(self, *_args):
                return
            def send_json(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                requests.append(("GET", self.headers.get("Authorization"), b""))
                if self.archived:
                    self.send_json(200, {"task_id": "T-TEST", "status": "archived"})
                else:
                    self.send_json(404, {"error": {"code": "task_not_found", "retryable": False}})
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append(("POST", self.headers.get("Authorization"), body))
                Handler.archived = True
                self.send_json(201, {"task_id": "T-TEST", "status": "archived"})

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "task.zip"
                archive.write_bytes(b"zip bytes")
                transport = HttpTransport({
                    "base_url": f"http://127.0.0.1:{server.server_address[1]}",
                    "token": "x" * 32,
                })
                transport.archive("T-TEST", archive)
                transport.archive("T-TEST", archive)
            self.assertEqual([item[0] for item in requests], ["GET", "POST", "GET"])
            self.assertTrue(all(item[1] == "Bearer " + "x" * 32 for item in requests))
            self.assertIn(b"zip bytes", requests[1][2])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
