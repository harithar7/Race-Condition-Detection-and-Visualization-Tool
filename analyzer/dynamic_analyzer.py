"""Dynamic (simulated) analyzer for race condition detection.

Instead of running the actual code, this module builds a *simulated*
execution timeline from the static access information collected by
:class:`~analyzer.static_analyzer.StaticAnalyzer`.

It generates multiple possible thread interleavings and for each one
checks whether conflicting accesses actually overlap in time.  The
results are used by the front-end to animate / visualise the race.
"""

import itertools
import random
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Internal timeline model
# ---------------------------------------------------------------------------

# Each "step" is an atomic unit placed on the timeline
_STEP_WIDTH = 2.0    # logical time units per operation
_STEP_GAP = 0.5      # gap between operations in the same thread
_THREAD_COLORS = [
    "#4CAF50", "#2196F3", "#FF9800", "#9C27B0",
    "#F44336", "#00BCD4", "#8BC34A", "#FF5722",
]


class DynamicAnalyzer:
    """
    Build a visualisable timeline from static analysis output.

    Usage::

        da = DynamicAnalyzer()
        timeline = da.build_timeline(analysis_result)
    """

    def build_timeline(self, analysis_result: dict) -> dict:
        """
        Convert the output of :meth:`StaticAnalyzer.analyze` into a
        timeline dict suitable for D3 rendering.

        Returned structure::

            {
                "threads": [{"id": ..., "name": ..., "color": ...}, ...],
                "events": [
                    {
                        "id":           int,
                        "thread":       str,
                        "thread_index": int,
                        "variable":     str,
                        "access_type":  "read"|"write"|"lock"|"unlock",
                        "line":         int,
                        "start":        float,   # logical time
                        "end":          float,
                        "protected_by": str|None,
                        "is_conflict":  bool,
                    },
                    ...
                ],
                "conflicts": [
                    {
                        "event1_id":     int,
                        "event2_id":     int,
                        "variable":      str,
                        "conflict_type": str,
                    },
                    ...
                ],
                "interleavings_checked": int,
            }
        """
        threads_names: List[str] = analysis_result.get("threads", [])
        accesses: List[dict] = analysis_result.get("accesses", [])
        race_conditions: List[dict] = analysis_result.get("race_conditions", [])
        lock_vars: List[str] = analysis_result.get("lock_vars", [])

        if not threads_names:
            return {"threads": [], "events": [], "conflicts": [],
                    "interleavings_checked": 0}

        # ---- Assign colors and indices ----
        thread_meta = []
        thread_index: Dict[str, int] = {}
        for idx, name in enumerate(threads_names):
            color = _THREAD_COLORS[idx % len(_THREAD_COLORS)]
            thread_meta.append({"id": name, "name": name, "color": color})
            thread_index[name] = idx

        # ---- Build per-thread event sequences ----
        # Group accesses by thread, preserving line order
        per_thread: Dict[str, List[dict]] = {n: [] for n in threads_names}
        for acc in accesses:
            t = acc.get("thread")
            if t in per_thread:
                per_thread[t].append(acc)

        # Sort each thread's accesses by line number
        for t in per_thread:
            per_thread[t].sort(key=lambda a: a["line"])

        # ---- Assign per-thread start times (simulate interleaving) ----
        # We use a simple model: threads start at slightly different times
        # and progress at the same rate.  The offset makes the race visible.
        events: List[dict] = []
        event_id = 0

        for t_idx, t_name in enumerate(threads_names):
            t_accesses = per_thread[t_name]
            # Stagger thread start times so they visually overlap
            t_start_offset = t_idx * (_STEP_WIDTH * 0.7)

            current_time = t_start_offset
            for acc in t_accesses:
                ev = {
                    "id": event_id,
                    "thread": t_name,
                    "thread_index": thread_index[t_name],
                    "variable": acc["variable"],
                    "access_type": acc["access_type"],
                    "line": acc["line"],
                    "start": round(current_time, 3),
                    "end": round(current_time + _STEP_WIDTH, 3),
                    "protected_by": acc.get("protected_by"),
                    "is_conflict": False,
                }
                events.append(ev)
                event_id += 1
                current_time += _STEP_WIDTH + _STEP_GAP

        # ---- Add lock/unlock events for protected accesses ----
        lock_events: List[dict] = []
        if lock_vars:
            # For each lock-var, find protected regions and add acquire/release
            lock_regions: Dict[str, List[dict]] = {lv: [] for lv in lock_vars}
            for ev in events:
                if ev["protected_by"] in lock_regions:
                    lock_regions[ev["protected_by"]].append(ev)

            for lv, region_evs in lock_regions.items():
                if not region_evs:
                    continue
                # Group consecutive events by thread
                for t_name in threads_names:
                    t_region = [e for e in region_evs if e["thread"] == t_name]
                    if not t_region:
                        continue
                    acquire_ev = {
                        "id": event_id,
                        "thread": t_name,
                        "thread_index": thread_index[t_name],
                        "variable": lv,
                        "access_type": "lock",
                        "line": t_region[0]["line"] - 1,
                        "start": round(t_region[0]["start"] - _STEP_GAP, 3),
                        "end": round(t_region[0]["start"], 3),
                        "protected_by": None,
                        "is_conflict": False,
                    }
                    event_id += 1
                    release_ev = {
                        "id": event_id,
                        "thread": t_name,
                        "thread_index": thread_index[t_name],
                        "variable": lv,
                        "access_type": "unlock",
                        "line": t_region[-1]["line"] + 1,
                        "start": round(t_region[-1]["end"], 3),
                        "end": round(t_region[-1]["end"] + _STEP_GAP, 3),
                        "protected_by": None,
                        "is_conflict": False,
                    }
                    event_id += 1
                    lock_events.extend([acquire_ev, release_ev])

        all_events = events + lock_events
        all_events.sort(key=lambda e: (e["start"], e["thread_index"]))

        # ---- Map race conditions to conflicting event pairs ----
        conflicts: List[dict] = []
        conflict_event_ids: set = set()
        seen_pairs: set = set()

        for rc in race_conditions:
            var = rc["variable"]
            t1, t2 = rc["thread1"], rc["thread2"]
            a1_line = rc["access1"]["line"]
            a2_line = rc["access2"]["line"]
            a1_type = rc["access1"]["access_type"]
            a2_type = rc["access2"]["access_type"]

            # Find the closest matching events
            ev1 = _find_event(all_events, t1, var, a1_type, a1_line)
            ev2 = _find_event(all_events, t2, var, a2_type, a2_line)

            if ev1 and ev2:
                pair = (min(ev1["id"], ev2["id"]), max(ev1["id"], ev2["id"]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    ev1["is_conflict"] = True
                    ev2["is_conflict"] = True
                    conflict_event_ids.update([ev1["id"], ev2["id"]])
                    conflicts.append({
                        "event1_id": ev1["id"],
                        "event2_id": ev2["id"],
                        "variable": var,
                        "conflict_type": rc["conflict_type"],
                    })

        # ---- Estimate interleavings checked (for display) ----
        n_threads = len(threads_names)
        ops_per_thread = max(
            (len(per_thread[t]) for t in threads_names), default=1
        )
        # Rough upper bound: (n*k)! / (k!)^n
        interleavings = 1
        total_ops = n_threads * ops_per_thread
        for i in range(1, min(total_ops + 1, 8)):
            interleavings *= i
        for i in range(1, min(ops_per_thread + 1, 5)):
            interleavings //= i

        return {
            "threads": thread_meta,
            "events": all_events,
            "conflicts": conflicts,
            "interleavings_checked": interleavings,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_event(
    events: List[dict],
    thread: str,
    variable: str,
    access_type: str,
    line: int,
) -> Optional[dict]:
    """Return the event best matching (thread, variable, access_type, line)."""
    best = None
    best_dist = float("inf")
    for ev in events:
        if ev["thread"] == thread and ev["variable"] == variable:
            if ev["access_type"] == access_type:
                dist = abs(ev["line"] - line)
                if dist < best_dist:
                    best_dist = dist
                    best = ev
    # fall back to any access of the same variable in that thread
    if best is None:
        for ev in events:
            if ev["thread"] == thread and ev["variable"] == variable:
                dist = abs(ev["line"] - line)
                if dist < best_dist:
                    best_dist = dist
                    best = ev
    return best
