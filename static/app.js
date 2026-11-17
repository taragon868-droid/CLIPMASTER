const jobsEl = document.querySelector("#jobs");
const healthEl = document.querySelector("#health");
const form = document.querySelector("#form");
const formError = document.querySelector("#form-error");
const submitBtn = document.querySelector("#submit");
const signatures = new Map();
const captionAsks = new Set();
let unipostReady = false;

function clock(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const remain = total % 60;
  return `${minutes}:${String(remain).padStart(2, "0")}`;
}

function signature(job) {
  const tiktok = job.tiktok || {};
  return JSON.stringify({
    tiktok: [tiktok.state, tiktok.error, tiktok.account],
    clips: (job.clips || []).map((clip) => [
      clip.index, clip.rev, clip.ready, clip.start, clip.end, clip.title, clip.hook, clip.score, clip.reason,
      clip.caption, clip.post_status, clip.post_error,
    ]),
  });
}

function clipCard(job, clip) {
  const wrap = document.createElement("article");
  wrap.className = "clip";
  wrap.dataset.index = String(clip.index);
  const media = document.createElement("div");
  if (clip.ready) {
    const video = document.createElement("video");
    video.controls = true;
    video.playsInline = true;
    video.src = `/api/jobs/${job.id}/clips/${clip.index}/file?rev=${clip.rev}`;
    media.appendChild(video);
  } else {
    const waiting = document.createElement("p");
    waiting.className = "meta";
    waiting.textContent = "Rendering…";
    media.appendChild(waiting);
  }

  const body = document.createElement("div");
  const heading = document.createElement("p");
  heading.innerHTML = `<span class="score">${clip.score ?? ""}</span> ${escapeHtml(clip.title || "Clip")}`;
  const reason = document.createElement("p");
  reason.className = "reason";
  reason.textContent = clip.reason || "";
  const edit = document.createElement("form");
  edit.className = "edit";
  edit.innerHTML = `
    <label>Title <input name="title" type="text" value="${escapeAttr(clip.title || "")}" /></label>
    <label>Hook <input name="hook" type="text" value="${escapeAttr(clip.hook || "")}" /></label>
    <div class="times">
      <label>Start <input name="start" type="number" step="0.1" value="${clip.start}" /></label>
      <label>End <input name="end" type="number" step="0.1" value="${clip.end}" /></label>
    </div>
  `;
  const row = document.createElement("div");
  row.className = "times";
  const rerender = document.createElement("button");
  rerender.type = "submit";
  rerender.className = "secondary";
  rerender.textContent = "Re-render";
  edit.appendChild(rerender);
  edit.addEventListener("submit", async (event) => {
    event.preventDefault();
    rerender.disabled = true;
    const payload = {
      title: edit.title.value,
      hook: edit.hook.value,
      start: Number(edit.start.value),
      end: Number(edit.end.value),
    };
    const response = await fetch(`/api/jobs/${job.id}/clips/${clip.index}/rerender`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      alert(data.detail || "Could not re-render");
    }
    rerender.disabled = false;
  });
  if (clip.ready) {
    const link = document.createElement("a");
    link.className = "download";
    link.href = `/api/jobs/${job.id}/clips/${clip.index}/file?download=1&rev=${clip.rev}`;
    link.textContent = "Download";
    row.appendChild(link);
  }
  edit.appendChild(row);
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = `${clock(clip.start)} – ${clock(clip.end)}`;
  body.append(heading, meta, reason, edit);
  if (job.status === "completed" && clip.ready) {
    const captionLabel = document.createElement("label");
    captionLabel.textContent = "TikTok caption";
    const area = document.createElement("textarea");
    area.name = "caption";
    area.value = clip.caption || "";
    captionLabel.append(area);
    body.append(captionLabel);
    if (clip.post_status) {
      const posted = document.createElement("p");
      posted.className = "meta";
      posted.textContent = clip.post_status === "posted"
        ? "Sent to TikTok"
        : clip.post_status === "failed"
          ? (clip.post_error || "TikTok rejected this clip")
          : "Uploading to TikTok…";
      body.append(posted);
    }
  }
  wrap.append(media, body);
  return wrap;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}

