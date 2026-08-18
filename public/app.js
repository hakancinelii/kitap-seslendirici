"use strict";

const state = {
  book: null,
  segments: [],
  doneBySegId: {},
  reference: null,
  currentIndex: -1,
  playing: false,
};

const audio = new Audio();
audio.preload = "auto";

const genInFlight = {};
const $ = (id) => document.getElementById(id);

const SILENT_WAV =
  "data:audio/wav;base64,UklGRmQGAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YUAGAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

function unlockAudio() {
  const prev = audio.src;
  audio.muted = true;
  audio.src = SILENT_WAV;
  audio.play().then(() => { audio.pause(); audio.currentTime = 0; }).catch(() => {})
    .finally(() => { audio.muted = false; audio.src = prev || ""; });
}

async function fetchJSON(url, options) {
  const r = await fetch(url, options);
  if (!r.ok) throw new Error(r.status + " " + r.statusText);
  return r.json();
}

function controlValue() {
  const v = $("controlInput").value.trim();
  localStorage.setItem("control", v);
  return v;
}

async function loadBook() {
  const data = await fetchJSON("/book.json");
  state.book = data;
  state.segments = data.segments;
  renderBook();
  $("bookTitle").textContent = data.title;
  $("controlInput").value = localStorage.getItem("control") || "";
  $("controlInput").addEventListener("change", controlValue);
  await refreshStatus();
  setInterval(refreshStatus, 20000);
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
      p.addEventListener("click", () => { unlockAudio(); playFrom(paraStartIndex(item.id)); });
      bookEl.appendChild(p);
    }
  }
}

function paraStartIndex(paraId) {
  const i = state.segments.findIndex((s) => s.para_id === paraId);
  return i >= 0 ? i : 0;
}

async function refreshStatus() {
  try {
    const data = await fetchJSON("/api/status");
    state.reference = data.reference || null;
    if (data.done) {
      for (const d of data.done) {
        if (d.seg_id && d.url && !state.doneBySegId[d.seg_id]) state.doneBySegId[d.seg_id] = d.url;
      }
    }
    const total = state.segments.length;
    const doneCount = Object.keys(state.doneBySegId).length;
    $("progressText").textContent = doneCount + " / " + total;
    updateDoneClasses();
  } catch (e) { /* ignore */ }
}

function updateDoneClasses() {
  document.querySelectorAll(".book p").forEach((p) => {
    const item = state.book.items.find((i) => i.id === p.dataset.paraId);
    if (item && item.segment_ids && item.segment_ids.every((id) => state.doneBySegId[id])) {
      p.classList.add("done");
    } else {
      p.classList.remove("done");
    }
  });
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function ensureSeg(index) {
  if (index < 0 || index >= state.segments.length) return Promise.resolve(null);
  const seg = state.segments[index];
  if (state.doneBySegId[seg.id]) return Promise.resolve(state.doneBySegId[seg.id]);
  if (genInFlight[seg.id]) return genInFlight[seg.id];

  const MAX_TRY = 4;
  genInFlight[seg.id] = (async () => {
    let lastErr = null;
    for (let a = 0; a < MAX_TRY; a++) {
      $("playerStatus").textContent = "Ses üretiliyor… (" + (index + 1) + "/" + state.segments.length +
        (a > 0 ? ", deneme " + (a + 1) : "") + ")";
      try {
        const r = await fetchJSON("/api/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ seg_id: seg.id, text: seg.text, control: controlValue() }),
        });
        if (r && r.url) {
          state.doneBySegId[seg.id] = r.url;
          return r.url;
        }
        throw new Error("URL yok");
      } catch (e) {
        lastErr = e;
        await sleep(2500 * (a + 1));
      }
    }
    throw lastErr || new Error("üretim başarısız");
  })().finally(() => { delete genInFlight[seg.id]; });

  return genInFlight[seg.id];
}

function playAudio(url) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; audio.onended = null; audio.onerror = null; resolve(); } };
    audio.onended = finish;
    audio.onerror = finish;
    audio.src = url;
    $("playerStatus").textContent = "Okuyor";
    audio.play().catch(() => finish());
  });
}

function highlight() {
  document.querySelectorAll(".book p").forEach((p) => p.classList.remove("active"));
  const seg = state.segments[state.currentIndex];
  if (!seg) return;
  const el = document.querySelector(`.book p[data-para-id="${seg.para_id}"]`);
  if (el) {
    el.classList.add("active");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

async function playFrom(start) {
  stopPlayback();
  state.playing = true;
  state.currentIndex = start;
  $("playBtn").textContent = "▶ Oynatılıyor";

  while (state.playing && state.currentIndex < state.segments.length) {
    const seg = state.segments[state.currentIndex];
    $("playerText").textContent = seg.text;
    highlight();
    const next = state.currentIndex + 1;
    if (next < state.segments.length) ensureSeg(next); // lookahead
    try {
      const url = await ensureSeg(state.currentIndex);
      if (!state.playing) break;
      await playAudio(url);
    } catch (e) {
      $("playerStatus").textContent = "Hata: " + e.message;
      stopPlayback();
      return;
    }
    state.currentIndex++;
    updateDoneClasses();
  }
  if (state.playing) stopPlayback(true);
}

function stopPlayback(finished) {
  state.playing = false;
  audio.pause();
  audio.onended = null;
  audio.onerror = null;
  $("playBtn").textContent = "▶ Oynat";
  $("playerStatus").textContent = finished ? "Bitti" : "Durduruldu";
}

$("playBtn").addEventListener("click", () => {
  if (state.playing) return;
  unlockAudio();
  const idx = state.currentIndex >= 0 ? state.currentIndex : 0;
  playFrom(idx);
});

$("stopBtn").addEventListener("click", () => stopPlayback(false));

$("refInput").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/reference", { method: "POST", body: fd });
  if (r.ok) {
    const j = await r.json();
    state.reference = { url: j.url };
    $("refLabel").textContent = "🎤 " + file.name;
    $("refInput").parentElement.classList.add("ready");
  }
});

loadBook();
