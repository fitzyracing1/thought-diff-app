"use strict";

const editor = document.getElementById("editor");
const gutter = document.getElementById("gutter");
const fileList = document.getElementById("file-list");
const notice = document.getElementById("notice");
const reloadDisk = document.getElementById("reload-disk");

const state = {
  session: null,
  file: null,
  side: "working",
  dirty: false,
  saving: false,
  draft: null,
  lastWritten: null,
  diskConflict: false,
  ackedRevision: 0,
  fileToken: 0,
  saveId: 0,
  configReady: false,
};

function plural(n, singular, pluralLabel) {
  const value = Number(n) || 0;
  return `${value} ${value === 1 ? singular : pluralLabel}`;
}

function shortTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().slice(11, 19) + " UTC";
}

function lineCount(value) {
  return value ? value.split("\n").length : 1;
}

function lineNumberAt(value, offset) {
  return value.slice(0, offset).split("\n").length;
}

function updateGutter() {
  const current = lineNumberAt(editor.value, editor.selectionStart || 0);
  const count = lineCount(editor.value);
  gutter.replaceChildren();
  for (let i = 1; i <= count; i += 1) {
    const row = document.createElement("div");
    row.textContent = String(i);
    if (i === current) row.className = "here";
    gutter.appendChild(row);
  }
}

function setNotice(text, kind) {
  if (!text) {
    notice.hidden = true;
    notice.textContent = "";
    notice.className = "";
    return;
  }
  notice.hidden = false;
  notice.className = kind || "info";
  notice.textContent = text;
}

function showSessionNotices() {
  const session = state.session;
  if (!session) return;
  if (session.error) {
    setNotice(session.error, "error");
    reloadDisk.hidden = true;
    return;
  }
  if (state.diskConflict) {
    setNotice(
      "The working folder changed on disk while this file was edited here. Reload the file to see that copy. The text in the editor is still yours.",
      "info"
    );
    reloadDisk.hidden = false;
    return;
  }
  reloadDisk.hidden = true;
  const meta = session.meta || {};
  if (meta.same_path_warning) {
    setNotice("The original folder and the working folder are the same path, so the frozen copy is not frozen.", "warning");
    return;
  }
  const notes = meta.read_notes || [];
  if (notes.length) {
    setNotice(notes.join(" "), "warning");
    return;
  }
  setNotice("", "");
}

function renderSession(session) {
  state.session = session;
  const counts = session.counts || {};
  const meta = session.meta || {};
  const when = meta.evaluated_at ? shortTime(meta.evaluated_at) : "";
  const watching = meta.watching === false ? "Watching is off." : "Watching.";
  const status = document.getElementById("status");
  const summary = session.empty
    ? "Working copy matches the frozen original."
    : `${plural(counts.files_differ, "file differs", "files differ")} · ${plural(counts.thought_changes, "thought change", "thought changes")} · ${plural(counts.structure_notes, "structure note", "structure notes")}.`;
  status.textContent = `${summary} ${watching} ${when}`.trim();
  document.getElementById("count-code").textContent = counts.files_differ ? `· ${plural(counts.files_differ, "file", "files")}` : "";
  document.getElementById("count-thought").textContent = counts.thought_changes ? `· ${counts.thought_changes}` : "";
  document.getElementById("count-structure").textContent = counts.structure_notes ? `· ${counts.structure_notes}` : "";
  const fullNote = document.getElementById("full-note");
  if (fullNote && session.disclaimer) {
    const note = session.app_note ? `${session.app_note} ` : "";
    fullNote.textContent = note + session.disclaimer;
  }
  if (meta.limits) document.getElementById("limits").textContent = meta.limits + " The frozen original is never written.";
  const codeText = session.code_diff ? session.code_diff.replace(/\n$/, "") : "";
  fillLines(document.getElementById("code-diff"), codeText ? codeText.split("\n") : [], codeClass, "No code changes. The working copy matches the frozen original on text the comparer reads.");
  renderThought(session.thought_diff || []);
  fillLines(document.getElementById("structure-diff"), session.structure_diff || [], structureClass, "No structure changes.");
  document.getElementById("structure-original").textContent = (session.structure_original || []).join("\n");
  document.getElementById("structure-working").textContent = (session.structure_working || []).join("\n");
  renderFiles(session.files || []);
  showSessionNotices();
}

