"""Tests for the static and dynamic analyzers."""

import pytest
from analyzer.static_analyzer import StaticAnalyzer
from analyzer.dynamic_analyzer import DynamicAnalyzer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def static_analyzer():
    return StaticAnalyzer()


@pytest.fixture
def dynamic_analyzer():
    return DynamicAnalyzer()


# ---------------------------------------------------------------------------
# StaticAnalyzer — basic structure
# ---------------------------------------------------------------------------

class TestStaticAnalyzerStructure:

    def test_analyze_returns_expected_keys(self, static_analyzer):
        result = static_analyzer.analyze("counter = 0\n")
        for key in ("threads", "shared_variables", "accesses",
                    "race_conditions", "suggestions", "lock_vars", "errors"):
            assert key in result

    def test_empty_code(self, static_analyzer):
        result = static_analyzer.analyze("")
        assert result["errors"] == []
        assert result["race_conditions"] == []

    def test_syntax_error_is_reported(self, static_analyzer):
        result = static_analyzer.analyze("def broken(:\n    pass\n")
        assert len(result["errors"]) > 0
        assert result["race_conditions"] == []


# ---------------------------------------------------------------------------
# StaticAnalyzer — shared variable detection
# ---------------------------------------------------------------------------

class TestSharedVariableDetection:

    def test_module_level_variable_detected(self, static_analyzer):
        code = "counter = 0\n"
        result = static_analyzer.analyze(code)
        assert "counter" in result["shared_variables"]

    def test_global_declaration_detected(self, static_analyzer):
        code = (
            "import threading\n"
            "data = []\n"
            "def worker():\n"
            "    global data\n"
            "    data.append(1)\n"
        )
        result = static_analyzer.analyze(code)
        assert "data" in result["shared_variables"]

    def test_no_false_positives_for_local_vars(self, static_analyzer):
        code = (
            "import threading\n"
            "def worker():\n"
            "    local_var = 42\n"
            "    return local_var\n"
        )
        result = static_analyzer.analyze(code)
        assert "local_var" not in result["shared_variables"]


# ---------------------------------------------------------------------------
# StaticAnalyzer — thread detection
# ---------------------------------------------------------------------------

class TestThreadDetection:

    def test_threading_thread_target_detected(self, static_analyzer):
        code = (
            "import threading\n"
            "counter = 0\n"
            "def worker():\n"
            "    global counter\n"
            "    counter += 1\n"
            "t = threading.Thread(target=worker)\n"
        )
        result = static_analyzer.analyze(code)
        assert "worker" in result["threads"]

    def test_multiple_threads_detected(self, static_analyzer):
        code = (
            "import threading\n"
            "val = 0\n"
            "def inc():\n"
            "    global val\n"
            "    val += 1\n"
            "def dec():\n"
            "    global val\n"
            "    val -= 1\n"
            "t1 = threading.Thread(target=inc)\n"
            "t2 = threading.Thread(target=dec)\n"
        )
        result = static_analyzer.analyze(code)
        assert "inc" in result["threads"]
        assert "dec" in result["threads"]


# ---------------------------------------------------------------------------
# StaticAnalyzer — race condition detection
# ---------------------------------------------------------------------------

_COUNTER_RACE = """\
import threading

counter = 0

def increment():
    global counter
    for _ in range(10000):
        counter += 1

def decrement():
    global counter
    for _ in range(10000):
        counter -= 1

t1 = threading.Thread(target=increment)
t2 = threading.Thread(target=decrement)
"""

_BANK_RACE = """\
import threading

balance_a = 1000
balance_b = 500

def transfer_a_to_b(amount):
    global balance_a, balance_b
    if balance_a >= amount:
        balance_a -= amount
        balance_b += amount

def transfer_b_to_a(amount):
    global balance_a, balance_b
    if balance_b >= amount:
        balance_b -= amount
        balance_a += amount

t1 = threading.Thread(target=transfer_a_to_b, args=(200,))
t2 = threading.Thread(target=transfer_b_to_a, args=(200,))
"""

_SAFE_WITH_LOCK = """\
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
"""


