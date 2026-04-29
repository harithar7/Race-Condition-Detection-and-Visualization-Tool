/**
 * Race Condition Detection & Visualization Tool — UI controller.
 *
 * Handles CodeMirror editor, example loading, API calls, and rendering
 * of analysis results (race cards, suggestions, and timeline visualization).
 */

// ── Editor setup ──────────────────────────────────────────────────────────
const editor = CodeMirror(document.getElementById("editor-mount"), {
  mode: "python",
  theme: "dracula",
  lineNumbers: true,
  indentUnit: 4,
  tabSize: 4,
  indentWithTabs: false,
  lineWrapping: false,
  autofocus: true,
  extraKeys: {
    "Ctrl-Enter": analyzeCode,
    "Cmd-Enter":  analyzeCode,
    Tab: (cm) => cm.replaceSelection("    "),
  },
});

// ── Globals ───────────────────────────────────────────────────────────────
let _currentResult = null;
let _highlightedLines = [];

// ── DOM references ────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const analyzeBtn        = $("btn-analyze");
const clearBtn          = $("btn-clear");
const examplesBtn       = $("btn-examples");
const examplesMenu      = $("examples-menu");
const examplesDropdown  = $("examples-dropdown");

const tabBtns           = document.querySelectorAll(".tab-btn");
const tabContents       = document.querySelectorAll(".tab-content");

const racesTab          = $("tab-races");
const suggestionsTab    = $("tab-suggestions");
const vizTab            = $("tab-viz");
const racesBadge        = $("races-badge");
const analyzingOverlay  = $("analyzing-overlay");
const errorAlert        = $("error-alert");

const statRaces         = $("stat-races");
const statThreads       = $("stat-threads");
const statVars          = $("stat-vars");
const statInterleavings = $("stat-interleavings");
const summaryBar        = $("summary-bar");

// ── Examples dropdown ─────────────────────────────────────────────────────
examplesBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  examplesDropdown.classList.toggle("open");
});

document.addEventListener("click", () => {
  examplesDropdown.classList.remove("open");
});

// Load examples from API
fetch("/api/examples")
  .then(r => r.json())
  .then(examples => {
    examplesMenu.innerHTML = "";
    examples.forEach(ex => {
      const item = document.createElement("div");
      item.className = "dropdown-item";
      item.innerHTML = `
        <div class="item-title">${_escHtml(ex.title)}</div>
        <div class="item-desc">${_escHtml(ex.description)}</div>
      `;
      item.addEventListener("click", () => {
        editor.setValue(ex.code);
        examplesDropdown.classList.remove("open");
        clearResults();
        analyzeCode();
      });
      examplesMenu.appendChild(item);
    });
  })
  .catch(() => {
    examplesMenu.innerHTML = '<div class="dropdown-item"><div class="item-title">Failed to load examples</div></div>';
  });

// ── Toolbar buttons ───────────────────────────────────────────────────────
analyzeBtn.addEventListener("click", analyzeCode);

clearBtn.addEventListener("click", () => {
  editor.setValue("");
  clearResults();
});

// ── Tab switching ─────────────────────────────────────────────────────────
tabBtns.forEach(btn => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    tabBtns.forEach(b => b.classList.toggle("active", b.dataset.tab === target));
    tabContents.forEach(c => c.classList.toggle("active", c.id === `tab-${target}`));

    // Render visualization when that tab becomes active
    if (target === "viz" && _currentResult) {
      VIZ.render(_currentResult.timeline);
    }
  });
});

// ── Core: analyze ─────────────────────────────────────────────────────────
async function analyzeCode() {
  const code = editor.getValue().trim();
  if (!code) return;

  setAnalyzing(true);
  clearResults(false);
  clearEditorHighlights();

  try {
    const resp = await fetch("/api/analyze", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ code }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
      showError(err.error || "Analysis failed.");
      return;
    }

    const data = await resp.json();
    _currentResult = data;
    renderResults(data);

  } catch (err) {
    showError(`Network error: ${err.message}`);
  } finally {
    setAnalyzing(false);
  }
}

