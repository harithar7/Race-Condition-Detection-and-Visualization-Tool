"""Static analyzer for detecting race conditions in Python multithreaded code.

Uses Python's ``ast`` module to parse source code and track shared-variable
accesses across thread functions, detecting read-write / write-write conflicts
and missing synchronization.
"""

import ast
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class VariableAccess:
    """A single read or write of a variable inside a thread function."""
    thread: str
    variable: str
    access_type: str          # 'read' | 'write'
    line: int
    col: int = 0
    protected_by: Optional[str] = None   # name of the lock (if any)


@dataclass
class RaceCondition:
    """Two conflicting accesses that constitute a race condition."""
    variable: str
    thread1: str
    thread2: str
    access1: VariableAccess
    access2: VariableAccess
    conflict_type: str        # 'write-write' | 'write-read' | 'read-write'
    severity: str             # 'high' | 'medium' | 'low'
    description: str
    suggestion: str


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

class _LockContextVisitor(ast.NodeVisitor):
    """Walk a function body and record which lock (if any) guards each line."""

    def __init__(self):
        self.lock_map: Dict[int, str] = {}   # line -> lock_name
        self._lock_stack: List[str] = []

    def _register_lines(self, node: ast.AST, lock: str) -> None:
        for child in ast.walk(node):
            if hasattr(child, "lineno"):
                self.lock_map[child.lineno] = lock

    def visit_With(self, node: ast.With) -> None:
        lock_name = None
        for item in node.items:
            ctx_expr = item.context_expr
            # with lock: / with self.lock:
            if isinstance(ctx_expr, ast.Name):
                lock_name = ctx_expr.id
            elif isinstance(ctx_expr, ast.Attribute):
                lock_name = f"{_attr_chain(ctx_expr)}"
            # with lock.acquire_lock() or similar call
            elif isinstance(ctx_expr, ast.Call):
                if isinstance(ctx_expr.func, ast.Attribute):
                    lock_name = _attr_chain(ctx_expr.func.value)
                elif isinstance(ctx_expr.func, ast.Name):
                    lock_name = ctx_expr.func.id

        if lock_name:
            self._lock_stack.append(lock_name)
            for stmt in node.body:
                self._register_lines(stmt, lock_name)
            self.generic_visit(node)
            self._lock_stack.pop()
        else:
            self.generic_visit(node)


def _attr_chain(node: ast.AST) -> str:
    """Return dotted name for attribute chains like ``self.lock``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_attr_chain(node.value)}.{node.attr}"
    return "<expr>"


# ---------------------------------------------------------------------------
# Per-function access collector
# ---------------------------------------------------------------------------

class _FunctionAccessCollector(ast.NodeVisitor):
    """Collect all variable accesses inside a function, noting lock context."""

    def __init__(self, thread_name: str, shared_vars: Set[str],
                 lock_map: Dict[int, str]):
        self.thread_name = thread_name
        self.shared_vars = shared_vars
        self.lock_map = lock_map
        self.accesses: List[VariableAccess] = []
        # Track which names are locally assigned (not shared)
        self._local_vars: Set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        # Globals are definitely shared
        pass

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_target(target, node.lineno)
        # rhs reads
        self._collect_reads(node.value, node.lineno)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        name = _extract_name(node.target)
        if name:
            lock = self.lock_map.get(node.lineno)
            if name in self.shared_vars:
                self.accesses.append(VariableAccess(
                    thread=self.thread_name, variable=name,
                    access_type="read", line=node.lineno,
                    col=node.col_offset, protected_by=lock))
                self.accesses.append(VariableAccess(
                    thread=self.thread_name, variable=name,
                    access_type="write", line=node.lineno,
                    col=node.col_offset, protected_by=lock))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self._record_target(node.target, node.lineno)
            self._collect_reads(node.value, node.lineno)

    def _record_target(self, target: ast.AST, lineno: int) -> None:
        name = _extract_name(target)
        if name:
            lock = self.lock_map.get(lineno)
            if name in self.shared_vars:
                self.accesses.append(VariableAccess(
                    thread=self.thread_name, variable=name,
                    access_type="write", line=lineno,
                    col=target.col_offset if hasattr(target, "col_offset") else 0,
                    protected_by=lock))
            else:
                self._local_vars.add(name)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._record_target(elt, lineno)

    def _collect_reads(self, node: ast.AST, lineno: int) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                if child.id in self.shared_vars:
                    lock = self.lock_map.get(lineno)
                    self.accesses.append(VariableAccess(
                        thread=self.thread_name, variable=child.id,
                        access_type="read", line=lineno,
                        col=child.col_offset, protected_by=lock))

    def visit_Expr(self, node: ast.Expr) -> None:
        self._collect_reads(node, node.lineno)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value:
            self._collect_reads(node.value, node.lineno)

    def visit_If(self, node: ast.If) -> None:
        self._collect_reads(node.test, node.lineno)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._collect_reads(node.test, node.lineno)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._collect_reads(node.iter, node.lineno)
        self._record_target(node.target, node.lineno)
        self.generic_visit(node)


def _extract_name(node: ast.AST) -> Optional[str]:
    """Return a simple name from a target node, or None if complex."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _attr_chain(node)
    return None