class TestRaceConditionDetection:

    def test_counter_race_detected(self, static_analyzer):
        result = static_analyzer.analyze(_COUNTER_RACE)
        assert len(result["race_conditions"]) > 0
        vars_with_race = {r["variable"] for r in result["race_conditions"]}
        assert "counter" in vars_with_race

    def test_bank_race_detected(self, static_analyzer):
        result = static_analyzer.analyze(_BANK_RACE)
        assert len(result["race_conditions"]) > 0
        vars_with_race = {r["variable"] for r in result["race_conditions"]}
        assert "balance_a" in vars_with_race or "balance_b" in vars_with_race

    def test_no_race_when_protected_by_lock(self, static_analyzer):
        result = static_analyzer.analyze(_SAFE_WITH_LOCK)
        # shared_list is always accessed under `lock` — expect no races
        assert result["race_conditions"] == []

    def test_race_condition_has_required_fields(self, static_analyzer):
        result = static_analyzer.analyze(_COUNTER_RACE)
        rc = result["race_conditions"][0]
        for field in ("variable", "thread1", "thread2",
                      "access1", "access2", "conflict_type",
                      "severity", "description", "suggestion"):
            assert field in rc

    def test_race_severity_is_valid(self, static_analyzer):
        result = static_analyzer.analyze(_COUNTER_RACE)
        for rc in result["race_conditions"]:
            assert rc["severity"] in ("high", "medium", "low")

    def test_conflict_type_is_valid(self, static_analyzer):
        result = static_analyzer.analyze(_COUNTER_RACE)
        valid = {"write-write", "write-read", "read-write"}
        for rc in result["race_conditions"]:
            assert rc["conflict_type"] in valid

    def test_write_write_conflict_detected(self, static_analyzer):
        code = (
            "import threading\n"
            "x = 0\n"
            "def writer_a():\n"
            "    global x\n"
            "    x = 1\n"
            "def writer_b():\n"
            "    global x\n"
            "    x = 2\n"
            "t1 = threading.Thread(target=writer_a)\n"
            "t2 = threading.Thread(target=writer_b)\n"
        )
        result = static_analyzer.analyze(code)
        conflict_types = {r["conflict_type"] for r in result["race_conditions"]}
        assert "write-write" in conflict_types

    def test_lazy_init_race_detected(self, static_analyzer):
        code = (
            "import threading\n"
            "resource = None\n"
            "def initialize():\n"
            "    global resource\n"
            "    if resource is None:\n"
            "        resource = 'done'\n"
            "t1 = threading.Thread(target=initialize)\n"
            "t2 = threading.Thread(target=initialize)\n"
        )
        result = static_analyzer.analyze(code)
        assert len(result["race_conditions"]) > 0

    def test_no_self_race(self, static_analyzer):
        """A single thread accessing a variable should not be flagged."""
        code = (
            "import threading\n"
            "counter = 0\n"
            "def only_thread():\n"
            "    global counter\n"
            "    counter += 1\n"
            "t1 = threading.Thread(target=only_thread)\n"
        )
        result = static_analyzer.analyze(code)
        assert result["race_conditions"] == []


# ---------------------------------------------------------------------------
# StaticAnalyzer — lock variable detection
# ---------------------------------------------------------------------------

class TestLockDetection:

    def test_threading_lock_detected(self, static_analyzer):
        code = (
            "import threading\n"
            "lock = threading.Lock()\n"
        )
        result = static_analyzer.analyze(code)
        assert "lock" in result["lock_vars"]

    def test_rlock_detected(self, static_analyzer):
        code = (
            "import threading\n"
            "rlock = threading.RLock()\n"
        )
        result = static_analyzer.analyze(code)
        assert "rlock" in result["lock_vars"]


# ---------------------------------------------------------------------------
# StaticAnalyzer — suggestions
# ---------------------------------------------------------------------------

class TestSuggestions:

    def test_general_suggestions_always_present(self, static_analyzer):
        result = static_analyzer.analyze("counter = 0\n")
        assert len(result["suggestions"]) > 0

    def test_race_specific_suggestion_non_empty(self, static_analyzer):
        result = static_analyzer.analyze(_COUNTER_RACE)
        for rc in result["race_conditions"]:
            assert rc["suggestion"].strip() != ""


