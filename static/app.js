"use strict";

const state = {
  book: null,
  segments: [],
  segIndexById: {},
  paraBySegId: {},
  statuses: {},
  currentSegIndex: -1,
  playing: false,
  waiting: false,
  reference: false,
};

let pollTimer = null;

const audio = new Audio();
audio.preload = "auto";

const SILENT_WAV =
  "data:audio/wav;base64,UklGRmQGAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YUAGAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

function unlockAudio() {
  const prev = audio.src;
  audio.muted = true;
  audio.src = SILENT_WAV;
  audio.play()
    .then(() => {
      audio.pause();
      audio.currentTime = 0;
    })
    .catch(() => {})
    .finally(() => {
      audio.muted = false;
      audio.src = prev || "";
    });
}

const $ = (id) => document.getElementById(id);

function fetchJSON(url, options) {
  return fetch(url, options).then((r) => {
    if (!r.ok) throw new Error(r.status + " " + r.statusText);
    return r.json();
  });
}

async function loadBook() {
  const data = await fetchJSON("/api/book");
  state.book = data;
  state.segments = data.segments;
  data.segments.forEach((s, i) => {
    state.segIndexById[s.id] = i;
    state.paraBySegId[s.id] = s.para_id;
  });
  renderBook();
  $("bookTitle").textContent = data.title;
  const settings = await fetchJSON("/api/settings");
  state.reference = settings.reference;
  if (settings.reference) {
    $("refLabel").textContent = "🎤 " + settings.reference_name;
    $("refInput").parentElement.classList.add("ready");
  }
  $("controlInput").value = settings.control || "";
  $("controlInput").addEventListener("change", () => {
    fetchJSON("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ control: $("controlInput").value }),
    });
  });
  startPolling();
}

function renderBook() {
  const bookEl = $("book");
  bookEl.innerHTML = "";
  for (const item of state.book.items) {
    if (item.type === "heading") {
      const tag = item.level === 1 ? "h1" : item.level === 2 ? "h2" : "h3";
      const el = document.createElement(tag);
      el.textContent = item.text;
      bookEl.appendChild(el);
    } else if (item.type === "paragraph") {
      const p = document.createElement("p");
      p.textContent = item.text;
      p.dataset.paraId = item.id;
      p.addEventListener("click", () => startFromParagraph(item.id));
      bookEl.appendChild(p);
    }
  }
}

function startFromParagraph(paraId) {
  const first = state.segments.findIndex((s) => s.para_id === paraId);
  if (first >= 0) {
    unlockAudio();
    startPlaying(first);
  }
}

function enqueueFrom(index) {
  return fetchJSON("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start: index }),
  });
}

function ensureQueued(index) {
  if (index < 0 || index >= state.segments.length) return;
  if (statusOf(state.segments[index].id) === "idle") {
    enqueueFrom(index);
  }
}

async function startPlaying(fromIndex) {
  if (!state.reference) {
    console.warn("Referans ses yüklenmedi; varsayılan sesle okunacak.");
  }
  await enqueueFrom(fromIndex);
  state.currentSegIndex = fromIndex;
  state.playing = true;
  state.waiting = false;
  $("playBtn").textContent = "⏸ Hazırlanıyor";
  $("playerStatus").textContent = "Hazırlanıyor…";
  audio.pause();
  audio.currentTime = 0;
  highlight();
  await waitForPrebuffer();
  playNext();
}

async function waitForPrebuffer() {
  return new Promise((resolve) => {
    const check = () => {
      if (!state.playing) return resolve();
      const n = state.segments.length;
      const i = state.currentSegIndex;
      if (i >= n) return resolve();
      const cur = statusOf(state.segments[i].id);
      if (cur === "error") return resolve();
      if (cur === "idle") {
        enqueueFrom(i);
      }
      if (cur !== "done") {
        setTimeout(check, 400);
        return;
      }
      const nxt = i + 1 >= n ? "done" : statusOf(state.segments[i + 1].id);
      if (nxt === "done" || nxt === "error") resolve();
      else setTimeout(check, 400);
    };
    check();
  });
}

function playNext() {
  if (!state.playing) return;
  if (state.currentSegIndex >= state.segments.length) {
    stopPlaying();
    return;
  }
  const seg = state.segments[state.currentSegIndex];
  highlight();
  $("playerText").textContent = seg.text;

  const st = statusOf(seg.id);
  if (st === "error") {
    const err = (state.statuses[seg.id] || {}).error || "";
    $("playerStatus").textContent = "Hata: " + err;
    stopPlaying();
    return;
  }
  if (st === "done") {
    state.waiting = false;
    $("playerStatus").textContent = "Okuyor";
    $("playBtn").textContent = "▶ Oynatılıyor";
    audio.src = "/api/audio/" + seg.id;
    audio.play().catch(() => {});
  } else {
    state.waiting = true;
    $("playerStatus").textContent = "Ses üretiliyor…";
    $("playBtn").textContent = "⏳ Ses üretiliyor";
    ensureQueued(state.currentSegIndex);
  }
}

audio.addEventListener("ended", () => {
  if (!state.playing) return;
  state.currentSegIndex++;
  playNext();
});

audio.addEventListener("error", () => {
  if (!state.playing) return;
  state.waiting = true;
  $("playerStatus").textContent = "Ses bekleniyor…";
});

function stopPlaying() {
  state.playing = false;
  state.waiting = false;
  audio.pause();
  $("playBtn").textContent = "▶ Oynat";
  $("playerStatus").textContent = "Durduruldu";
}

function statusOf(segId) {
  const s = state.statuses[segId];
  return s ? s.state : "idle";
}

function highlight() {
  document.querySelectorAll(".book p").forEach((p) => p.classList.remove("active"));
  if (state.currentSegIndex >= 0 && state.currentSegIndex < state.segments.length) {
    const seg = state.segments[state.currentSegIndex];
    const el = document.querySelector(`.book p[data-para-id="${seg.para_id}"]`);
    if (el) {
      el.classList.add("active");
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }
}

function startPolling() {
  pollTimer = setInterval(refreshStatus, 1500);
  refreshStatus();
}

async function refreshStatus() {
  try {
    const data = await fetchJSON("/api/status");
    state.statuses = data.segments;
    state.reference = data.reference;
    const done = data.done || 0;
    $("progressText").textContent = done + " / " + data.total;

    document.querySelectorAll(".book p").forEach((p) => {
      const ids = (state.book.items.find((i) => i.id === p.dataset.paraId) || {}).segment_ids;
      if (ids && ids.every((id) => statusOf(id) === "done")) p.classList.add("done");
      else p.classList.remove("done");
    });

    if (state.waiting && state.currentSegIndex >= 0 && state.currentSegIndex < state.segments.length) {
      const seg = state.segments[state.currentSegIndex];
      const st = statusOf(seg.id);
      if (st === "done") {
        state.waiting = false;
        playNext();
      } else if (st === "idle") {
        enqueueFrom(state.currentSegIndex);
      }
    }
  } catch (e) {
    /* ignore transient errors */
  }
}

$("playBtn").addEventListener("click", () => {
  if (state.playing) return;
  unlockAudio();
  const idx = state.currentSegIndex >= 0 ? state.currentSegIndex : 0;
  startPlaying(idx);
});

$("stopBtn").addEventListener("click", stopPlaying);

$("refInput").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/reference", { method: "POST", body: fd });
  if (r.ok) {
    const j = await r.json();
    state.reference = true;
    $("refLabel").textContent = "🎤 " + j.name;
    $("refInput").parentElement.classList.add("ready");
  }
});

loadBook();