# ---------------------------------------------------------------------------
# Module-level shared variable finder
# ---------------------------------------------------------------------------

class _SharedVarFinder(ast.NodeVisitor):
    """
    Find module-level variable names and also variables explicitly declared
    ``global`` inside any function (so they are definitely shared).
    """

    def __init__(self):
        self.shared: Set[str] = set()
        self._in_function = False

    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    name = _extract_name(target)
                    if name:
                        self.shared.add(name)
            elif isinstance(stmt, ast.AnnAssign):
                name = _extract_name(stmt.target)
                if name:
                    self.shared.add(name)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.visit(stmt)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        old = self._in_function
        self._in_function = True
        self.generic_visit(node)
        self._in_function = old

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.shared.add(name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # class-level attributes are also potentially shared
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    name = _extract_name(target)
                    if name and not name.startswith("__"):
                        self.shared.add(name)


# ---------------------------------------------------------------------------
# Thread finder
# ---------------------------------------------------------------------------

_LOCK_CLASSES = {
    "Lock", "RLock", "Semaphore", "BoundedSemaphore",
    "threading.Lock", "threading.RLock", "threading.Semaphore",
}

_THREAD_SAFE_CLASSES = {
    "Queue", "queue.Queue", "JoinableQueue",
    "deque",  # thread-safe for appends/pops from both ends
}


class _ThreadFinder(ast.NodeVisitor):
    """
    Identify thread target functions.

    Handles patterns like:
        t = threading.Thread(target=worker)
        t = Thread(target=worker)
        with ThreadPoolExecutor() as ex:
            ex.submit(worker, ...)

    ``thread_target_counts`` maps each target function name to the number of
    Thread instances that use it.  When the same function is the target of
    multiple threads (e.g. ``t1 = Thread(target=f)`` and
    ``t2 = Thread(target=f)``), the analyzer creates synthetic per-instance
    thread names (``f[0]``, ``f[1]``, …) so that cross-thread races on
    shared variables are still detected.
    """

    def __init__(self, function_map: Dict[str, ast.FunctionDef]):
        self.function_map = function_map   # name -> AST node
        self.thread_targets: Set[str] = set()
        # func_name -> number of Thread objects pointing at it
        self.thread_target_counts: Dict[str, int] = {}
        self.lock_vars: Set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            self._check_call(node.value, node.targets)
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Call):
            self._check_call(node.value, [])
        self.generic_visit(node)

    def _check_call(self, call: ast.Call,
                    targets: List[ast.AST]) -> None:
        func_name = _call_func_name(call)

        # --- Thread creation ---
        if func_name in ("threading.Thread", "Thread"):
            target = _get_keyword(call, "target")
            if target:
                name = _extract_name(target)
                if name:
                    self.thread_targets.add(name)
                    self.thread_target_counts[name] = (
                        self.thread_target_counts.get(name, 0) + 1
                    )
            # positional: Thread(group, target, ...)
            elif len(call.args) >= 2:
                name = _extract_name(call.args[1])
                if name:
                    self.thread_targets.add(name)
                    self.thread_target_counts[name] = (
                        self.thread_target_counts.get(name, 0) + 1
                    )

        # --- ThreadPoolExecutor.submit / map ---
        if func_name in ("submit", "executor.submit", "ex.submit",
                         "pool.submit"):
            if call.args:
                name = _extract_name(call.args[0])
                if name:
                    self.thread_targets.add(name)
                    self.thread_target_counts[name] = (
                        self.thread_target_counts.get(name, 0) + 1
                    )

        # --- Lock creation ---
        if func_name in _LOCK_CLASSES:
            for tgt in targets:
                name = _extract_name(tgt)
                if name:
                    self.lock_vars.add(name)
            # self.lock = ...
        if func_name.endswith((".Lock", ".RLock", ".Semaphore")):
            for tgt in targets:
                name = _extract_name(tgt)
                if name:
                    self.lock_vars.add(name)


def _call_func_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return _attr_chain(call.func)
    return ""


def _get_keyword(call: ast.Call, name: str) -> Optional[ast.AST]:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


# ---------------------------------------------------------------------------
# Suggestion catalogue
# ---------------------------------------------------------------------------