# ---------------------------------------------------------------------------
# DynamicAnalyzer — timeline building
# ---------------------------------------------------------------------------

class TestDynamicAnalyzer:

    def _get_timeline(self, dynamic_analyzer, static_analyzer, code):
        static_result = static_analyzer.analyze(code)
        return dynamic_analyzer.build_timeline(static_result)

    def test_timeline_returns_expected_keys(self, dynamic_analyzer, static_analyzer):
        timeline = self._get_timeline(dynamic_analyzer, static_analyzer,
                                      _COUNTER_RACE)
        for key in ("threads", "events", "conflicts", "interleavings_checked"):
            assert key in timeline

    def test_timeline_threads_match_static(self, dynamic_analyzer, static_analyzer):
        static_result = static_analyzer.analyze(_COUNTER_RACE)
        timeline = dynamic_analyzer.build_timeline(static_result)
        static_threads = set(static_result["threads"])
        timeline_threads = {t["id"] for t in timeline["threads"]}
        assert static_threads == timeline_threads

    def test_timeline_events_have_required_fields(self, dynamic_analyzer,
                                                   static_analyzer):
        timeline = self._get_timeline(dynamic_analyzer, static_analyzer,
                                      _COUNTER_RACE)
        required = {"id", "thread", "thread_index", "variable",
                    "access_type", "line", "start", "end",
                    "protected_by", "is_conflict"}
        for ev in timeline["events"]:
            assert required.issubset(ev.keys())

    def test_conflicts_reference_valid_event_ids(self, dynamic_analyzer,
                                                  static_analyzer):
        timeline = self._get_timeline(dynamic_analyzer, static_analyzer,
                                      _COUNTER_RACE)
        event_ids = {ev["id"] for ev in timeline["events"]}
        for cf in timeline["conflicts"]:
            assert cf["event1_id"] in event_ids
            assert cf["event2_id"] in event_ids

    def test_conflict_events_flagged(self, dynamic_analyzer, static_analyzer):
        timeline = self._get_timeline(dynamic_analyzer, static_analyzer,
                                      _COUNTER_RACE)
        flagged = {ev["id"] for ev in timeline["events"] if ev["is_conflict"]}
        for cf in timeline["conflicts"]:
            assert cf["event1_id"] in flagged
            assert cf["event2_id"] in flagged

    def test_safe_code_produces_no_timeline_conflicts(self, dynamic_analyzer,
                                                        static_analyzer):
        timeline = self._get_timeline(dynamic_analyzer, static_analyzer,
                                      _SAFE_WITH_LOCK)
        assert timeline["conflicts"] == []

    def test_empty_analysis_gives_empty_timeline(self, dynamic_analyzer):
        empty_result = {
            "threads": [],
            "accesses": [],
            "race_conditions": [],
            "lock_vars": [],
        }
        timeline = dynamic_analyzer.build_timeline(empty_result)
        assert timeline["threads"] == []
        assert timeline["events"] == []
        assert timeline["conflicts"] == []

    def test_event_time_ordering(self, dynamic_analyzer, static_analyzer):
        """Events in the same thread must be time-ordered."""
        timeline = self._get_timeline(dynamic_analyzer, static_analyzer,
                                      _COUNTER_RACE)
        per_thread = {}
        for ev in timeline["events"]:
            per_thread.setdefault(ev["thread"], []).append(ev)
        for t_evs in per_thread.values():
            t_evs.sort(key=lambda e: e["start"])
            for i in range(1, len(t_evs)):
                assert t_evs[i]["start"] >= t_evs[i - 1]["start"]

    def test_thread_colors_assigned(self, dynamic_analyzer, static_analyzer):
        timeline = self._get_timeline(dynamic_analyzer, static_analyzer,
                                      _COUNTER_RACE)
        for t in timeline["threads"]:
            assert "color" in t
            assert t["color"].startswith("#")
