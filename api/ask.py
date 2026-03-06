import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import anthropic

KB_DIR = Path(__file__).parent.parent / "kb_output"


def load_kb():
    kb = []
    for f in sorted(KB_DIR.glob("*.json")):
        with open(f) as fh:
            data = json.load(fh)
            data["_filename"] = f.name
            kb.append(data)
    return kb


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length))
        query = body.get("query", "")

        if not query:
            self._respond({"answer": "Please enter a question."})
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            self._respond({"answer": "ANTHROPIC_API_KEY not configured."}, 500)
            return

        kb = load_kb()
        kb_context = json.dumps(kb, indent=1)

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=(
                "You are Bid IQ, an expert assistant for Mohawk Lifts products. "
                "You have access to a knowledge base of extracted product data from "
                "installation drawings and spec sheets. Answer questions accurately "
                "based on the KB data provided. Use specific numbers, part numbers, "
                "and dimensions from the data. Format your response in clean HTML "
                "(use <strong>, <table>, <ul>, <h3> tags as needed). "
                "If data is not available in the KB, say so clearly."
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"Knowledge Base Data:\n{kb_context}\n\nQuestion: {query}",
                }
            ],
        )

        answer = response.content[0].text
        self._respond({"answer": answer})

    def _respond(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