function fillLines(el, lines, classify, emptyText) {
  el.replaceChildren();
  const meaningful = lines.some((line) => line.length);
  if (!meaningful) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = emptyText;
    el.appendChild(p);
    return;
  }
  for (const line of lines) {
    const div = document.createElement("div");
    div.className = "ln " + classify(line);
    div.textContent = line.length ? line : " ";
    el.appendChild(div);
  }
}

function codeClass(line) {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

function structureClass(line) {
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("~")) return "chg";
  if (line.startsWith("!")) return "err";
  return "ctx";
}

function renderThought(lines) {
  const el = document.getElementById("thought-diff");
  el.replaceChildren();
  if (!lines.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "No interpretation changes.";
    el.appendChild(p);
    return;
  }
  let block = null;
  for (const line of lines) {
    if (!line.startsWith(" ") && !line.startsWith("\t")) {
      block = document.createElement("article");
      block.className = line.includes("reinterpreted") ? "tblock reint" : "tblock";
      const heading = document.createElement("h3");
      heading.textContent = line;
      heading.addEventListener("click", () => openFromThought(line));
      block.appendChild(heading);
      el.appendChild(block);
    } else if (block) {
      const pre = document.createElement("pre");
      const trimmed = line.trimStart();
      pre.className = trimmed.startsWith("+") ? "add" : trimmed.startsWith("-") ? "del" : "ctx";
      pre.textContent = line;
      block.appendChild(pre);
    }
  }
}

function renderFiles(files) {
  const query = document.getElementById("filter").value.trim().toLowerCase();
  const top = fileList.scrollTop;
  fileList.replaceChildren();
  for (const file of files) {
    if (query && !file.path.toLowerCase().includes(query)) continue;
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.path = file.path;
    if (state.file && state.file.path === file.path) button.setAttribute("aria-current", "true");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = file.path;
    const badge = document.createElement("span");
    badge.className = "badge " + file.status;
    badge.textContent = file.status;
    button.append(name, badge);
    button.addEventListener("click", () => selectFile(file.path));
    li.appendChild(button);
    fileList.appendChild(li);
  }
  if (!fileList.childElementCount) {
    const li = document.createElement("li");
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = files.length ? "No files match that filter." : "No compared files.";
    li.appendChild(p);
    fileList.appendChild(li);
  }
  fileList.scrollTop = top;
}

function fillConfig(meta) {
  document.getElementById("working").value = meta.working || "";
  document.getElementById("original").value = meta.original_folder || "";
  document.getElementById("git-ref").value = meta.git_ref || "";
  document.getElementById("repo").value = meta.repo || "";
  state.configReady = true;
}

async function getJSON(url, options) {
  const res = await fetch(url, options);
  let data = {};
  try {
    data = await res.json();
  } catch (_err) {
    data = { error: "The app returned a response that was not JSON." };
  }
  return { res, data };
}

function editorTextFor(file) {
  if (!file) return "";
  if (state.side === "original" || !file.exists_working) return file.original || "";
  return state.draft != null ? state.draft : (file.working || "");
}

