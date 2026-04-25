import * as tus from "https://cdn.jsdelivr.net/npm/tus-js-client@4/+esm";

const state = {
  items: [],
  map: new Map(),
  paused: false,
  running: 0,
  active: new Map(),
  cursor: 0,
  batchId: null,
  startedAt: null,
  pollTimer: null,
};

const els = {
  endpoint: document.getElementById("endpoint"),
  uploader: document.getElementById("uploader"),
  batchName: document.getElementById("batchName"),
  concurrency: document.getElementById("concurrency"),
  chunkMiB: document.getElementById("chunkMiB"),
  fileInput: document.getElementById("fileInput"),
  folderInput: document.getElementById("folderInput"),
  startBtn: document.getElementById("startBtn"),
  pauseBtn: document.getElementById("pauseBtn"),
  resumeBtn: document.getElementById("resumeBtn"),
  clearBtn: document.getElementById("clearBtn"),
  dropzone: document.getElementById("dropzone"),
  queueBody: document.getElementById("queueBody"),
  summaryText: document.getElementById("summaryText"),
  serverBatchText: document.getElementById("serverBatchText"),
  overallProgress: document.getElementById("overallProgress"),
  log: document.getElementById("log"),
};

function fmtBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let i = 1; i < units.length && value >= 1024; i += 1) {
    value /= 1024;
    unit = units[i];
  }
  return `${value.toFixed(1)} ${unit}`;
}

function log(line) {
  const ts = new Date().toISOString().replace("T", " ").slice(0, 19);
  els.log.textContent += `[${ts}] ${line}\n`;
  els.log.scrollTop = els.log.scrollHeight;
}

function updateSummary() {
  const totalFiles = state.items.length;
  const done = state.items.filter((x) => x.status === "done").length;
  const failed = state.items.filter((x) => x.status === "error").length;
  const active = state.items.filter((x) => x.status === "uploading").length;

  const loaded = state.items.reduce((sum, x) => sum + x.uploaded, 0);
  const total = state.items.reduce((sum, x) => sum + x.file.size, 0);
  const pct = total > 0 ? (loaded / total) * 100 : 0;

  els.summaryText.textContent = `${done}/${totalFiles} done, ${active} active, ${failed} failed`;
  els.overallProgress.value = Math.max(0, Math.min(100, pct));
}

function rowFor(item) {
  if (item.row) return item.row;

  const tr = document.createElement("tr");
  const fileTd = document.createElement("td");
  const sizeTd = document.createElement("td");
  const statusTd = document.createElement("td");
  const progressTd = document.createElement("td");

  const statusSpan = document.createElement("span");
  statusSpan.className = "status queued";
  statusSpan.textContent = "queued";

  const progressText = document.createElement("span");
  progressText.textContent = "0.0%";

  fileTd.textContent = item.relativePath;
  sizeTd.textContent = fmtBytes(item.file.size);
  statusTd.appendChild(statusSpan);
  progressTd.appendChild(progressText);

  tr.append(fileTd, sizeTd, statusTd, progressTd);
  els.queueBody.appendChild(tr);

  item.row = tr;
  item.statusEl = statusSpan;
  item.progressEl = progressText;
  return tr;
}

function setStatus(item, status, note = "") {
  item.status = status;
  rowFor(item);
  item.statusEl.className = `status ${status}`;
  item.statusEl.textContent = note ? `${status}: ${note}` : status;
  updateSummary();
}

function setProgress(item, bytesUploaded, bytesTotal) {
  item.uploaded = bytesUploaded;
  rowFor(item);
  const pct = bytesTotal > 0 ? ((bytesUploaded / bytesTotal) * 100).toFixed(1) : "0.0";
  item.progressEl.textContent = `${pct}%`;
  updateSummary();
}

function getEndpoint() {
  return els.endpoint.value.trim();
}

function getNotifyStatusUrl(batchId) {
  try {
    const endpointUrl = new URL(getEndpoint(), window.location.href);
    return `${endpointUrl.origin}/notify/batch/${encodeURIComponent(batchId)}`;
  } catch (_err) {
    return null;
  }
}

