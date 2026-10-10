"""Exercise read-only HTTP, CSV escaping and paths without a real Mihomo instance."""
import csv
import http.client
import http.server
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest

import clash_audit as a


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "audit # space &"
        (self.base / "data").mkdir(parents=True)
        for name in ("index.html", "app.js", "style.css"):
            (self.base / name).write_text("test asset")
        (self.base / ".env").write_text("EXAMPLE=not-public")
        db = a.Store(self.base / "data/audit.sqlite3")
        with db.db:
            db.db.execute("INSERT INTO daily VALUES (?,?,?,?,?,?)", (a.date_at(time.time()), "domain", "proxy", "=SUM(1,1)", 10, 20))
        db.close()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), a.handler_for(self.base))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, method="GET", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        result = r.status, dict(r.getheaders()), r.read()
        c.close()
        return result

    def test_report_uri_with_spaces_and_fragment_characters(self):
        import json
        status, headers, body = self.request("/api/report?kind=domain&route=proxy")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["matched_totals"], {"tx": 10, "rx": 20})
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_csv_formula_is_escaped(self):
        status, headers, body = self.request("/export.csv")
        self.assertEqual(status, 200)
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertEqual(rows[1][4], "'=SUM(1,1)")

    def test_private_files_foreign_host_and_mutation_are_rejected(self):
        self.assertEqual(self.request("/.env")[0], 404)
        self.assertEqual(self.request("/", headers={"Host": "example.com"})[0], 403)
        self.assertEqual(self.request("/api/report", method="POST")[0], 501)
        self.assertEqual(self.request("/api/report?route=invalid")[0], 400)


if __name__ == "__main__":
    unittest.main()