function applyEditor(preserve) {
  const file = state.file;
  const readOnly = !file || state.side === "original" || !file.exists_working;
  editor.readOnly = readOnly;
  const next = file ? editorTextFor(file) : "";
  const prevPos = editor.selectionStart || 0;
  if (editor.value !== next) {
    editor.value = next;
    const pos = preserve ? Math.min(prevPos, next.length) : 0;
    editor.selectionStart = editor.selectionEnd = pos;
  }
  document.getElementById("side-working").setAttribute("aria-pressed", state.side === "working" ? "true" : "false");
  document.getElementById("side-original").setAttribute("aria-pressed", state.side === "original" ? "true" : "false");
  document.getElementById("delete-file").disabled = !file || !file.exists_working;
  const label = document.getElementById("open-label");
  if (!file) label.textContent = "No file open";
  else if (!file.exists_working) label.textContent = `${file.path} — removed from the working copy`;
  else if (state.side === "original") label.textContent = `${file.path} — frozen original, read only`;
  else label.textContent = file.path;
  updateGutter();
  updateLineStatus();
}

function updateLineStatus() {
  const el = document.getElementById("line-status");
  if (!state.file) {
    el.textContent = "Open a file to see the reading of the current line.";
    return;
  }
  if (state.dirty && state.side === "working") {
    el.textContent = "Editing. The line reading updates when this text is written to the working folder.";
    return;
  }
  const n = lineNumberAt(editor.value, editor.selectionStart || 0);
  const rows = state.file.rows || [];
  const row = state.side === "original"
    ? rows.find((item) => item.original_line === n)
    : rows.find((item) => item.working_line === n);
  if (!row) {
    el.textContent = `Line ${n}`;
    return;
  }
  const now = state.side === "original" ? row.original_reading : row.working_reading;
  const other = state.side === "original" ? row.working_reading : row.original_reading;
  if (other && other !== now) {
    el.textContent = `Line ${n}: ${now || "(no reading)"}  ·  was: ${other}`;
  } else {
    el.textContent = `Line ${n}: ${now || "(no reading)"}`;
  }
}

async function openFile(path, opts = {}) {
  const token = ++state.fileToken;
  const { res, data } = await getJSON("/api/file?path=" + encodeURIComponent(path));
  if (token !== state.fileToken) return false;
  if (!res.ok) {
    setNotice(data.error || "Could not open that file.", "error");
    return false;
  }
  state.file = data;
  if (!state.dirty) {
    state.draft = data.working;
    state.lastWritten = data.working;
    if (!data.exists_working) state.side = "original";
  }
  applyEditor(!!opts.preserve);
  if (!opts.preserve && opts.line) moveCursorToLine(opts.line);
  if (window.location.hash !== "#" + encodeURIComponent(path)) {
    history.replaceState(null, "", "#" + encodeURIComponent(path));
  }
  renderFiles((state.session && state.session.files) || []);
  return true;
}

async function selectFile(path) {
  if (state.file && state.file.path === path && !state.diskConflict) return;
  if (state.dirty) {
    window.clearTimeout(state.saveTimer);
    const ok = await saveOpenFile();
    if (!ok) return;
  }
  state.dirty = false;
  state.diskConflict = false;
  state.side = "working";
  await openFile(path);
}

function moveCursorToLine(n) {
  const lines = editor.value.split("\n");
  let pos = 0;
  for (let i = 0; i < n - 1 && i < lines.length; i += 1) pos += lines[i].length + 1;
  editor.focus();
  editor.selectionStart = editor.selectionEnd = pos;
  const lineHeight = 19.5;
  editor.scrollTop = Math.max(0, (n - 3) * lineHeight);
  gutter.scrollTop = editor.scrollTop;
  updateGutter();
  updateLineStatus();
}

async function openFromThought(header) {
  const match = header.match(/^(.+?):(\d+)/);
  if (!match) return;
  const path = match[1];
  const line = Number(match[2]);
  if (!state.file || state.file.path !== path) await selectFile(path);
  if (state.file && state.file.path === path) {
    state.side = "working";
    applyEditor(false);
    moveCursorToLine(line);
  }
}