// ── Render results ────────────────────────────────────────────────────────
function renderResults(data) {
  const { static: st, timeline } = data;

  if (!st) return;

  // Parse errors
  if (st.errors && st.errors.length > 0) {
    showError(st.errors.join("\n"));
    return;
  }

  const races   = st.race_conditions || [];
  const threads = st.threads          || [];
  const vars    = st.shared_variables || [];

  // ── Summary bar ──
  summaryBar.style.display = "flex";
  statRaces.textContent    = races.length;
  statRaces.className      = `stat-value ${races.length > 0 ? "danger" : "success"}`;
  statThreads.textContent  = threads.length;
  statVars.textContent     = vars.length;
  statInterleavings.textContent = timeline
    ? (timeline.interleavings_checked || 0).toLocaleString()
    : "—";

  // ── Badge ──
  if (races.length > 0) {
    racesBadge.textContent = races.length;
    racesBadge.className   = "tab-badge";
    racesBadge.style.display = "inline-block";
  } else {
    racesBadge.textContent  = "✓";
    racesBadge.className    = "tab-badge safe";
    racesBadge.style.display = "inline-block";
  }

  // ── Race conditions tab ──
  const raceList = $("race-list");
  raceList.innerHTML = "";

  if (races.length === 0) {
    raceList.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M9 12l2 2 4-4M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <p>No race conditions detected!</p>
        <span class="hint">All shared variables appear to be properly synchronized.</span>
      </div>`;
  } else {
    races.forEach((rc, i) => {
      raceList.appendChild(buildRaceCard(rc, i));
    });
    // Highlight conflicting lines in editor
    highlightRaceLines(races);
  }

  // ── Suggestions tab ──
  const suggestions = st.suggestions || [];
  const suggList    = $("suggestion-list");
  suggList.innerHTML = "";
  suggestions.forEach((s, i) => {
    suggList.appendChild(buildSuggestionCard(s, i));
  });

  // ── Auto-switch to races tab ──
  activateTab("races");

  // ── Render timeline if already on viz tab ──
  const vizTabContent = $("tab-viz");
  if (vizTabContent && vizTabContent.classList.contains("active") && timeline) {
    VIZ.render(timeline);
  }
}

// ── Build a race card DOM element ─────────────────────────────────────────
function buildRaceCard(rc, index) {
  const card = document.createElement("div");
  card.className = "race-card";

  const icon = rc.conflict_type === "write-write" ? "✏️" : "↕️";

  card.innerHTML = `
    <div class="race-card-header" onclick="toggleCard(this.parentElement)">
      <span class="severity-badge severity-${rc.severity}">${rc.severity}</span>
      <span class="race-card-title">${icon} <code>${_escHtml(rc.variable)}</code></span>
      <span class="conflict-type-badge">${_escHtml(rc.conflict_type)}</span>
      <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="9 18 15 12 9 6"/>
      </svg>
    </div>
    <div class="race-card-body">
      <p>${_escHtml(rc.description)}</p>
      <table class="access-table">
        <thead>
          <tr>
            <th>Thread</th>
            <th>Access</th>
            <th>Line</th>
            <th>Lock</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>${_escHtml(rc.thread1)}</code></td>
            <td class="${rc.access1.access_type}">${rc.access1.access_type.toUpperCase()}</td>
            <td>${rc.access1.line}</td>
            <td>${rc.access1.protected_by ? `<code>${_escHtml(rc.access1.protected_by)}</code>` : '<span style="color:#f44336">none</span>'}</td>
          </tr>
          <tr>
            <td><code>${_escHtml(rc.thread2)}</code></td>
            <td class="${rc.access2.access_type}">${rc.access2.access_type.toUpperCase()}</td>
            <td>${rc.access2.line}</td>
            <td>${rc.access2.protected_by ? `<code>${_escHtml(rc.access2.protected_by)}</code>` : '<span style="color:#f44336">none</span>'}</td>
          </tr>
        </tbody>
      </table>
      <div class="suggestion-box">
        <div class="suggestion-label">💡 Fix suggestion</div>
        ${_escHtml(rc.suggestion)}
      </div>
    </div>
  `;

  // Auto-expand first card
  if (index === 0) card.classList.add("expanded");
  return card;
}

// ── Build a suggestion card DOM element ──────────────────────────────────
function buildSuggestionCard(s, index) {
  const card = document.createElement("div");
  card.className = "suggestion-card";

  const icons = ["🔒", "📬", "🧵", "⚛️", "🔄"];
  const icon  = icons[index % icons.length];

  card.innerHTML = `
    <div class="suggestion-card-header" onclick="toggleCard(this.parentElement)">
      <span>${icon}</span>
      <span class="suggestion-card-title">${_escHtml(s.title)}</span>
      <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="9 18 15 12 9 6"/>
      </svg>
    </div>
    <div class="suggestion-card-body">
      <p>${_escHtml(s.description)}</p>
      <pre>${_escHtml(s.example)}</pre>
    </div>
  `;

  if (index === 0) card.classList.add("expanded");
  return card;
}

// ── Editor line highlights ────────────────────────────────────────────────
function highlightRaceLines(races) {
  clearEditorHighlights();
  const lines = new Set();
  races.forEach(rc => {
    if (rc.access1.line) lines.add(rc.access1.line - 1);
    if (rc.access2.line) lines.add(rc.access2.line - 1);
  });
  lines.forEach(line => {
    const handle = editor.addLineClass(line, "background", "cm-race-line");
    _highlightedLines.push(handle);
  });
}

function clearEditorHighlights() {
  _highlightedLines.forEach(h => {
    try { editor.removeLineClass(h, "background", "cm-race-line"); } catch (_) {}
  });
  _highlightedLines = [];
}

// ── Utilities ─────────────────────────────────────────────────────────────
function toggleCard(card) {
  card.classList.toggle("expanded");
}

function activateTab(name) {
  tabBtns.forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  tabContents.forEach(c => c.classList.toggle("active", c.id === `tab-${name}`));
}

function setAnalyzing(active) {
  analyzingOverlay.classList.toggle("visible", active);
  analyzeBtn.disabled = active;
}

function clearResults(resetEditor) {
  _currentResult = null;
  VIZ.clear();
  errorAlert.classList.remove("visible");
  racesBadge.style.display = "none";
  summaryBar.style.display = "none";

  const raceList  = $("race-list");
  const suggList  = $("suggestion-list");
  const vizWrapper = $("timeline-svg-wrapper");

  if (raceList)   raceList.innerHTML  = _emptyHtml("Run analysis to see results here.");
  if (suggList)   suggList.innerHTML  = _emptyHtml("Synchronization suggestions will appear here.");
  if (vizWrapper) vizWrapper.innerHTML = _emptyHtml("Thread timeline visualization will appear here.");
}

function showError(msg) {
  errorAlert.textContent = "⚠ " + msg;
  errorAlert.classList.add("visible");
}

function _emptyHtml(text) {
  return `
    <div class="empty-state">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <p>${_escHtml(text)}</p>
    </div>`;
}

function _escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── Initial state ─────────────────────────────────────────────────────────
clearResults(false);
summaryBar.style.display = "none";

// ── Keyboard shortcut hint ────────────────────────────────────────────────
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") analyzeCode();
});
