from __future__ import annotations

import argparse
import json
import logging
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app import MAX_VIDEO_DURATION, parse_script, render_video

APP_DIR = Path(__file__).parent
STATIC_FILES = {
    "/": ("static/index.html", "text/html; charset=utf-8"),
    "/styles.css": ("static/styles.css", "text/css; charset=utf-8"),
    "/app.js": ("static/app.js", "text/javascript; charset=utf-8"),
    "/preview.bundle.js": ("static/preview.bundle.js", "text/javascript; charset=utf-8"),
    "/preview.bundle.css": ("static/preview.bundle.css", "text/css; charset=utf-8"),
}
MAX_REQUEST_SIZE = 1_000_000


class ChatVideoHandler(BaseHTTPRequestHandler):
    server_version = "ChatVideoCreator/1.0"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"ok": True})
            return
        resource = STATIC_FILES.get(path)
        if resource is None:
            self._send_json(404, {"error": "Not found."})
            return
        relative_path, content_type = resource
        file_path = APP_DIR / relative_path
        try:
            content = file_path.read_bytes()
        except OSError:
            logging.exception("Could not read static app file %s", file_path)
            self._send_json(500, {"error": "The app UI could not be loaded."})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/preview", "/api/render"}:
            self._send_json(404, {"error": "Not found."})
            return
        try:
            request = self._read_json()
            script = parse_script(request.get("source", ""), request.get("format", ""))
            if path == "/api/preview":
                self._send_json(200, self._preview_payload(script))
                return
            self._render_response(request, script)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except RuntimeError as exc:
            logging.exception("Could not complete %s", path)
            self._send_json(500, {"error": str(exc)})
        except Exception:
            logging.exception("Unexpected error handling %s", path)
            self._send_json(500, {"error": "An unexpected error occurred while processing the conversation."})

    def _read_json(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if content_length <= 0 or content_length > MAX_REQUEST_SIZE:
            raise ValueError(f"Request body must be between 1 byte and {MAX_REQUEST_SIZE:,} bytes.")
        try:
            data = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        source = data.get("source")
        if not isinstance(source, str):
            raise ValueError("'source' must be a string.")
        return data

    @staticmethod
    def _preview_payload(script: dict[str, Any]) -> dict[str, Any]:
        return {
            "contact": script["contact"],
            "contact_has_name": script["contact_has_name"],
            "avatar": script["avatar"],
            "avatar_display": script["avatar_display"],
            "start_time": script["start_time"],
            "duration": script["duration"],
            "history": [
                {
                    "sender": item.message.sender,
                    "text": item.message.text,
                    "timestamp": item.timestamp,
                    "outgoing": item.message.outgoing,
                }
                for item in script["history"]
            ],
            "messages": [
                {
                    "sender": entry.message.sender,
                    "text": entry.message.text,
                    "timestamp": entry.timestamp,
                    "typing_duration": entry.message.typing_duration,
                    "reply_delay": entry.message.reply_delay,
                    "typing_start": entry.typing_start,
                    "typing_end": entry.typing_end,
                    "reveal_at": entry.reveal_at,
                    "outgoing": entry.message.outgoing,
                }
                for entry in script["timeline"]
            ],
        }

    def _render_response(self, request: dict[str, Any], script: dict[str, Any]) -> None:
        aspect_ratio = request.get("aspect_ratio", "iphone")
        fps = request.get("fps", 24)
        if aspect_ratio not in {"preview", "iphone", "portrait", "square"}:
            raise ValueError("Choose preview box, iPhone, portrait, or square video format.")
        if isinstance(fps, bool) or not isinstance(fps, int) or not 12 <= fps <= 30:
            raise ValueError("Frame rate must be an integer between 12 and 30 FPS.")
        if script["duration"] > MAX_VIDEO_DURATION:
            raise ValueError(f"The conversation is too long. Keep the video under {MAX_VIDEO_DURATION:g} seconds.")

        with tempfile.TemporaryDirectory(prefix="chat-video-") as directory:
            output_path = Path(directory) / "imessage-conversation.mp4"
            render_video(script, output_path, aspect_ratio=aspect_ratio, fps=fps)
            size = output_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", 'attachment; filename="imessage-conversation.mp4"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with output_path.open("rb") as video:
                while chunk := video.read(64 * 1024):
                    self.wfile.write(chunk)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: Any) -> None:
        logging.info("%s - %s", self.address_string(), format_string % args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local chat-story video creator.")
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to use (default: 8000)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer((args.host, args.port), ChatVideoHandler)
    print(f"Chat Video Creator is ready at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Chat Video Creator.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
