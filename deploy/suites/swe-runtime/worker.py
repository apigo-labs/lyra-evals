"""Agent shell in the frozen official environment; hidden grading runs separately."""

import contextlib
import json
import os
import signal
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/exec" or not 0 < size <= 100000:
                raise ValueError("Invalid request")
            body = json.loads(self.rfile.read(size))
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(
                    [
                        "/bin/bash",
                        "-c",
                        "source /opt/miniconda3/etc/profile.d/conda.sh; "
                        "conda activate testbed; " + body["command"],
                    ],
                    cwd="/testbed",
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                timed_out = False
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    timed_out = True
                finally:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                output.seek(0)
                text = output.read(200001)
            payload = {
                "exit_code": process.returncode,
                "timed_out": timed_out,
                "output": text[:200000].decode(errors="replace"),
                "truncated": len(text) > 200000,
            }
            self.send_response(200)
        except Exception:
            payload = {"error": "Environment command failed"}
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


HTTPServer(("0.0.0.0", 8091), Handler).serve_forever()