SUGGESTIONS = {
    "write-write": (
        "high",
        "Two threads both write to '{var}' without synchronization — "
        "the final value is non-deterministic.",
        "Protect all accesses to '{var}' with a threading.Lock() or "
        "use thread-safe data structures (e.g., queue.Queue).",
    ),
    "write-read": (
        "high",
        "Thread '{t1}' writes to '{var}' while thread '{t2}' reads it "
        "without a lock — the reader may observe a partial or stale value.",
        "Acquire the same lock before both the write in '{t1}' (line {l1}) "
        "and the read in '{t2}' (line {l2}).",
    ),
    "read-write": (
        "high",
        "Thread '{t1}' reads '{var}' while thread '{t2}' may write to it "
        "concurrently — stale data may be used.",
        "Acquire the same lock before both the read in '{t1}' (line {l1}) "
        "and the write in '{t2}' (line {l2}).",
    ),
}

GENERAL_SUGGESTIONS = [
    {
        "title": "Use threading.Lock for mutual exclusion",
        "description": (
            "Wrap every access to a shared variable with "
            "`lock.acquire()` / `lock.release()`, or better, "
            "use the `with lock:` context manager."
        ),
        "example": (
            "lock = threading.Lock()\n"
            "# In each thread:\n"
            "with lock:\n"
            "    shared_var += 1"
        ),
    },
    {
        "title": "Use thread-safe queue.Queue",
        "description": (
            "Replace shared lists or counters with a `queue.Queue` "
            "instance. All operations on Queue are internally synchronized."
        ),
        "example": (
            "import queue\n"
            "q = queue.Queue()\n"
            "q.put(item)   # thread-safe\n"
            "item = q.get()  # thread-safe"
        ),
    },
    {
        "title": "Use threading.local() for per-thread data",
        "description": (
            "If each thread needs its own copy of a variable, use "
            "`threading.local()` so that every thread sees an independent "
            "instance without any locking overhead."
        ),
        "example": (
            "local_data = threading.local()\n"
            "local_data.counter = 0  # private to each thread"
        ),
    },
    {
        "title": "Avoid compound check-then-act patterns",
        "description": (
            "Patterns like `if counter > 0: counter -= 1` are not atomic. "
            "Another thread can modify `counter` between the check and the "
            "update. Always hold the lock for the entire compound operation."
        ),
        "example": (
            "with lock:\n"
            "    if counter > 0:\n"
            "        counter -= 1"
        ),
    },
    {
        "title": "Use atomic types or higher-level abstractions",
        "description": (
            "Consider using `concurrent.futures` thread pools with "
            "Future-based result passing, or the `multiprocessing` module "
            "with its managed objects (`Manager().Value`, `Manager().list`) "
            "to avoid manual synchronization."
        ),
        "example": (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "with ThreadPoolExecutor(max_workers=4) as ex:\n"
            "    futures = [ex.submit(work, item) for item in items]\n"
            "    results = [f.result() for f in futures]"
        ),
    },
]


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class StaticAnalyzer:
    """
    Analyze Python source code for race conditions.

    Usage::

        analyzer = StaticAnalyzer()
        result = analyzer.analyze(source_code)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, code: str) -> dict:
        """
        Parse *code* and return an analysis result dictionary.

        The returned dict has the following keys:

        ``threads``
            List of thread-function names found.

        ``shared_variables``
            List of variable names identified as shared.

        ``accesses``
            List of all variable-access records (serialised
            :class:`VariableAccess` dicts).

        ``race_conditions``
            List of detected races (serialised :class:`RaceCondition` dicts).

        ``suggestions``
            General synchronisation suggestions.

        ``lock_vars``
            Set of lock-variable names found in the code.

        ``errors``
            List of parse/analysis error messages (empty on success).
        """
        result = {
            "threads": [],
            "shared_variables": [],
            "accesses": [],
            "race_conditions": [],
            "suggestions": GENERAL_SUGGESTIONS,
            "lock_vars": [],
            "errors": [],
        }

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            result["errors"].append(f"Syntax error: {exc}")
            return result

        # --- Step 1: collect all function definitions ---
        function_map: Dict[str, ast.FunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_map[node.name] = node

        # --- Step 2: find shared (module-level / global) variables ---
        shared_finder = _SharedVarFinder()
        shared_finder.visit(tree)
        shared_vars: Set[str] = shared_finder.shared

        # --- Step 3: find thread targets and lock vars ---
        thread_finder = _ThreadFinder(function_map)
        thread_finder.visit(tree)
        thread_targets: Set[str] = thread_finder.thread_targets
        lock_vars: Set[str] = thread_finder.lock_vars
        target_counts: Dict[str, int] = thread_finder.thread_target_counts

        # If no explicit thread targets found, fall back to all non-private
        # functions (common in teaching-style code snippets)
        if not thread_targets:
            for name in function_map:
                if not name.startswith("_") and name not in ("main",):
                    thread_targets.add(name)

        # Build the final thread-name list.
        # If a function is used by N > 1 Thread objects we create N virtual
        # thread names (func[0], func[1], …) so cross-instance races are
        # detectable.  This covers the common pattern where the same function
        # is launched in two threads:
        #   t1 = Thread(target=f); t2 = Thread(target=f)
        expanded_threads: List[str] = []
        # maps virtual thread name -> actual function name
        thread_func_map: Dict[str, str] = {}
        for func_name in sorted(thread_targets):
            count = target_counts.get(func_name, 1)
            if count > 1:
                for i in range(count):
                    vname = f"{func_name}[{i}]"
                    expanded_threads.append(vname)
                    thread_func_map[vname] = func_name
            else:
                expanded_threads.append(func_name)
                thread_func_map[func_name] = func_name

        result["threads"] = expanded_threads
        result["lock_vars"] = sorted(lock_vars)

        # --- Step 4: collect per-thread accesses ---
        all_accesses: List[VariableAccess] = []
        for thread_name in expanded_threads:
            func_name = thread_func_map[thread_name]
            func_node = function_map.get(func_name)
            if func_node is None:
                continue

            # build lock map for this function
            lock_visitor = _LockContextVisitor()
            lock_visitor.visit(func_node)
            lock_map = lock_visitor.lock_map

            collector = _FunctionAccessCollector(
                thread_name=thread_name,
                shared_vars=shared_vars,
                lock_map=lock_map,
            )
            collector.visit(func_node)
            all_accesses.extend(collector.accesses)

        result["shared_variables"] = sorted(shared_vars)
        result["accesses"] = [
            {
                "thread": a.thread,
                "variable": a.variable,
                "access_type": a.access_type,
                "line": a.line,
                "col": a.col,
                "protected_by": a.protected_by,
            }
            for a in all_accesses
        ]

        # --- Step 5: detect races ---
        races = self._find_races(all_accesses)
        result["race_conditions"] = [
            {
                "variable": r.variable,
                "thread1": r.thread1,
                "thread2": r.thread2,
                "access1": {
                    "thread": r.access1.thread,
                    "variable": r.access1.variable,
                    "access_type": r.access1.access_type,
                    "line": r.access1.line,
                    "protected_by": r.access1.protected_by,
                },
                "access2": {
                    "thread": r.access2.thread,
                    "variable": r.access2.variable,
                    "access_type": r.access2.access_type,
                    "line": r.access2.line,
                    "protected_by": r.access2.protected_by,
                },
                "conflict_type": r.conflict_type,
                "severity": r.severity,
                "description": r.description,
                "suggestion": r.suggestion,
            }
            for r in races
        ]

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_races(
        self,
        accesses: List[VariableAccess],
    ) -> List[RaceCondition]:
        """
        Compare all pairs of accesses from different threads.
        A race exists when:
          - different threads access the same variable
          - at least one access is a write
          - they are NOT protected by the **same** non-None lock
        """
        races: List[RaceCondition] = []
        seen: Set[Tuple] = set()

        # Group by variable
        var_accesses: Dict[str, List[VariableAccess]] = {}
        for acc in accesses:
            var_accesses.setdefault(acc.variable, []).append(acc)

        for var, accs in var_accesses.items():
            for i, a1 in enumerate(accs):
                for a2 in accs[i + 1:]:
                    if a1.thread == a2.thread:
                        continue
                    # At least one must be a write
                    if a1.access_type == "read" and a2.access_type == "read":
                        continue
                    # Same non-None lock protects both → safe
                    if (a1.protected_by is not None
                            and a1.protected_by == a2.protected_by):
                        continue

                    # Deduplicate (variable, thread-pair, type-pair)
                    key = (var, a1.thread, a2.thread,
                           a1.access_type, a2.access_type)
                    rev_key = (var, a2.thread, a1.thread,
                               a2.access_type, a1.access_type)
                    if key in seen or rev_key in seen:
                        continue
                    seen.add(key)

                    conflict_type = (
                        f"{a1.access_type}-{a2.access_type}"
                    )
                    tmpl = SUGGESTIONS.get(
                        conflict_type,
                        SUGGESTIONS["write-read"],
                    )
                    severity = tmpl[0]
                    description = tmpl[1].format(
                        var=var, t1=a1.thread, t2=a2.thread,
                        l1=a1.line, l2=a2.line,
                    )
                    suggestion = tmpl[2].format(
                        var=var, t1=a1.thread, t2=a2.thread,
                        l1=a1.line, l2=a2.line,
                    )

                    races.append(RaceCondition(
                        variable=var,
                        thread1=a1.thread,
                        thread2=a2.thread,
                        access1=a1,
                        access2=a2,
                        conflict_type=conflict_type,
                        severity=severity,
                        description=description,
                        suggestion=suggestion,
                    ))

        return races
