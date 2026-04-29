"""Race Condition Detection and Visualization Tool — Flask application."""

import os
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from analyzer import DynamicAnalyzer, StaticAnalyzer

app = Flask(__name__)
CORS(app)

_static_analyzer = StaticAnalyzer()
_dynamic_analyzer = DynamicAnalyzer()

# ---------------------------------------------------------------------------
# Example code snippets served to the front-end
# ---------------------------------------------------------------------------

EXAMPLES = {
    "counter": {
        "title": "Shared Counter (classic race)",
        "description": (
            "Two threads increment a shared counter 10,000 times each. "
            "Without a lock the final value is unpredictable."
        ),
        "code": """\
import threading

counter = 0

def increment():
    global counter
    for _ in range(10000):
        counter += 1   # read then write — NOT atomic

def decrement():
    global counter
    for _ in range(10000):
        counter -= 1   # read then write — NOT atomic

t1 = threading.Thread(target=increment)
t2 = threading.Thread(target=decrement)
t1.start()
t2.start()
t1.join()
t2.join()
print("Final counter:", counter)  # expected 0, may differ
""",
    },
    "bank": {
        "title": "Bank Account Transfer",
        "description": (
            "Two threads simultaneously transfer money between accounts "
            "without locking — balances can go negative or money can be "
            "created out of thin air."
        ),
        "code": """\
import threading

balance_a = 1000
balance_b = 500

def transfer_a_to_b(amount):
    global balance_a, balance_b
    if balance_a >= amount:
        balance_a -= amount   # write
        balance_b += amount   # write

def transfer_b_to_a(amount):
    global balance_a, balance_b
    if balance_b >= amount:
        balance_b -= amount   # write
        balance_a += amount   # write

t1 = threading.Thread(target=transfer_a_to_b, args=(200,))
t2 = threading.Thread(target=transfer_b_to_a, args=(200,))
t1.start()
t2.start()
t1.join()
t2.join()
""",
    },
    "producer_consumer": {
        "title": "Producer–Consumer (safe with Lock)",
        "description": (
            "A producer and a consumer share a list protected by a lock. "
            "This is an example of *correct* synchronisation — no race "
            "conditions should be reported."
        ),
        "code": """\
import threading

shared_list = []
lock = threading.Lock()

def producer():
    for i in range(5):
        with lock:
            shared_list.append(i)

def consumer():
    for _ in range(5):
        with lock:
            if shared_list:
                item = shared_list.pop(0)

t1 = threading.Thread(target=producer)
t2 = threading.Thread(target=consumer)
t1.start()
t2.start()
t1.join()
t2.join()
""",
    },
    "lazy_init": {
        "title": "Lazy Initialization (check-then-act)",
        "description": (
            "Two threads both check whether a shared resource has been "
            "initialized and may both proceed to initialize it. "
            "A classic 'check-then-act' race."
        ),
        "code": """\
import threading

resource = None

def initialize():
    global resource
    if resource is None:          # check
        resource = "initialized"  # act — another thread may also do this

t1 = threading.Thread(target=initialize)
t2 = threading.Thread(target=initialize)
t1.start()
t2.start()
t1.join()
t2.join()
""",
    },
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")

    if not code.strip():
        return jsonify({"error": "No code provided."}), 400

    # Static analysis
    static_result = _static_analyzer.analyze(code)

    # Dynamic timeline
    timeline = _dynamic_analyzer.build_timeline(static_result)

    return jsonify({
        "static": static_result,
        "timeline": timeline,
    })


@app.route("/api/examples", methods=["GET"])
def examples():
    return jsonify(
        [
            {"key": k, "title": v["title"], "description": v["description"],
             "code": v["code"]}
            for k, v in EXAMPLES.items()
        ]
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, host="0.0.0.0", port=port)
