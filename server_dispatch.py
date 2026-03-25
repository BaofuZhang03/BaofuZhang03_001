import datetime
import json
import os
import pathlib
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT_DIR = pathlib.Path(__file__).resolve().parent
RUNS_DIR = ROOT_DIR / "server_runs"
API_KEY = os.getenv("SERVER_DISPATCH_API_KEY", "").strip()
HOST = os.getenv("SERVER_DISPATCH_HOST", "0.0.0.0")
PORT = int(os.getenv("SERVER_DISPATCH_PORT", "8788"))


def _beijing_now() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def _json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "SeatDispatchHTTP/1.0"

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _reply(self, status: int, data: dict):
        body = _json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not API_KEY:
            return True
        return self.headers.get("X-API-Key", "").strip() == API_KEY

    def do_GET(self):
        if self.path == "/health":
            self._reply(
                200,
                {
                    "ok": True,
                    "now": _beijing_now().isoformat(),
                    "service": "server-dispatch",
                    "runs_dir": str(RUNS_DIR),
                },
            )
            return
        self._reply(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if self.path != "/dispatch":
            self._reply(404, {"ok": False, "error": "Not found"})
            return
        if not self._authorized():
            self._reply(401, {"ok": False, "error": "Unauthorized"})
            return

        try:
            payload = self._read_json()
        except json.JSONDecodeError as e:
            self._reply(400, {"ok": False, "error": f"Invalid JSON: {e}"})
            return

        users = payload.get("users")
        if not isinstance(users, list) or not users:
            self._reply(400, {"ok": False, "error": "Payload must include non-empty users array"})
            return

        run_id = payload.get("run_id") or _beijing_now().strftime("%Y%m%d_%H%M%S_%f")
        payload["run_id"] = run_id
        run_dir = RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        payload_path = run_dir / "payload.json"
        log_path = run_dir / "server_dispatch.log"
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        cmd = [sys.executable, "run_batch.py", "--payload-file", str(payload_path)]
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(
                f"[dispatch] accepted_at={_beijing_now().isoformat()} "
                f"school_id={payload.get('school_id', '')} users={len(users)}\n"
            )
            log_file.flush()
            subprocess.Popen(
                cmd,
                cwd=ROOT_DIR,
                env=os.environ.copy(),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )

        self._reply(
            202,
            {
                "ok": True,
                "accepted": True,
                "run_id": run_id,
                "user_count": len(users),
                "payload_path": str(payload_path),
                "log_path": str(log_path),
            },
        )


def main():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"server_dispatch listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
