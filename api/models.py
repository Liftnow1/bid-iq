import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path

KB_DIR = Path(__file__).parent.parent / "kb_output"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        models = set()
        for f in KB_DIR.glob("*.json"):
            with open(f) as fh:
                data = json.load(fh)
                model = data.get("model", "")
                if model:
                    models.add(model)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(sorted(models)).encode())
