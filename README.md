# Race Condition Detection and Visualization Tool

A web-based tool that detects race conditions in multithreaded Python programs
using **static analysis** (Python AST) and **simulated dynamic analysis**
(thread interleaving simulation). It identifies read-write conflicts, suggests
synchronization techniques, and visualizes thread interactions to help
developers debug and optimize concurrent code efficiently.

---

## Features

| Feature | Details |
|---|---|
| **Static analysis** | Parses Python source with `ast`, detects shared-variable accesses across thread functions |
| **Race detection** | Finds write-write, write-read, and read-write conflicts across threads |
| **Lock awareness** | Recognizes `with lock:`, `threading.Lock()`, `threading.RLock()` and marks protected accesses as safe |
| **Same-function threads** | Correctly detects races when the same function is used by multiple `Thread` instances |
| **Severity ratings** | Each race condition is rated `high`, `medium`, or `low` |
| **Synchronization suggestions** | 5 general-purpose and per-race-specific fix suggestions with code examples |
| **Thread timeline** | D3.js Gantt-chart visualization of thread operations with conflict arcs |
| **4 built-in examples** | Shared counter, bank transfer, producer-consumer (safe), lazy initialization |

## Screenshot

![Race Condition Detector UI](https://github.com/user-attachments/assets/ed7d2b24-5581-4546-a0ea-f9ff840df7e5)

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the development server
python app.py

# 3. Open http://localhost:5000 in your browser
```

## Project Structure

```
.
├── app.py                        # Flask web server & API routes
├── requirements.txt
├── analyzer/
│   ├── __init__.py
│   ├── static_analyzer.py        # AST-based race condition detection
│   └── dynamic_analyzer.py       # Thread interleaving simulation & timeline
├── templates/
│   └── index.html                # Single-page UI (CodeMirror + D3.js)
├── static/
│   ├── css/style.css
│   └── js/
│       ├── main.js               # UI controller
│       └── visualization.js      # D3.js thread timeline
└── tests/
    └── test_analyzer.py          # 30 pytest tests
```

## API

### `POST /api/analyze`

Accepts JSON `{"code": "<python source>"}` and returns:

```json
{
  "static": {
    "threads": ["increment", "decrement"],
    "shared_variables": ["counter"],
    "race_conditions": [
      {
        "variable": "counter",
        "thread1": "increment",
        "thread2": "decrement",
        "conflict_type": "write-write",
        "severity": "high",
        "description": "...",
        "suggestion": "..."
      }
    ],
    "suggestions": [...]
  },
  "timeline": {
    "threads": [{"id": "increment", "color": "#4CAF50"}],
    "events": [...],
    "conflicts": [...]
  }
}
```

### `GET /api/examples`

Returns the list of built-in example code snippets.

## Running Tests

```bash
python -m pytest tests/test_analyzer.py -v
```

All 30 tests cover:

- Static analyzer structure and output shape
- Shared variable detection
- Thread detection (single and multi-instance)
- Race condition detection (counter, bank, lazy-init, write-write)
- Lock protection (no false positives when code is safe)
- Dynamic timeline generation and conflict flagging