function renderJob(job) {
  let card = jobsEl.querySelector(`[data-id="${job.id}"]`);
  if (!card) {
    card = document.createElement("article");
    card.className = "job";
    card.dataset.id = job.id;
    card.innerHTML = `
      <header><h3></h3><span class="meta status"></span></header>
      <div class="bar"><span></span></div>
      <p class="meta stage"></p>
      <pre class="logs"></pre>
      <div class="tiktok" hidden></div>
      <div class="clips"></div>
    `;
    jobsEl.prepend(card);
  }
  const title = job.title || job.source_url || "Uploaded video";
  card.querySelector("h3").textContent = title;
  const platform = job.platform ? ` · ${job.platform}` : "";
  const live = job.was_live ? " · live" : "";
  card.querySelector(".status").textContent = `${job.status}${platform}${live}`;
  card.querySelector(".bar span").style.width = `${job.progress || 0}%`;
  card.querySelector(".stage").textContent = job.error || job.message || "";
  card.querySelector(".logs").textContent = (job.logs || []).slice(-12).join("\n");
  const sign = signature(job);
  if (signatures.get(job.id) !== sign) {
    signatures.set(job.id, sign);
    const clips = card.querySelector(".clips");
    clips.replaceChildren(...(job.clips || []).map((clip) => clipCard(job, clip)));
  }
  renderTikTok(card, job);
}

function tiktokMode(job) {
  const ready = (job.clips || []).filter((clip) => clip.ready);
  if (job.status !== "completed" || !ready.length) return "hidden";
  const state = (job.tiktok && job.tiktok.state) || "";
  if (state === "dismissed") return "again";
  if (state === "uploading") return "uploading";
  if (state === "done") return "done";
  if (state === "failed") return "failed";
  if (state === "writing" || ready.some((clip) => !clip.caption)) return "writing";
  return "review";
}

function renderTikTok(card, job) {
  const box = card.querySelector(".tiktok");
  const mode = tiktokMode(job);
  if (mode === "writing" && (job.tiktok || {}).state !== "writing" && !captionAsks.has(job.id)) {
    captionAsks.add(job.id);
    fetch(`/api/jobs/${job.id}/captions`, { method: "POST" }).then(() => refresh());
  }
  if (box.dataset.mode === mode && mode !== "uploading" && mode !== "failed" && mode !== "done") return;
  box.dataset.mode = mode;
  box.hidden = mode === "hidden";
  const count = (job.clips || []).filter((clip) => clip.ready).length;
  const note = (job.tiktok && (job.tiktok.error || job.tiktok.account)) || "";
  if (mode === "hidden") {
    box.replaceChildren();
    return;
  }
  const text = document.createElement("p");
  const actions = document.createElement("div");
  actions.className = "actions";
  if (mode === "writing") {
    text.textContent = "Writing the TikTok title and hashtags. Subtitles are already burned into each clip.";
  } else if (mode === "uploading") {
    text.textContent = job.message || "Uploading to TikTok. The clip audio is used as-is, with no added music.";
  } else if (mode === "done") {
    text.textContent = job.message || "Uploaded. TikTok got the clip audio only.";
  } else if (mode === "again") {
    text.textContent = "These clips were not sent to TikTok.";
    const again = document.createElement("button");
    again.type = "button";
    again.textContent = "Review for TikTok";
    again.addEventListener("click", () => fetch(`/api/jobs/${job.id}/captions`, { method: "POST" }).then(() => refresh()));
    actions.append(again);
  } else {
    text.textContent = mode === "failed"
      ? (note || "TikTok did not take every clip.")
      : `Upload all ${count} clips to TikTok? Subtitles are in the video. No TikTok music is added, so only the clip audio plays.`;
    const upload = document.createElement("button");
    upload.type = "button";
    upload.textContent = mode === "failed" ? "Try the failed clips again" : "Upload all";
    upload.addEventListener("click", () => sendTikTok(job.id, card, upload));
    actions.append(upload);
    if (mode === "review") {
      const skip = document.createElement("button");
      skip.type = "button";
      skip.className = "secondary";
      skip.textContent = "Don't upload";
      skip.addEventListener("click", () => {
        fetch(`/api/jobs/${job.id}/tiktok/dismiss`, { method: "POST" }).then(() => refresh());
      });
      actions.append(skip);
    }
  }
  box.replaceChildren(text, actions);
}