async function saveOpenFile() {
  if (!state.file || !state.file.exists_working) return false;
  const id = ++state.saveId;
  const path = state.file.path;
  const content = state.side === "working" ? editor.value : state.draft;
  if (content == null) return false;
  state.saving = true;
  state.draft = content;
  try {
    const { res, data } = await getJSON("/api/file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content }),
    });
    if (id !== state.saveId) return false;
    if (!res.ok) {
      setNotice(data.error || "Could not write that file.", "error");
      return false;
    }
    state.lastWritten = content;
    state.ackedRevision = data.session.revision;
    renderSession(data.session);
    if (state.side === "working" && editor.value !== content) {
      state.dirty = true;
      state.draft = editor.value;
      window.clearTimeout(state.saveTimer);
      state.saveTimer = window.setTimeout(saveOpenFile, 350);
      return true;
    }
    state.dirty = false;
    state.diskConflict = false;
    state.file = data.file || state.file;
    state.draft = content;
    applyEditor(true);
    return true;
  } catch (_err) {
    setNotice("Cannot reach the Thought Diff app. Is it still running?", "error");
    return false;
  } finally {
    if (id === state.saveId) state.saving = false;
  }
}

async function checkDiskConflict() {
  if (!state.file || !state.dirty || state.saving) return;
  const { res, data } = await getJSON("/api/file?path=" + encodeURIComponent(state.file.path));
  if (!res.ok) return;
  const local = state.side === "working" ? editor.value : state.draft;
  if (data.working !== state.lastWritten && data.working !== local) {
    state.diskConflict = true;
  }
  showSessionNotices();
}

async function poll() {
  if (document.hidden) return;
  try {
    const { res, data } = await getJSON("/api/session");
    if (!res.ok) return;
    if (data.revision === state.ackedRevision) return;
    state.ackedRevision = data.revision;
    renderSession(data);
    if (!state.dirty) {
      state.diskConflict = false;
      if (state.file) await openFile(state.file.path, { preserve: true });
    } else {
      await checkDiskConflict();
    }
  } catch (_err) {
    setNotice("Cannot reach the Thought Diff app. Is it still running?", "error");
  }
}

async function boot() {
  const { res, data } = await getJSON("/api/session");
  if (!res.ok) {
    document.getElementById("status").textContent = data.error || "The comparison is not ready.";
    setNotice(data.error || "The comparison is not ready.", "error");
    return;
  }
  state.ackedRevision = data.revision;
  renderSession(data);
  fillConfig(data.meta || {});
  let hash = "";
  try {
    hash = decodeURIComponent((window.location.hash || "").replace(/^#/, ""));
  } catch (_err) {
    hash = "";
  }
  const files = data.files || [];
  const preferred = files.find((file) => file.path === hash)
    || files.find((file) => file.status !== "unmodified")
    || files[0];
  if (preferred) await openFile(preferred.path);
  window.setInterval(poll, 700);
}

document.getElementById("config").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {
    working: document.getElementById("working").value.trim(),
    original: document.getElementById("original").value.trim(),
    git_ref: document.getElementById("git-ref").value.trim(),
    repo: document.getElementById("repo").value.trim(),
  };
  const { res, data } = await getJSON("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    setNotice(data.error || "Could not open those folders.", "error");
    return;
  }
  state.dirty = false;
  state.diskConflict = false;
  state.file = null;
  state.draft = null;
  state.side = "working";
  state.ackedRevision = data.revision;
  renderSession(data);
  fillConfig(data.meta || {});
  editor.value = "";
  applyEditor(false);
  const files = data.files || [];
  const first = files.find((file) => file.status !== "unmodified") || files[0];
  if (first) await openFile(first.path);
});

document.getElementById("refresh").addEventListener("click", async () => {
  const { res, data } = await getJSON("/api/refresh", { method: "POST" });
  if (!res.ok) {
    setNotice(data.error || "Re-evaluation failed.", "error");
    return;
  }
  state.ackedRevision = data.revision;
  renderSession(data);
  if (!state.dirty && state.file) await openFile(state.file.path, { preserve: true });
});

