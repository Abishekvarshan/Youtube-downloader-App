const $ = (selector) => document.querySelector(selector);
let currentVideo = null;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...(options.body ? { "content-type": "application/json" } : {}), ...options.headers },
  });
  let data = {};
  try { data = await response.json(); } catch {}
  if (!response.ok) throw new Error(data.detail || "Something went wrong.");
  return data;
}

function showError(element, message) {
  element.className = message ? "error" : "";
  element.textContent = message || "";
}

function formatDuration(seconds) {
  if (!seconds) return "—";
  return [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60]
    .map((part, index) => index === 0 && part === 0 ? null : String(part).padStart(index ? 2 : 1, "0"))
    .filter((part) => part !== null).join(":");
}

function qualityOptions(formats) {
  const heights = [...new Set((formats || []).map((item) => item.height).filter(Boolean))]
    .sort((left, right) => right - left);
  return '<option value="best">Best available</option>' + heights
    .map((height) => `<option value="${height}">${height}p</option>`).join("");
}

function renderPreview(video) {
  currentVideo = video;
  const thumbnail = typeof video.thumbnail === "string" && /^https:\/\/(?:i\.ytimg\.com|img\.youtube\.com)\//.test(video.thumbnail)
    ? video.thumbnail : "";
  $("#preview").innerHTML = `
    <div class="preview-media">
      <img class="thumb" src="${escapeHtml(thumbnail)}" alt="Video thumbnail">
      <div>
        <h3 class="video-title">${escapeHtml(video.title)}</h3>
        <div class="meta"><span>◷ ${formatDuration(video.duration)}</span><span>◉ ${escapeHtml(video.uploader || "YouTube")}</span></div>
        <label class="field" style="display:block;margin-top:18px"><span class="label">Video quality</span><select id="quality">${qualityOptions(video.formats)}</select></label>
        <button id="add" class="primary download-btn">Add video to queue&nbsp; ↓</button>
        <div id="preview-error" aria-live="polite"></div>
      </div>
    </div>`;
  $("#add").addEventListener("click", addDownload);
}

async function fetchVideo() {
  const button = $("#fetch");
  const url = $("#url").value.trim();
  showError($("#form-error"), "");
  if (!url) return showError($("#form-error"), "Paste a YouTube video link first.");
  button.disabled = true;
  button.textContent = "Loading details…";
  try {
    renderPreview(await api("/api/info", { method: "POST", body: JSON.stringify({ url }) }));
  } catch (error) {
    showError($("#form-error"), error.message);
  } finally {
    button.disabled = false;
    button.innerHTML = "Get video details&nbsp; →";
  }
}

async function addDownload() {
  const button = $("#add");
  button.disabled = true;
  try {
    await api("/api/downloads", {
      method: "POST",
      body: JSON.stringify({ url: $("#url").value.trim(), quality: $("#quality").value }),
    });
    await refreshQueue();
  } catch (error) {
    showError($("#preview-error"), error.message);
  } finally {
    button.disabled = false;
  }
}

function renderJob(job) {
  const active = job.status === "queued" || job.status === "downloading";
  const actions = active
    ? `<div class="progress"><i style="width:${job.status === "queued" ? 0 : job.progress || 0}%"></i></div><div class="job-actions"><span class="job-meta">${job.status === "queued" ? "Waiting" : Math.round(job.progress || 0) + "%"}</span><button class="small-btn danger" data-action="cancel" data-id="${escapeHtml(job.id)}">Cancel</button></div>`
    : job.status === "completed"
      ? `<div class="job-actions"><a class="small-btn" href="/api/downloads/${encodeURIComponent(job.id)}/file">Save video ↓</a><button class="small-btn danger" data-action="delete" data-id="${escapeHtml(job.id)}">Delete</button></div>`
      : `<div class="job-meta">${escapeHtml(job.error || job.status)}</div><div class="job-actions"><button class="small-btn" data-action="retry" data-id="${escapeHtml(job.id)}">Retry</button><button class="small-btn danger" data-action="delete" data-id="${escapeHtml(job.id)}">Remove</button></div>`;
  return `<article class="queue-item"><div class="queue-top"><div><div class="job-title">${escapeHtml(job.title)}</div><div class="job-meta">Video · ${escapeHtml(job.quality)} ${job.quality === "best" ? "" : "p"} · ${new Date(job.created * 1000).toLocaleString()}</div></div><span class="status ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></div>${actions}</article>`;
}

async function refreshQueue() {
  try {
    const jobs = await api("/api/downloads");
    $("#queue").innerHTML = jobs.length ? jobs.map(renderJob).join("") : '<div class="empty-history">Your queue is empty.</div>';
  } catch (error) {
    showError($("#form-error"), error.message);
  }
}

async function handleQueueAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, id } = button.dataset;
  try {
    if (action === "cancel") await api(`/api/downloads/${encodeURIComponent(id)}/cancel`, { method: "POST" });
    if (action === "retry") await api(`/api/downloads/${encodeURIComponent(id)}/retry`, { method: "POST" });
    if (action === "delete") await api(`/api/downloads/${encodeURIComponent(id)}`, { method: "DELETE" });
    await refreshQueue();
  } catch (error) {
    showError($("#form-error"), error.message);
  }
}

$("#fetch").addEventListener("click", fetchVideo);
$("#refresh").addEventListener("click", refreshQueue);
$("#queue").addEventListener("click", handleQueueAction);
refreshQueue();
window.setInterval(refreshQueue, 2500);