function enqueueFiles(fileList) {
  const files = Array.from(fileList);
  for (const file of files) {
    const relativePath = file.webkitRelativePath || file.name;
    const id = `${relativePath}::${file.size}::${file.lastModified}`;
    if (state.map.has(id)) continue;

    const item = {
      id,
      file,
      relativePath,
      status: "queued",
      uploaded: 0,
      row: null,
      statusEl: null,
      progressEl: null,
      upload: null,
    };

    state.items.push(item);
    state.map.set(id, item);
    rowFor(item);
  }

  log(`Queued ${files.length} file(s).`);
  updateSummary();
}

function uploadOptions(item, batchId, batchTotal) {
  const chunkSizeBytes = Number(els.chunkMiB.value || 64) * 1024 * 1024;
  const uploader = els.uploader.value.trim();
  const userBatchName = els.batchName.value.trim();
  const batchName = userBatchName || `batch-${new Date().toISOString()}`;

  return {
    endpoint: getEndpoint(),
    chunkSize: chunkSizeBytes,
    retryDelays: [0, 1000, 3000, 5000, 10000, 20000, 30000, 60000],
    removeFingerprintOnSuccess: false,
    metadata: {
      filename: item.file.name,
      relative_path: item.relativePath,
      uploader,
      batch_id: batchId,
      batch_total: String(batchTotal),
      batch_name: batchName,
    },
    onError(error) {
      setStatus(item, "error", "retry later");
      state.running -= 1;
      state.active.delete(item.id);
      log(`ERROR ${item.relativePath}: ${String(error)}`);
      pumpQueue();
    },
    onProgress(bytesUploaded, bytesTotal) {
      setProgress(item, bytesUploaded, bytesTotal);
    },
    onSuccess() {
      setProgress(item, item.file.size, item.file.size);
      setStatus(item, "done");
      state.running -= 1;
      state.active.delete(item.id);
      log(`DONE ${item.relativePath}`);
      pumpQueue();
    },
  };
}

async function beginItem(item, batchId, batchTotal) {
  setStatus(item, "uploading");

  const options = uploadOptions(item, batchId, batchTotal);
  const upload = new tus.Upload(item.file, options);
  item.upload = upload;
  state.active.set(item.id, upload);

  try {
    const previousUploads = await upload.findPreviousUploads();
    if (previousUploads.length > 0) {
      upload.resumeFromPreviousUpload(previousUploads[0]);
      log(`RESUME ${item.relativePath}`);
    }
  } catch (err) {
    log(`WARN cannot check previous upload for ${item.relativePath}: ${String(err)}`);
  }

  upload.start();
}

function nextQueuedItem() {
  while (state.cursor < state.items.length) {
    const item = state.items[state.cursor++];
    if (item.status === "queued" || item.status === "error") {
      return item;
    }
  }
  return null;
}

function pumpQueue() {
  if (state.paused) return;

  const maxConcurrent = Math.max(1, Math.min(6, Number(els.concurrency.value || 3)));
  while (state.running < maxConcurrent) {
    const item = nextQueuedItem();
    if (!item) break;
    state.running += 1;
    beginItem(item, state.batchId, state.items.length);
  }

  updateSummary();
}

async function pollBatchStatusOnce() {
  if (!state.batchId) return;
  const url = getNotifyStatusUrl(state.batchId);
  if (!url) {
    els.serverBatchText.textContent = "Server batch status: invalid endpoint URL";
    return;
  }

  try {
    const response = await fetch(url, { cache: "no-store" });
    if (response.status === 404) {
      els.serverBatchText.textContent = "Server batch status: waiting for first completed file";
      return;
    }
    if (!response.ok) {
      els.serverBatchText.textContent = `Server batch status: polling error (${response.status})`;
      return;
    }

    const status = await response.json();
    const expected = status.expected_total ?? "?";
    const completed = status.completed_count ?? 0;
    const emailSent = status.email_sent === true;

    if (emailSent) {
      els.serverBatchText.textContent = `Server batch status: email sent (${completed}/${expected}) at ${status.emailed_at}`;
      stopBatchPolling();
      return;
    }

    if (status.is_complete) {
      els.serverBatchText.textContent = `Server batch status: complete on server (${completed}/${expected}), waiting for email dispatch`;
      return;
    }

    els.serverBatchText.textContent = `Server batch status: in progress (${completed}/${expected} completed server-side)`;
  } catch (err) {
    els.serverBatchText.textContent = `Server batch status: polling failed (${String(err)})`;
  }
}