document.getElementById("new-file").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const path = document.getElementById("new-path").value.trim();
  if (!path) return;
  if (state.dirty) {
    const ok = await saveOpenFile();
    if (!ok) return;
  }
  const { res, data } = await getJSON("/api/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content: "" }),
  });
  if (!res.ok) {
    setNotice(data.error || "Could not add that file.", "error");
    return;
  }
  document.getElementById("new-path").value = "";
  state.dirty = false;
  state.diskConflict = false;
  state.side = "working";
  state.ackedRevision = data.session.revision;
  state.file = data.file;
  state.draft = "";
  state.lastWritten = "";
  renderSession(data.session);
  applyEditor(false);
});

document.getElementById("delete-file").addEventListener("click", async () => {
  if (!state.file || !state.file.exists_working) return;
  const path = state.file.path;
  if (!window.confirm(`Delete ${path} from the working folder? The frozen original is not changed.`)) return;
  const { res, data } = await getJSON("/api/file?path=" + encodeURIComponent(path), { method: "DELETE" });
  if (!res.ok) {
    setNotice(data.error || "Could not delete that file.", "error");
    return;
  }
  state.dirty = false;
  state.diskConflict = false;
  state.ackedRevision = data.revision;
  renderSession(data);
  const still = (data.files || []).some((file) => file.path === path);
  if (still) {
    state.side = "original";
    await openFile(path);
  } else {
    state.file = null;
    state.draft = null;
    editor.value = "";
    applyEditor(false);
  }
});

function matchingLine(fromSide, toSide) {
  const n = lineNumberAt(editor.value, editor.selectionStart || 0);
  const rows = (state.file && state.file.rows) || [];
  const fromKey = fromSide === "original" ? "original_line" : "working_line";
  const toKey = toSide === "original" ? "original_line" : "working_line";
  const exact = rows.find((item) => item[fromKey] === n && item[toKey]);
  if (exact) return exact[toKey];
  const earlier = rows.filter((item) => item[fromKey] && item[fromKey] <= n && item[toKey]);
  if (earlier.length) return earlier[earlier.length - 1][toKey];
  return n;
}

function showSide(side) {
  if (!state.file || state.side === side) return;
  const line = matchingLine(state.side, side);
  if (state.side === "working") state.draft = editor.value;
  state.side = side;
  applyEditor(false);
  moveCursorToLine(line);
}

document.getElementById("side-working").addEventListener("click", () => showSide("working"));
document.getElementById("side-original").addEventListener("click", () => showSide("original"));

document.getElementById("filter").addEventListener("input", () => {
  renderFiles((state.session && state.session.files) || []);
});

reloadDisk.addEventListener("click", async () => {
  if (!state.file) return;
  state.dirty = false;
  state.diskConflict = false;
  state.draft = null;
  await openFile(state.file.path);
});

editor.addEventListener("scroll", () => {
  gutter.scrollTop = editor.scrollTop;
});

editor.addEventListener("input", () => {
  if (editor.readOnly) return;
  state.dirty = true;
  state.side = "working";
  state.draft = editor.value;
  updateGutter();
  updateLineStatus();
  window.clearTimeout(state.saveTimer);
  state.saveTimer = window.setTimeout(saveOpenFile, 350);
});

editor.addEventListener("keyup", updateLineStatus);
editor.addEventListener("click", () => {
  updateGutter();
  updateLineStatus();
});
editor.addEventListener("keydown", (ev) => {
  if (ev.key === "Tab") {
    ev.preventDefault();
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    editor.value = editor.value.slice(0, start) + "    " + editor.value.slice(end);
    editor.selectionStart = editor.selectionEnd = start + 4;
    editor.dispatchEvent(new Event("input"));
  }
});

document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
    ev.preventDefault();
    window.clearTimeout(state.saveTimer);
    saveOpenFile();
  }
});

boot().catch(() => {
  setNotice("Cannot reach the Thought Diff app. Is it still running?", "error");
});
