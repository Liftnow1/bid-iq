import json
import os
from pathlib import Path

import anthropic
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

KB_DIR = Path(__file__).parent.parent / "kb_output"

HTML = """\
<!DOCTYPE html>
<html>
<head>
<title>Bid IQ - Mohawk Knowledge Base</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1117; color: #e0e0e0; }
  .container { max-width: 900px; margin: 0 auto; padding: 40px 20px; }
  h1 { font-size: 28px; margin-bottom: 8px; color: #fff; }
  .subtitle { color: #888; margin-bottom: 30px; font-size: 14px; }
  .search-box { display: flex; gap: 10px; margin-bottom: 30px; }
  input[type=text] {
    flex: 1; padding: 14px 18px; font-size: 16px; border: 1px solid #333;
    border-radius: 8px; background: #1a1d27; color: #fff; outline: none;
  }
  input[type=text]:focus { border-color: #4a6cf7; }
  button {
    padding: 14px 28px; font-size: 16px; border: none; border-radius: 8px;
    background: #4a6cf7; color: #fff; cursor: pointer; font-weight: 600;
  }
  button:hover { background: #3a5ce5; }
  button:disabled { background: #333; cursor: not-allowed; }
  .answer {
    background: #1a1d27; border: 1px solid #2a2d37; border-radius: 8px;
    padding: 24px; line-height: 1.7; white-space: pre-wrap; font-size: 15px;
  }
  .answer h2, .answer h3 { color: #fff; margin: 16px 0 8px; }
  .answer strong { color: #fff; }
  .answer table { border-collapse: collapse; margin: 12px 0; width: 100%; }
  .answer th, .answer td { border: 1px solid #333; padding: 8px 12px; text-align: left; }
  .answer th { background: #222; color: #fff; }
  .loading { color: #888; font-style: italic; }
  .models { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
  .model-tag {
    background: #1a1d27; border: 1px solid #2a2d37; border-radius: 6px;
    padding: 6px 12px; font-size: 13px; cursor: pointer; color: #aaa;
  }
  .model-tag:hover { border-color: #4a6cf7; color: #fff; }
  .examples { color: #666; font-size: 13px; margin-bottom: 20px; }
</style>
</head>
<body>
<div class="container">
  <h1>Bid IQ</h1>
  <p class="subtitle">Mohawk Lifts Knowledge Base - {{ model_count }} products indexed</p>

  <div class="models" id="models"></div>

  <p class="examples">
    Try: "What are the specs for the 75-30-F?" or "Compare flush vs surface mount"
    or "What slab thickness is needed for a 100-42?"
  </p>

  <div class="search-box">
    <input type="text" id="query" placeholder="Ask anything about Mohawk lifts..."
           onkeydown="if(event.key==='Enter') ask()">
    <button onclick="ask()" id="btn">Ask</button>
  </div>

  <div id="result"></div>
</div>
<script>
  // Load model list
  fetch('/api/models').then(r=>r.json()).then(models => {
    const el = document.getElementById('models');
    models.forEach(m => {
      const tag = document.createElement('span');
      tag.className = 'model-tag';
      tag.textContent = m;
      tag.onclick = () => {
        document.getElementById('query').value = 'What are the full specs for the ' + m + '?';
        ask();
      };
      el.appendChild(tag);
    });
  });

  async function ask() {
    const q = document.getElementById('query').value.trim();
    if (!q) return;
    const btn = document.getElementById('btn');
    const res = document.getElementById('result');
    btn.disabled = true;
    btn.textContent = 'Thinking...';
    res.innerHTML = '<p class="loading">Searching knowledge base...</p>';
    try {
      const r = await fetch('/api/ask', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query: q})
      });
      const data = await r.json();
      res.innerHTML = '<div class="answer">' + data.answer + '</div>';
    } catch(e) {
      res.innerHTML = '<div class="answer" style="color:#f55">Error: ' + e.message + '</div>';
    }
    btn.disabled = false;
    btn.textContent = 'Ask';
  }
</script>
</body>
</html>
"""


def load_kb():
    """Load all knowledge base JSON files."""
    kb = []
    for f in sorted(KB_DIR.glob("*.json")):
        with open(f) as fh:
            data = json.load(fh)
            data["_filename"] = f.name
            kb.append(data)
    return kb


def get_model_list(kb):
    """Extract unique model identifiers from KB."""
    models = set()
    for item in kb:
        model = item.get("model", "")
        if model:
            models.add(model)
    return sorted(models)


@app.route("/")
def index():
    kb = load_kb()
    return render_template_string(HTML, model_count=len(kb))


@app.route("/api/models")
def models():
    kb = load_kb()
    return jsonify(get_model_list(kb))


@app.route("/api/ask", methods=["POST"])
def ask():
    query = request.json.get("query", "")
    if not query:
        return jsonify({"answer": "Please enter a question."})

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"answer": "ANTHROPIC_API_KEY not set on server."}), 500

    kb = load_kb()

    # Build context from KB - include all data (it's not that large)
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
    return jsonify({"answer": answer})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