function startBatchPolling() {
  stopBatchPolling();
  pollBatchStatusOnce();
  state.pollTimer = window.setInterval(pollBatchStatusOnce, 15000);
}

function stopBatchPolling() {
  if (state.pollTimer !== null) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startBatch() {
  if (!getEndpoint()) {
    log("Missing upload endpoint.");
    return;
  }
  const uploader = els.uploader.value.trim();
  if (!uploader) {
    log("Uploader name is required.");
    els.uploader.focus();
    return;
  }
  if (!state.items.length) {
    log("No files in queue.");
    return;
  }

  state.paused = false;
  state.cursor = 0;
  state.batchId = crypto.randomUUID();
  state.startedAt = Date.now();
  els.serverBatchText.textContent = `Server batch status: tracking batch ${state.batchId}`;

  // Reset non-complete items for a fresh run.
  for (const item of state.items) {
    if (item.status !== "done") {
      item.status = "queued";
      item.uploaded = 0;
      setStatus(item, "queued");
      setProgress(item, 0, item.file.size);
    }
  }

  log(`Starting batch ${state.batchId} with ${state.items.length} file(s).`);
  startBatchPolling();
  pumpQueue();
}

function pauseBatch() {
  state.paused = true;
  for (const [id, upload] of state.active.entries()) {
    const item = state.map.get(id);
    if (item && item.status === "uploading") {
      try {
        upload.abort();
      } catch (err) {
        log(`WARN pause failed for ${item.relativePath}: ${String(err)}`);
      }
      setStatus(item, "paused");
    }
  }
  state.active.clear();
  state.running = 0;
  log("Paused active uploads.");
  updateSummary();
}

function resumeBatch() {
  if (!state.items.length) return;

  state.paused = false;

  // Re-queue paused/error items from start.
  state.cursor = 0;
  for (const item of state.items) {
    if (item.status === "paused" || item.status === "error") {
      setStatus(item, "queued");
    }
  }

  log("Resuming upload queue.");
  pumpQueue();
}

function clearQueue() {
  pauseBatch();
  stopBatchPolling();
  state.items = [];
  state.map.clear();
  state.cursor = 0;
  state.batchId = null;
  els.serverBatchText.textContent = "Server batch status: not started";
  els.queueBody.innerHTML = "";
  log("Queue cleared.");
  updateSummary();
}

function collectDroppedFiles(dataTransfer) {
  // Browsers that support folder drag-drop expose files with relative paths.
  if (dataTransfer.files && dataTransfer.files.length > 0) {
    enqueueFiles(dataTransfer.files);
  }
}

function bindEvents() {
  els.fileInput.addEventListener("change", () => {
    enqueueFiles(els.fileInput.files);
    els.fileInput.value = "";
  });

  els.folderInput.addEventListener("change", () => {
    enqueueFiles(els.folderInput.files);
    els.folderInput.value = "";
  });

  els.startBtn.addEventListener("click", startBatch);
  els.pauseBtn.addEventListener("click", pauseBatch);
  els.resumeBtn.addEventListener("click", resumeBatch);
  els.clearBtn.addEventListener("click", clearQueue);

  ["dragenter", "dragover"].forEach((name) => {
    els.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      els.dropzone.classList.add("active");
    });
  });

  ["dragleave", "drop"].forEach((name) => {
    els.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      els.dropzone.classList.remove("active");
    });
  });

  els.dropzone.addEventListener("drop", (event) => {
    collectDroppedFiles(event.dataTransfer);
  });

  window.addEventListener("beforeunload", (event) => {
    if (state.running > 0) {
      event.preventDefault();
      event.returnValue = "Uploads are still running.";
    }
  });
}

bindEvents();
updateSummary();
log("Uploader ready.");