async function sendTikTok(jobId, card, button) {
  if (!unipostReady) {
    alert("Save your UniPost API key first.");
    return;
  }
  button.disabled = true;
  const clips = [...card.querySelectorAll(".clip")].map((node) => ({
    index: Number(node.dataset.index),
    caption: node.querySelector("[name=caption]")?.value || "",
  }));
  const response = await fetch(`/api/jobs/${jobId}/tiktok`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: true, clips }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    alert(data.detail || "Could not start the upload");
    button.disabled = false;
  }
  await refresh();
}

async function refresh() {
  const response = await fetch("/api/jobs");
  if (!response.ok) return;
  const data = await response.json();
  const jobs = data.jobs || [];
  if (!jobs.length) {
    if (!jobsEl.childElementCount) {
      jobsEl.innerHTML = `<p class="empty">No jobs yet. Paste a link to start.</p>`;
    }
    return;
  }
  const empty = jobsEl.querySelector(".empty");
  if (empty) empty.remove();
  const ids = new Set(jobs.map((job) => job.id));
  jobs.forEach(renderJob);
  jobsEl.querySelectorAll(".job").forEach((node) => {
    if (!ids.has(node.dataset.id)) node.remove();
  });
}

async function health() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (data.ok) {
      healthEl.className = "health good";
      healthEl.textContent = `Model ready: ${data.model}. Speech model stays on CPU so the GTX 1060 can run Qwen.`;
      submitBtn.disabled = false;
    } else {
      healthEl.className = "health bad";
      healthEl.textContent = data.detail || "Ollama is not ready.";
      submitBtn.disabled = true;
    }
  } catch {
    healthEl.className = "health bad";
    healthEl.textContent = "Clipmaster API is not responding.";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  submitBtn.disabled = true;
  try {
    const file = form.file.files[0];
    let uploadId = "";
    if (file) {
      const body = new FormData();
      body.append("file", file);
      const uploaded = await fetch("/api/upload", { method: "POST", body });
      const uploadData = await uploaded.json();
      if (!uploaded.ok) throw new Error(uploadData.detail || "Upload failed");
      uploadId = uploadData.upload_id;
    }
    const url = form.url.value.trim();
    if (!url && !uploadId) throw new Error("Paste a link or choose a file.");
    const payload = {
      url: uploadId ? "" : url,
      upload_id: uploadId,
      minutes: Number(form.minutes.value),
      clips: Number(form.clips.value),
      min_sec: Number(form.min_sec.value),
      max_sec: Number(form.max_sec.value),
      live_from_start: form.live_from_start.checked,
    };
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not start the job");
    form.file.value = "";
    await refresh();
  } catch (error) {
    formError.hidden = false;
    formError.textContent = error.message;
  } finally {
    submitBtn.disabled = false;
  }
});

async function loadSettings() {
  const account = document.querySelector("#tiktok-account");
  try {
    const response = await fetch("/api/settings");
    const data = await response.json();
    unipostReady = Boolean(data.unipost);
    if (data.unipost) {
      account.textContent = `UniPost key saved (${data.hint}). TikTok posts wait for your approval. Subtitles stay burned in, and no TikTok music is added.`;
    }
  } catch {
    account.textContent = "Could not read the saved UniPost key.";
  }
}

document.querySelector("#save-key").addEventListener("click", async () => {
  const account = document.querySelector("#tiktok-account");
  const key = form.unipost.value.trim();
  if (!key) {
    account.textContent = "Paste the API key from app.unipost.dev.";
    return;
  }
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ unipost_api_key: key }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    account.textContent = data.detail || "Could not save the key.";
    return;
  }
  form.unipost.value = "";
  unipostReady = true;
  account.textContent = data.warning
    ? `Key saved (${data.hint}). ${data.warning}`
    : `Key saved (${data.hint}). Connected TikTok: ${data.account || "account found"}.`;
});

health();
loadSettings();
refresh();
setInterval(() => { health(); refresh(); }, 2000);
