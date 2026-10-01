const $ = (id) => document.getElementById(id);
let mode = "group";
let postLimitEnabled = true;

function setMode(nextMode) {
  mode = nextMode;
  $("tabGroup").classList.toggle("on", mode === "group");
  $("tabComments").classList.toggle("on", mode === "comments");
  $("formGroup").style.display = mode === "group" ? "" : "none";
  $("formComments").style.display = mode === "comments" ? "" : "none";
  $("outputName").placeholder = mode === "group" ? "name_urls.json" : "name_comments.json";
}

function setPostLimitEnabled(enabled) {
  postLimitEnabled = enabled;
  const toggle = $("postLimitToggle");
  toggle.textContent = enabled ? "Post limit: ACTIVE" : "Post limit: INACTIVE";
  toggle.classList.toggle("active", enabled);
  toggle.classList.toggle("inactive", !enabled);
  toggle.setAttribute("aria-pressed", String(enabled));
  $("postLimit").disabled = !enabled;
}

async function api(url, opts) {
  const response = await fetch(url, opts);
  return response.json();
}

function renderStatus(status) {
  $("jobInfo").textContent = "Status: " + status;
}

function showResultText(text) {
  $("result").textContent = text;
}

function appendLog(lines) {
  const box = $("log");
  for (const line of lines) {
    const div = document.createElement("div");
    div.textContent = line;
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

async function refresh() {
  const status = await api("/api/status");
  const badge = $("sessionBadge");
  if (status.session) {
    badge.className = "badge b-ok";
    badge.textContent = "Session active";
  } else {
    badge.className = "badge b-bad";
    badge.textContent = "Login required";
  }

  const files = status.files || [];
  const source = $("commentsSourceFile");
  const selectedSource = source.value;
  source.innerHTML = '<option value="">Use a single URL</option>';
  const fileList = $("fileList");
  fileList.innerHTML = "";
  for (const file of files) {
    const option = document.createElement("option");
    option.value = file.name;
    option.textContent = file.name;
    source.appendChild(option);

    const item = document.createElement("li");
    const info = document.createElement("span");
    info.style.flex = "1";
    const link = document.createElement("a");
    link.href = file.view_url || file.url;
    link.target = "_blank";
    link.textContent = file.name;
    const size = document.createElement("span");
    size.className = "size";
    size.textContent = (file.size / 1024).toFixed(1) + " KB";
    const actions = document.createElement("span");
    actions.className = "file-actions";
    const view = document.createElement("button");
    view.className = "ghost";
    view.textContent = "View";
    view.onclick = () => window.open(file.view_url || file.url, "_blank");
    const rename = document.createElement("button");
    rename.className = "ghost";
    rename.textContent = "Rename";
    rename.onclick = () => renameFile(file.name);
    const remove = document.createElement("button");
    remove.className = "ghost";
    remove.textContent = "Delete";
    remove.onclick = () => deleteFile(file.name);
    info.append(link, document.createTextNode(" "), size);
    actions.append(view, rename, remove);
    item.append(info, actions);
    fileList.appendChild(item);
  }
  source.value = selectedSource;
  if (!files.length) {
    fileList.innerHTML = '<li class="center">No files</li>';
  }

  const job = status.job;
  if (!job) {
    renderStatus("Idle");
    $("runBtn").disabled = false;
    $("runBtn").textContent = "START";
    $("stopBtn").style.display = "none";
    return;
  }

  renderStatus(job.status);
  if (job.logs && job.logs.length) {
    $("log").innerHTML = "";
    appendLog(job.logs);
  }
  if (job.result) {
    const path = job.result.path;
    if (path) {
      fetch("/api/result/" + path.split(/[\\/]/).pop())
        .then((response) => (response.ok ? response.json() : null))
        .then((result) => result && showResultText(JSON.stringify(result, null, 2)));
    }
    $("jobInfo").textContent = "Finished. JSON saved: " + (path || "-");
  }
  if (job.error) {
    showResultText("ERROR: " + job.error);
  }

  const busy = ["pending", "running", "waiting_login", "stopping"].includes(job.status);
  $("runBtn").disabled = busy;
  $("runBtn").textContent = busy ? "Running..." : "START";
  $("stopBtn").style.display = busy ? "" : "none";
  $("stopBtn").disabled = job.status === "stopping";
  $("stopBtn").textContent = job.status === "stopping" ? "STOPPING..." : "STOP AND SAVE";
}

async function start() {
  const params = {};
  if (mode === "group") {
    const groupInput = $("groupInput").value.trim();
    if (!groupInput) {
      alert("Group ID or group URL is required");
      return;
    }
    params.group_input = groupInput;
    params.limit_enabled = postLimitEnabled;
    if (postLimitEnabled) {
      params.limit = parseInt($("postLimit").value || "10", 10);
    }
  } else {
    const url = $("commentsUrl").value.trim();
    const sourceFile = $("commentsSourceFile").value;
    if (!url && !sourceFile) {
      alert("A post URL or JSON file is required");
      return;
    }
    if (url) params.post_url = url;
    if (sourceFile) params.source_file = sourceFile;
  }
  const outputName = $("outputName").value.trim();
  if (outputName) params.output_name = outputName;

  const result = await api("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, params }),
  });
  if (result.error) alert(result.error);
}

async function clearSession() {
  if (!confirm("Delete the session file?")) return;
  const result = await api("/api/session", { method: "DELETE" });
  if (result.error) alert(result.error);
  refresh();
}

async function renameFile(name) {
  const next = prompt("New filename:", name);
  if (!next || !next.trim()) return;
  const result = await api("/api/files/" + encodeURIComponent(name), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: next.trim() }),
  });
  if (result.error) alert(result.error);
  refresh();
}

async function deleteFile(name) {
  if (!confirm("Delete this file?")) return;
  const result = await api("/api/files/" + encodeURIComponent(name), {
    method: "DELETE",
  });
  if (result.error) alert(result.error);
  refresh();
}

$("runBtn").onclick = start;
$("postLimitToggle").onclick = () => setPostLimitEnabled(!postLimitEnabled);
 $("tabGroup").onclick = () => setMode("group");
 $("tabComments").onclick = () => setMode("comments");
$("stopBtn").onclick = async () => {
  const result = await api("/api/stop", { method: "POST" });
  if (result.error) alert(result.error);
  refresh();
};
$("clearSessionBtn").onclick = clearSession;
$("clearJobBtn").onclick = async () => {
  await api("/api/clear", { method: "POST" });
  refresh();
};

setMode("group");
setPostLimitEnabled(true);
setInterval(refresh, 1500);
refresh();
