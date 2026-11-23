// UI actions: voice names, moves, merges, re-rendering, playback.
// Every request carries X-MA: the server rejects it otherwise (protection against other pages in the browser).

const STRINGS = JSON.parse(document.getElementById("strings").textContent);

function tr(key, values = {}) {
  return STRINGS[key].replace(/\{(\w+)\}/g, (_, name) => values[name]);
}

function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3000);
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-MA": "1" },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

function busy(form, on) {
  form.querySelectorAll("button").forEach((b) => (b.disabled = on));
}

function rerendered(d) {
  const skipped = d.skipped && d.skipped.length ? tr("js_skipped", { n: d.skipped.length }) : "";
  return tr("js_rerendered", { n: d.rerendered }) + skipped;
}

async function act(form, fn) {
  busy(form, true);
  try {
    await fn();
  } catch (e) {
    toast(tr("js_failed", { error: e.message }));
    busy(form, false);
  }
}

document.addEventListener("submit", (ev) => {
  const f = ev.target;
  ev.preventDefault();
  if (f.classList.contains("name-form")) {
    act(f, async () => {
      const d = await post(`/api/voices/${f.dataset.voice}/name`, { name: f.elements["name"].value });
      toast(tr("js_name_saved", { rest: rerendered(d) }));
      setTimeout(() => location.reload(), 800);
    });
  } else if (f.classList.contains("reassign-form")) {
    const v = f.elements["voice_id"].value;
    if (!v) return toast(tr("js_pick_voice"));
    act(f, async () => {
      await post(`/api/r/${f.dataset.rid}/reassign`, { cluster: f.dataset.cluster, voice_id: v === "new" ? null : Number(v) });
      toast(tr("js_moved"));
      setTimeout(() => location.reload(), 800);
    });
  } else if (f.classList.contains("merge-form")) {
    const into = f.elements["into"].value;
    if (!into) return toast(tr("js_pick"));
    act(f, async () => {
      const d = await post(`/api/voices/merge`, { into: Number(into), other: Number(f.dataset.other) });
      toast(tr("js_merged", { rest: rerendered(d) }));
      setTimeout(() => location.reload(), 800);
    });
  } else if (f.classList.contains("process-form")) {
    act(f, async () => {
      await post(`/api/r/${f.dataset.rid}/process`, { from: f.elements["from"].value });
      toast(tr("js_started"));
      busy(f, false);
    });
  }
});

let audio = null;
let playing = null;
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.classList.contains("act")) {
    act(b.parentElement, async () => {
      await post(b.dataset.url);
      toast(tr("js_notes"));
      setTimeout(() => location.reload(), 800);
    });
  } else if (b.classList.contains("danger-act")) {
    if (!confirm(b.dataset.confirm)) return;
    act(b.parentElement, async () => {
      await post(b.dataset.url);
      toast(tr("js_deleted"));
      setTimeout(() => (b.dataset.next ? location.assign(b.dataset.next) : location.reload()), 800);
    });
  } else if (b.classList.contains("play")) {
    if (audio) audio.pause();
    if (playing) playing.classList.remove("playing");
    if (playing === b) { playing = null; return; }
    audio = new Audio(b.dataset.src);
    playing = b;
    b.classList.add("playing");
    audio.onended = () => { b.classList.remove("playing"); playing = null; };
    audio.onerror = () => { b.classList.remove("playing"); playing = null; toast(tr("js_no_audio")); };
    audio.play();
  }
});
