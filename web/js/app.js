/* Frameza frontend — hash-routed views over the FastAPI backend.
 * Screens: home (submit), run (live progress), results, edit (per-clip),
 * queue (batch), youtube (upload center), settings, history.
 * All copy is plain language; server sentences are shown verbatim. */

import { api, uploadFile, watchRun } from "./api.js";
import { barsSVG, sparklineSVG } from "./charts.js";
import { createDotMatrix, miniScore } from "./dots.js";
import { clipVideo, confirmDialog, copyToClipboard, el, field, fmtBytes, fmtClock, isKept,
  toast, toggle } from "./ui.js";
import {
  pickFont, pickMusic, pickPosition, pickPreset, pickProfile, pickShape,
  pickSubsMode,
} from "./pickers.js";
import { mountUploadQueue } from "./upload_queue.js";
import { mountLibrary } from "./library.js";
import { mountPublishTiming } from "./publish_timing_panel.js";
import { mountPresets } from "./presets_panel.js";
import { mountChannels } from "./channels_panel.js";
import { mountPostQueue } from "./post_queue_panel.js";

const view = document.getElementById("view");
let cleanup = null; // per-view teardown (websockets, dot-matrix rafs, timers)

// -------------------------------------------------------------- router ----

const routes = {
  "": renderHome,
  run: renderRun,
  activity: renderActivity,
  results: renderResults,
  edit: renderEditor,
  queue: renderQueue,
  library: renderLibrary,
  youtube: renderYouTube,
  analytics: renderAnalytics,
  presets: renderPresets,
  channels: renderChannels,
  settings: renderSettings,
  history: renderHistory,
  workspaces: renderWorkspaceSelect,
};

const WORKSPACE_KEY = "frameza_workspace";
const LEGACY_WORKSPACE_KEY = "clipforge_workspace";
const WORKSPACE_NAME_KEY = "frameza_workspace_name";

function navigate() {
  const [, name = "", ...rest] = location.hash.split("/");
  // auto-default to 'default' workspace so user directly lands on home
  if (!localStorage.getItem(WORKSPACE_KEY)) {
    const legacy = localStorage.getItem("clipforge_workspace");
    if (legacy) {
      localStorage.setItem(WORKSPACE_KEY, legacy);
      localStorage.setItem(WORKSPACE_NAME_KEY, localStorage.getItem("clipforge_workspace_name") || "Default");
    } else {
      localStorage.setItem(WORKSPACE_KEY, "default");
      localStorage.setItem(WORKSPACE_NAME_KEY, "Default");
    }
  }
  if (cleanup) { cleanup(); cleanup = null; }
  document.querySelectorAll(".topbar nav a").forEach((a) => {
    a.toggleAttribute("aria-current",
      a.getAttribute("href") === `#/${name}` || (!name && a.hash === "#/"));
  });
  updateWorkspaceBadge();
  view.replaceChildren();
  (routes[name] || renderHome)(...rest.map(decodeURIComponent));
}
window.addEventListener("hashchange", navigate);

function updateWorkspaceBadge() {
  const badge = document.getElementById("workspace-badge");
  if (!badge) return;
  const name = localStorage.getItem(WORKSPACE_NAME_KEY);
  badge.textContent = name || "";
  badge.style.display = name ? "" : "none";
}

function backButton(defaultHash = "#/", label = "← Back") {
  const btn = el("button", {
    class: "btn btn-ghost btn-sm",
    type: "button",
    style: "display:inline-flex;align-items:center;gap:6px;cursor:pointer;",
  }, label);
  btn.addEventListener("click", () => {
    if (window.history.length > 1) {
      window.history.back();
    } else {
      location.hash = defaultHash;
    }
  });
  return btn;
}

// --------------------------------------------------------- workspaces -----

function renderWorkspaceSelect() {
  const list = el("div", { class: "activity-list" });
  const nameIn = el("input", { class: "input", type: "text",
                               placeholder: "e.g. My Cooking Channel" });
  const createBtn = el("button", { class: "btn btn-primary", type: "button" },
    "Create");

  const choose = (id, name) => {
    localStorage.setItem(WORKSPACE_KEY, id);
    localStorage.setItem(WORKSPACE_NAME_KEY, name);
    location.hash = "#/";
  };

  const row = (w) => {
    const open = el("a", { class: "activity-row", href: "#" },
      el("div", { class: "activity-meta" },
        el("div", { class: "activity-title" }, w.name)));
    open.addEventListener("click", (e) => { e.preventDefault(); choose(w.id, w.name); });
    if (w.id === "default") return open;
    const delBtn = el("button", { class: "btn btn-danger btn-sm", type: "button" },
      "Delete");
    delBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      const ok = await confirmDialog({
        title: `Delete "${w.name}"?`,
        body: "This permanently deletes every video and clip in this "
          + "workspace, and disconnects its YouTube channel. This can't be undone.",
      });
      if (!ok) return;
      delBtn.disabled = true;
      try {
        await api.del(`/api/workspaces/${encodeURIComponent(w.id)}`);
        if (localStorage.getItem(WORKSPACE_KEY) === w.id) {
          localStorage.removeItem(WORKSPACE_KEY);
          localStorage.removeItem(WORKSPACE_NAME_KEY);
        }
        load();
      } catch (err) {
        toast(err.message, "is-error");
        delBtn.disabled = false;
      }
    });
    return el("div", { class: "activity-row",
                       style: "display:flex;align-items:center;gap:12px" },
      el("div", { style: "flex:1" }, open), delBtn);
  };

  const load = async () => {
    try {
      const { workspaces } = await api.get("/api/workspaces");
      list.replaceChildren(...workspaces.map(row));
    } catch (e) {
      list.replaceChildren(el("p", { class: "t-dim", style: "margin:0" }, e.message));
    }
  };

  createBtn.addEventListener("click", async () => {
    const name = nameIn.value.trim();
    if (!name) { toast("Give it a name first.", "is-error"); return; }
    createBtn.disabled = true;
    try {
      const w = await api.post("/api/workspaces", { name });
      choose(w.id, w.name);
    } catch (e) {
      toast(e.message, "is-error");
      createBtn.disabled = false;
    }
  });

  view.append(el("section", { class: "screen" },
    el("div", { style: "margin-bottom:12px;" }, backButton("#/")),
    el("div", { class: "hero" },
      el("div", { class: "t-label" }, "Workspaces"),
      el("h1", { class: "t-display" }, "Which channel are you working on?"),
      el("p", { class: "hero-sub" },
        "Each workspace keeps its own separate videos and clips — nothing ",
        "from one ever shows up in another."),
      el("div", { class: "card card-flat" }, list),
      el("div", { class: "field-inline" }, nameIn, createBtn))));

  load();
}

/* A picker-backed form row: button shows the current choice. */
function pickerRow(label, initialText, open) {
  const btn = el("button", { class: "btn picker-btn", type: "button" },
    initialText);
  btn.addEventListener("click", open);
  return { node: field(label, btn), btn };
}

// ---------------------------------------------------------------- home ----

function renderHome() {
  let filePath = "";

  // per-run options (empty string = leave the app's defaults untouched)
  const o = {
    count: 0, aspect: "9:16", quality: "1080p", faceTracking: true,
    captions: true, metadata: true, preset: "", font: "", music: "",
    captionLanguage: "hinglish", captionPosition: "bottom",
    musicVol: -18, cta: "", highlight: "", pacing: "", clipMin: "30",
    clipMax: "60", subsMode: "", profile: "", wmMode: "text", wmText: "",
    wmImage: "", wmImageName: "", wmPos: "bottom-right",
  };

  // ---- dashboard stats bar ----
  const statsRow = el("div", { class: "dashboard-stats" });
  const sysPill = el("div", { class: "sys-pill", style: "display:none" });

  const loadStats = async () => {
    try {
      const s = await api.get("/api/dashboard/stats");
      statsRow.replaceChildren(
        el("div", { class: "stat-card" },
          el("span", { class: "stat-label" }, "Videos Processed"),
          el("span", { class: "stat-val" }, String(s.total_videos)),
          el("span", { class: "stat-sub" }, `${s.completed_jobs} successful`)),
        el("div", { class: "stat-card" },
          el("span", { class: "stat-label" }, "Clips Generated"),
          el("span", { class: "stat-val" }, String(s.total_clips)),
          el("span", { class: "stat-sub" }, `${s.kept_clips} kept`)),
        el("div", { class: "stat-card" },
          el("span", { class: "stat-label" }, "Active Jobs"),
          el("span", { class: "stat-val", style: s.active_jobs > 0 ? "color:var(--ok)" : "" }, String(s.active_jobs)),
          el("span", { class: "stat-sub" }, s.active_jobs > 0 ? "Processing now" : "Idle")),
        el("div", { class: "stat-card" },
          el("span", { class: "stat-label" }, "Disk Storage"),
          el("span", { class: "stat-val" }, fmtBytes(s.storage_bytes)),
          el("span", { class: "stat-sub" }, "Output clips"))
      );
    } catch (_) {}
    try {
      const sys = await api.get("/api/system");
      sysPill.replaceChildren(
        el("span", { class: "sys-dot" }),
        document.createTextNode(` ${sys.gpu_name} (NVENC + CUDA) · Ollama ${sys.ollama_model || 'llama3.2:3b'}`)
      );
      sysPill.style.display = "inline-flex";
    } catch (_) {}
  };
  loadStats();

  // ---- video ingestion (URL + Preview & File Upload) ----
  const urlInput = el("input", {
    class: "input input-hero", type: "text",
    placeholder: "Paste a YouTube or video link",
    "aria-label": "Video link",
  });
  const ytPreviewWrap = el("div");

  let inspectTimer = null;
  const inspectUrl = () => {
    clearTimeout(inspectTimer);
    const u = urlInput.value.trim();
    if (!u.startsWith("http")) {
      ytPreviewWrap.replaceChildren();
      return;
    }
    inspectTimer = setTimeout(async () => {
      ytPreviewWrap.replaceChildren(el("div", { class: "t-dim", style: "font-size:var(--text-xs);padding:6px 0" }, "Inspecting video…"));
      try {
        const res = await api.post("/api/youtube/inspect", { url: u });
        if (res.valid) {
          const card = el("div", { class: "yt-preview-card" },
            res.thumbnail ? el("img", { class: "yt-preview-thumb", src: res.thumbnail, alt: "thumbnail" }) : null,
            el("div", { class: "yt-preview-info" },
              el("div", { class: "yt-preview-title" }, res.title),
              el("div", { class: "yt-preview-sub" },
                res.uploader ? el("span", {}, res.uploader) : null,
                res.duration ? el("span", { class: "t-mono" }, fmtClock(res.duration)) : null)),
            el("button", {
              class: "btn btn-ghost btn-sm", type: "button",
              onclick: () => { urlInput.value = ""; ytPreviewWrap.replaceChildren(); }
            }, "✕"));
          ytPreviewWrap.replaceChildren(card);
        } else {
          ytPreviewWrap.replaceChildren(el("div", { class: "t-dim", style: "font-size:var(--text-xs);color:var(--warn);padding:6px 0" }, res.error || "Could not inspect this link."));
        }
      } catch (_) {
        ytPreviewWrap.replaceChildren();
      }
    }, 600);
  };
  urlInput.addEventListener("input", inspectUrl);
  urlInput.addEventListener("paste", () => setTimeout(inspectUrl, 50));

  const fileChipWrap = el("div");
  const browseBtn = el("button", { class: "btn", type: "button" }, "Choose a file");
  const dropHint = el("div", { class: "drop-hint" },
    "or drop a video file anywhere on this page (MP4, MKV, MOV, WebM, AVI)", browseBtn);
  const filePicker = el("input", {
    class: "visually-hidden", type: "file",
    accept: "video/*,.mp4,.mov,.mkv,.webm,.avi", "aria-hidden": "true", tabindex: "-1",
  });
  browseBtn.addEventListener("click", () => filePicker.click());
  filePicker.addEventListener("change", () => {
    if (filePicker.files[0]) takeFile(filePicker.files[0]);
  });

  // ---- format / aspect ratio ----
  const aspectSeg = segmented([
    ["9:16", "9:16 Vertical (Shorts/Reels/TikTok)"],
    ["1:1", "1:1 Square (Feed)"],
    ["16:9", "16:9 Landscape (YouTube)"],
  ], o.aspect, (v) => { o.aspect = v; });

  // ---- duration selector pills ----
  const durPills = el("div", { class: "pill-group" });
  const customDurRow = el("div", { class: "field-inline", style: "display:none;margin-top:8px" });
  const lenMin = el("input", { class: "input", type: "number", min: "5", max: "180", value: "30" });
  const lenMax = el("input", { class: "input", type: "number", min: "5", max: "180", value: "60" });
  customDurRow.append(lenMin, "to", lenMax, "seconds");
  lenMin.addEventListener("input", () => { o.clipMin = lenMin.value; });
  lenMax.addEventListener("input", () => { o.clipMax = lenMax.value; });

  const durPresets = [
    { label: "15–30s (Hook)", min: "15", max: "30" },
    { label: "30–60s (Standard)", min: "30", max: "60", active: true },
    { label: "60–90s (In-depth)", min: "60", max: "90" },
    { label: "Custom Range", custom: true },
  ];
  durPresets.forEach((p) => {
    const btn = el("button", {
      class: `pill-btn ${p.active ? "is-active" : ""}`,
      type: "button",
    }, p.label);
    btn.addEventListener("click", () => {
      durPills.querySelectorAll(".pill-btn").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      if (p.custom) {
        customDurRow.style.display = "";
        o.clipMin = lenMin.value;
        o.clipMax = lenMax.value;
      } else {
        customDurRow.style.display = "none";
        o.clipMin = p.min;
        o.clipMax = p.max;
      }
    });
    durPills.append(btn);
  });

  // ---- clip count pills ----
  const countPills = el("div", { class: "pill-group" });
  const customCountRow = el("div", { class: "field-inline", style: "display:none;margin-top:8px" });
  const clipCount = el("input", { class: "range", type: "range", min: "1", max: "15", value: "5" });
  const clipCountOut = el("span", { class: "t-mono t-dim" }, "5");
  customCountRow.append(clipCount, clipCountOut);
  clipCount.addEventListener("input", () => {
    o.count = Number(clipCount.value);
    clipCountOut.textContent = String(o.count);
  });

  const countPresets = [
    { label: "Auto (AI picks)", count: 0, active: true },
    { label: "3 Clips", count: 3 },
    { label: "5 Clips", count: 5 },
    { label: "10 Clips", count: 10 },
    { label: "Custom", custom: true },
  ];
  countPresets.forEach((p) => {
    const btn = el("button", {
      class: `pill-btn ${p.active ? "is-active" : ""}`,
      type: "button",
    }, p.label);
    btn.addEventListener("click", () => {
      countPills.querySelectorAll(".pill-btn").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      if (p.custom) {
        customCountRow.style.display = "";
        o.count = Number(clipCount.value);
      } else {
        customCountRow.style.display = "none";
        o.count = p.count;
      }
    });
    countPills.append(btn);
  });

  // ---- quality / resolution ----
  const qualitySeg = segmented([
    ["1080p", "1080p Full HD (Recommended)"],
    ["720p", "720p HD (Faster encode)"],
  ], o.quality, (v) => { o.quality = v; });

  // ---- pacing ----
  const pacingSel = el("select", { class: "select", "aria-label": "Pacing" },
    el("option", { value: "", selected: "" }, "Balanced — natural flow"),
    el("option", { value: "0.15" }, "Relaxed — keep pauses"),
    el("option", { value: "0.85" }, "Snappy — tight cuts"));
  pacingSel.addEventListener("change", () => { o.pacing = pacingSel.value; });

  // ---- feature toggles ----
  const faceToggle = toggle(true, "Face Tracking & Smart Speaker Crop");
  faceToggle.input.addEventListener("change", () => { o.faceTracking = faceToggle.input.checked; });

  const subsToggle = toggle(true, "Burn Animated Subtitles");
  const noCapsNotice = el("div", {
    class: "card card-flat",
    style: "display:none;padding:10px 14px;background:rgba(255,255,255,0.03);border:1px dashed rgba(255,255,255,0.15);border-radius:8px;font-size:var(--text-xs);color:var(--text-dim);"
  }, "⚡ Subtitles disabled — your clips will be exported as clean video without burned text.");

  const capPosSel = el("select", { class: "select", "aria-label": "Caption Position" },
    el("option", { value: "bottom", selected: "" }, "Bottom (Recommended — Reels & Shorts)"),
    el("option", { value: "center" }, "Center (Middle of screen)"),
    el("option", { value: "top" }, "Top (Upper third — Podcasts & Reactions)"));
  capPosSel.addEventListener("change", () => { o.captionPosition = capPosSel.value; });
  const capPosRow = field("Caption Position", capPosSel);

  const viralToggle = toggle(true, "AI Viral Moments Detection (Ollama)");
  const metaToggle = toggle(true, "AI Titles, Descriptions & Hashtags");
  metaToggle.input.addEventListener("change", () => { o.metadata = metaToggle.input.checked; });

  const styleToggle = toggle(true, "Polish Clip Timing & Cuts");

  // ---- caption style & font pickers ----
  const presetRow = pickerRow("Subtitle Preset", "Style's default", async () => {
    const v = await pickPreset(o.preset, o.font);
    if (v !== null) {
      o.preset = v;
      if (v === "none") {
        subsToggle.input.checked = false;
        o.captions = false;
      } else {
        subsToggle.input.checked = true;
        o.captions = true;
      }
      updateCaptionsUi();
    }
  });
  const fontRow = pickerRow("Subtitle Font", "Style's own font", async () => {
    const v = await pickFont(o.font, o.preset);
    if (v !== null) {
      o.font = v;
      fontRow.btn.textContent = v || "Style's own font";
    }
  });

  const langSeg = segmented([
    ["hinglish", "Hinglish"],
    ["hindi", "Hindi (देवनागरी)"],
    ["english", "English"]
  ], "hinglish", (v) => { o.captionLanguage = v; });
  const langSegRow = field("Caption Language", langSeg);

  function updateCaptionsUi() {
    const active = o.captions && o.preset !== "none";
    langSegRow.style.display = active ? "" : "none";
    fontRow.node.style.display = active ? "" : "none";
    capPosRow.style.display = active ? "" : "none";
    noCapsNotice.style.display = active ? "none" : "";
    if (!active) {
      presetRow.btn.textContent = "No captions (Clean Video)";
    } else if (o.preset && o.preset !== "none") {
      presetRow.btn.textContent = o.preset.replace(/-/g, " ");
    } else {
      presetRow.btn.textContent = "Style's default";
    }
  }

  subsToggle.input.addEventListener("change", () => {
    o.captions = subsToggle.input.checked;
    if (subsToggle.input.checked && o.preset === "none") {
      o.preset = "";
    }
    updateCaptionsUi();
  });

  // ---- background music ----
  const musicToggle = toggle(false, "Add Background Music");
  const musicArea = el("div", { style: "display:none;margin-top:8px" });
  const musicRow = pickerRow("Music Track", "Surprise me (Random)", async () => {
    const v = await pickMusic(o.music);
    if (v !== null) {
      o.music = v;
      musicRow.btn.textContent =
        v === "" ? "No music" : v === "auto" ? "Pick for me"
          : v === "random" ? "Surprise me" : v;
    }
  });
  o.music = "random";
  const musicVol = el("input", {
    class: "range", type: "range", min: "-40", max: "-10", value: "-18",
    "aria-label": "Music loudness",
  });
  musicVol.addEventListener("input", () => { o.musicVol = Number(musicVol.value); });
  musicArea.append(musicRow.node, field("Music Volume", musicVol));

  musicToggle.input.addEventListener("change", () => {
    musicArea.style.display = musicToggle.input.checked ? "" : "none";
    o.music = musicToggle.input.checked ? "random" : "";
  });

  // ---- watermark & branding ----
  const ctaInput = el("input", {
    class: "input", type: "text", maxlength: "60",
    placeholder: "e.g. Follow for more",
  });
  ctaInput.addEventListener("input", () => { o.cta = ctaInput.value.trim(); });

  const wmText = el("input", { class: "input", type: "text", maxlength: "40", placeholder: "@yourhandle" });
  wmText.addEventListener("input", () => { o.wmText = wmText.value.trim(); });
  const wmLogoBtn = el("button", { class: "btn", type: "button" }, "Choose a logo");
  const wmLogoIn = el("input", {
    class: "visually-hidden", type: "file", accept: "image/png,image/webp",
    "aria-hidden": "true", tabindex: "-1",
  });
  wmLogoBtn.addEventListener("click", () => wmLogoIn.click());
  wmLogoIn.addEventListener("change", async () => {
    const f = wmLogoIn.files[0];
    if (!f) return;
    try {
      const { path } = await uploadFile("/api/uploads/logo", f);
      o.wmImage = path;
      o.wmImageName = f.name;
      wmLogoBtn.textContent = f.name;
    } catch (e) { toast(e.message, "is-error"); }
  });
  const wmRows = { text: field("Watermark text", wmText), image: field("Logo image", wmLogoBtn) };
  const wmPosRow = pickerRow("Watermark position", "Bottom right", async () => {
    const v = await pickPosition(o.wmPos);
    if (v !== null) {
      o.wmPos = v;
      wmPosRow.btn.textContent = v.replace("-", " ").replace(/^\w/, (c) => c.toUpperCase());
    }
  });
  const wmSeg = segmented([["off", "None"], ["text", "Text"], ["image", "Logo"]],
    o.wmMode, (v) => {
      o.wmMode = v;
      wmRows.text.style.display = v === "text" ? "" : "none";
      wmRows.image.style.display = v === "image" ? "" : "none";
      wmPosRow.node.style.display = v === "off" ? "none" : "";
    });
  wmRows.image.style.display = "none";

  const makeBtn = el("button", {
    class: "btn btn-primary btn-lg",
    style: "width:100%;padding:14px 20px;font-size:var(--text-md);font-weight:700;letter-spacing:0.02em",
    type: "button"
  }, "Make clips");
  makeBtn.addEventListener("click", start);
  urlInput.addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });

  const brandCard = el("div", { class: "studio-brand-card card" },
    el("div", { class: "studio-brand-info" },
      el("div", { class: "t-label" }, "AI Short Video Studio"),
      el("h1", { class: "t-display", style: "font-size:var(--text-xl);margin:0" }, "Turn Long Videos into Viral Shorts"),
      el("p", { class: "t-dim", style: "margin:4px 0 0;font-size:var(--text-sm)" },
        "Free, local AI video clipper. Analyzes speech, detects viral moments, reframes faces to 9:16 vertical, and generates animated captions."),
      el("div", { style: "margin-top:8px" }, sysPill)),
    el("img", {
      src: "logo.png",
      alt: "Frameza",
      class: "studio-logo-img",
    }));

  const inputCard = el("div", { class: "studio-card card" },
    el("div", { class: "studio-section-title" }, "1. Input Video"),
    el("div", { class: "source-row" }, urlInput, ytPreviewWrap, dropHint, fileChipWrap));

  const formatCard = el("div", { class: "studio-card card" },
    el("div", { class: "studio-section-title" }, "2. Output Format & Duration"),
    field("Format & Aspect Ratio", aspectSeg),
    field("Quality / Resolution", qualitySeg),
    field("Target Clip Duration", durPills, customDurRow));

  const mainCol = el("div", { class: "studio-main" },
    brandCard,
    inputCard,
    formatCard,
    el("div", { style: "margin-top:2px" }, makeBtn));

  const aiCard = el("div", { class: "studio-card card" },
    el("div", { class: "studio-section-title" }, "3. AI Features & Highlight Selection"),
    el("div", { class: "toggles-grid" },
      faceToggle.node,
      viralToggle.node,
      metaToggle.node,
      styleToggle.node),
    field("Number of Clips", countPills, customCountRow),
    field("Pacing & Flow", pacingSel));

  const stylingCard = el("div", { class: "studio-card card" },
    el("div", { class: "studio-section-title" }, "4. Captions & Call To Action"),
    subsToggle.node,
    noCapsNotice,
    capPosRow,
    langSegRow,
    presetRow.node,
    fontRow.node,
    field("Call to Action (Ending Message)", ctaInput));

  const audioBrandCard = el("div", { class: "studio-card card" },
    el("div", { class: "studio-section-title" }, "5. Audio & Watermark"),
    el("div", { class: "field" },
      el("label", {}, "Background Music"),
      musicToggle.node,
      musicArea),
    field("Watermark", wmSeg),
    wmRows.text, wmRows.image, wmPosRow.node);

  const sidebarCol = el("div", { class: "studio-sidebar" },
    aiCard,
    stylingCard,
    audioBrandCard);

  const studioGrid = el("div", { class: "studio-grid" }, mainCol, sidebarCol);

  view.append(el("section", { class: "screen" },
    statsRow,
    studioGrid,
    filePicker, wmLogoIn));

  // drag & drop over page
  const onDragOver = (e) => { e.preventDefault(); document.body.classList.add("dragging"); };
  const onDragLeave = (e) => {
    if (!e.relatedTarget) document.body.classList.remove("dragging");
  };
  const onDrop = (e) => {
    e.preventDefault();
    document.body.classList.remove("dragging");
    if (e.dataTransfer.files[0]) takeFile(e.dataTransfer.files[0]);
  };
  document.addEventListener("dragover", onDragOver);
  document.addEventListener("dragleave", onDragLeave);
  document.addEventListener("drop", onDrop);
  cleanup = () => {
    document.removeEventListener("dragover", onDragOver);
    document.removeEventListener("dragleave", onDragLeave);
    document.removeEventListener("drop", onDrop);
    document.body.classList.remove("dragging");
  };

  async function takeFile(file) {
    filePath = "";
    urlInput.value = "";
    ytPreviewWrap.replaceChildren();
    const status = el("span", { class: "file-chip" }, `Uploading ${file.name} (${fmtBytes(file.size)})… 0%`);
    fileChipWrap.replaceChildren(status);
    try {
      const { path } = await uploadFile("/api/uploads", file, (f) => {
        status.textContent = `Uploading ${file.name} (${fmtBytes(file.size)})… ${Math.round(f * 100)}%`;
      });
      filePath = path;
      fileChipWrap.replaceChildren(el("span", { class: "file-chip" },
        `\u{1F3AC} ${file.name} (${fmtBytes(file.size)})`,
        el("button", {
          class: "btn btn-ghost btn-sm", type: "button",
          "aria-label": "Remove file",
          onclick: () => { filePath = ""; fileChipWrap.replaceChildren(); },
        }, "✕")));
      toast(`Selected ${file.name}`, "is-ok");
    } catch (e) {
      fileChipWrap.replaceChildren();
      toast(e.message, "is-error");
    }
  }

  async function start() {
    const source = filePath || urlInput.value.trim();
    if (!source) {
      toast("Paste a link or choose a video file first.", "is-error");
      urlInput.focus();
      return;
    }
    makeBtn.disabled = true;
    makeBtn.textContent = "Starting pipeline…";
    try {
      const { run_id } = await api.post("/api/runs", {
        source,
        target_count: o.count || null,
        aspect: o.aspect,
        quality: o.quality,
        face_tracking: o.faceTracking,
        generate_captions: Boolean(o.captions && o.preset !== "none"),
        generate_metadata: o.metadata,
        caption_language: o.captionLanguage || "hinglish",
        caption_position: o.captionPosition || "bottom",
        preset: (o.captions && o.preset !== "none") ? (o.preset || null) : "none",
        music: o.music,
        music_volume_db: o.musicVol,
        cta_text: o.cta,
        highlight_hex: o.highlight,
        pacing: o.pacing,
        clip_min: o.clipMin,
        clip_max: o.clipMax,
        subs_mode: o.subsMode || null,
        style_profile: o.profile || null,
        watermark_mode: o.wmMode,
        watermark_text: o.wmText,
        watermark_image: o.wmMode === "image" ? o.wmImage : "",
        watermark_position: o.wmPos,
        font_family: o.font,
        style_refine: styleToggle.input.checked,
        viral: viralToggle.input.checked,
      });
      location.hash = `#/run/${encodeURIComponent(run_id)}`;
    } catch (e) {
      toast(e.message, "is-error");
      makeBtn.disabled = false;
      makeBtn.textContent = "Make clips";
    }
  }
}

/* Small segmented control (radio group styled as buttons). */
function segmented(items, current, onChange) {
  const wrap = el("div", { class: "seg", role: "radiogroup" });
  for (const [value, label] of items) {
    const b = el("button", {
      class: `seg-item ${value === current ? "is-on" : ""}`,
      type: "button", role: "radio",
      "aria-checked": value === current ? "true" : "false",
    }, label);
    b.addEventListener("click", () => {
      wrap.querySelectorAll(".seg-item").forEach((x) => {
        x.classList.remove("is-on");
        x.setAttribute("aria-checked", "false");
      });
      b.classList.add("is-on");
      b.setAttribute("aria-checked", "true");
      onChange(value);
    });
    wrap.append(b);
  }
  return wrap;
}

// ------------------------------------------------------------ progress ----

function renderRun(runId) {
  const stageTitle = el("h1", { class: "t-display", style: "margin:0;font-size:var(--text-xl)" }, "Getting ready");
  const detail = el("p", { class: "t-dim", style: "margin:4px 0 0;min-height:1.4em" }, " ");
  const pct = el("span", { class: "t-hero-num", style: "font-size:3.5rem;line-height:1" }, "0");
  const eta = el("span", { class: "t-mono badge", style: "font-size:var(--text-xs)" }, "");
  const canvas = el("canvas");
  const stageList = el("div", { class: "stage-list" });
  const logEntries = el("div", { class: "log-console", style: "flex:1;min-height:360px;max-height:480px" });
  const cancelBtn = el("button", { class: "btn btn-danger btn-sm", type: "button" }, "Stop");

  const headRow = el("div", { class: "results-head", style: "margin-bottom:var(--sp-4)" },
    el("div", {},
      el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
        backButton("#/activity", "← Back to Activity"),
        el("div", { class: "t-label", style: "margin:0;" }, "Processing Run")),
      stageTitle,
      detail),
    el("div", { class: "field-inline", style: "align-items:center;gap:12px" },
      eta,
      cancelBtn));

  const visualCard = el("div", { class: "card progress-visual-card" },
    el("div", { style: "display:flex;align-items:baseline;justify-content:center;gap:4px;margin-bottom:12px" },
      pct,
      el("span", { class: "t-title t-dim", style: "font-size:1.5rem" }, "%")),
    canvas,
    el("div", { class: "t-label", style: "margin:16px 0 8px;align-self:flex-start" }, "Pipeline Stages"),
    stageList);

  const logCard = el("div", { class: "card progress-log-card" },
    el("div", { style: "display:flex;align-items:center;justify-content:space-between;margin-bottom:8px" },
      el("span", { class: "t-label" }, "Live Execution Console"),
      el("span", { class: "sys-dot" })),
    logEntries);

  const progressGrid = el("div", { class: "progress-screen-grid" },
    visualCard,
    logCard);

  view.append(el("section", { class: "screen" },
    headRow,
    progressGrid));

  const dm = createDotMatrix(canvas, { variant: "progress" });
  dm.update({ fraction: 0, state: "running" });

  const STAGE_WEIGHTS = {
    init: 0.01,
    deps: 0.01,
    model_download: 0.05,
    model_load: 0.03,
    ingest: 0.10,
    transcribe: 0.13,
    events: 0.02,
    scenes: 0.04,
    highlights: 0.05,
    refine: 0.03,
    ai_prep: 0.04,
    render: 0.35,
    rescore: 0.04,
    cleanup: 0.01,
    done: 0.05,
  };

  let lastLoggedMsg = "";
  let latestSnap = null;
  let snapReceivedAt = 0;
  let displayedFraction = 0;
  let isTerminal = false;

  const tick = () => {
    if (!latestSnap || isTerminal) return;
    const now = performance.now();
    const dt = Math.max(0, (now - snapReceivedAt) / 1000);
    const liveElapsed = (latestSnap.elapsed || 0) + dt;
    const liveElapsedSec = Math.floor(liveElapsed);

    // 1. Dynamic Progress Percentage & Dot-Matrix Indicator
    const baseOverall = latestSnap.overall || 0;
    const stages = latestSnap.stages || [];
    const running = stages.filter((s) => s.state === "running").at(-1);

    let targetFrac = baseOverall;
    if (running) {
      const weight = STAGE_WEIGHTS[running.key] || 0.04;
      const maxFrac = Math.min(0.99, baseOverall + weight * 0.95);

      // Only time-project during later execution stages when overall_eta is reliable
      if (latestSnap.overall_eta != null && latestSnap.overall_eta > 0 && displayedFraction >= 0.25) {
        const totalEst = (latestSnap.elapsed || 0) + latestSnap.overall_eta;
        if (totalEst > 0) {
          const projected = liveElapsed / totalEst;
          targetFrac = Math.max(baseOverall, Math.min(maxFrac, projected));
        }
      }
    }

    // Smooth forward progression (never backwards)
    if (targetFrac > displayedFraction) {
      displayedFraction += (targetFrac - displayedFraction) * 0.15;
      if (Math.abs(targetFrac - displayedFraction) < 0.001) {
        displayedFraction = targetFrac;
      }
    } else if (baseOverall > displayedFraction) {
      displayedFraction = baseOverall;
    }

    const currentPct = Math.min(99, Math.round(displayedFraction * 100));
    pct.textContent = String(currentPct);
    dm.update({ fraction: displayedFraction, state: "running" });

    // 2. Dynamic Left Time & Elapsed Time
    if (cancelBtn.disabled) {
      eta.textContent = `stopping… · ${fmtClock(liveElapsedSec)} elapsed`;
    } else if (latestSnap.overall_eta != null && displayedFraction >= 0.25) {
      const liveEta = Math.max(0, Math.ceil(latestSnap.overall_eta - dt));
      if (liveEta > 0) {
        eta.textContent = `about ${fmtClock(liveEta)} left · ${fmtClock(liveElapsedSec)} elapsed`;
      } else if (currentPct >= 95) {
        eta.textContent = `almost done · ${fmtClock(liveElapsedSec)} elapsed`;
      } else {
        eta.textContent = `${fmtClock(liveElapsedSec)} elapsed`;
      }
    } else {
      if (currentPct >= 95) {
        eta.textContent = `almost done · ${fmtClock(liveElapsedSec)} elapsed`;
      } else {
        eta.textContent = `${fmtClock(liveElapsedSec)} elapsed`;
      }
    }
  };

  const ticker = setInterval(tick, 250);

  const apply = (snap) => {
    latestSnap = snap;
    snapReceivedAt = performance.now();
    const stages = snap.stages || [];
    const running = stages.filter((s) => s.state === "running").at(-1);
    const lastDone = stages.filter((s) => s.state === "done").at(-1);
    const current = running || lastDone;
    if (current) stageTitle.textContent = current.label;
    const msg = (running && (running.message || running.current_file)) || "";
    detail.textContent = msg || " ";

    if (snap.overall != null && snap.overall > displayedFraction) {
      displayedFraction = snap.overall;
    }

    tick();

    stageList.replaceChildren(...stages
      .filter((s) => s.state !== "skipped" && s.key !== "done")
      .map((s) => el("div", {
        class: `stage-row ${s.state === "running" ? "is-running"
          : s.state === "done" ? "is-done" : ""}`,
      }, s.label)));

    if (msg && msg !== lastLoggedMsg) {
      lastLoggedMsg = msg;
      const now = new Date().toLocaleTimeString();
      const line = el("div", { class: "log-line" },
        el("span", { class: "log-time" }, now),
        el("span", { class: "log-msg" }, `${current ? current.label : "Pipeline"}: ${msg}`));
      logEntries.append(line);
      logEntries.scrollTop = logEntries.scrollHeight;
    }
  };

  const stop = watchRun(runId, {
    onSnapshot: apply,
    onDone: () => {
      isTerminal = true;
      clearInterval(ticker);
      pct.textContent = "100";
      dm.update({ fraction: 1, state: "done" });
      stageTitle.textContent = "Done";
      detail.textContent = " ";
      setTimeout(() => { location.hash = `#/results/${encodeURIComponent(runId)}`; }, 900);
    },
    onCancelled: () => {
      isTerminal = true;
      clearInterval(ticker);
      toast("Stopped. Finished clips are kept.");
      location.hash = "#/";
    },
    onError: (message) => {
      isTerminal = true;
      clearInterval(ticker);
      dm.update({ state: "error" });
      stageTitle.textContent = "That didn't work";
      detail.textContent = message;
      cancelBtn.removeEventListener("click", onCancel);
      cancelBtn.textContent = "Back";
      cancelBtn.disabled = false;
      cancelBtn.onclick = () => { location.hash = "#/"; };
    },
  });

  async function onCancel() {
    cancelBtn.disabled = true;
    cancelBtn.textContent = "Stopping…";
    eta.textContent = "stopping…";
    try {
      await api.post(`/api/runs/${runId}/cancel`);
    } catch (e) {
      toast(e.message, "is-error");
      cancelBtn.disabled = false;
      cancelBtn.textContent = "Stop";
    }
  }
  cancelBtn.addEventListener("click", onCancel);

  // page refresh mid-run: the registry may already hold a snapshot
  api.get(`/api/runs/${runId}`).then((st) => {
    if (st.snapshot) apply(st.snapshot);
    if (st.state === "done") location.hash = `#/results/${encodeURIComponent(runId)}`;
  }).catch(async () => {
    // registry entry gone (finished run + app restart) — the clips may
    // still be on disk; land on results instead of stranding the user
    try {
      await api.get(`/api/jobs/${encodeURIComponent(runId)}`);
      location.hash = `#/results/${encodeURIComponent(runId)}`;
    } catch {
      toast("That run isn't active anymore — check your finished clips.");
      location.hash = "#/";
    }
  });

  cleanup = () => { isTerminal = true; clearInterval(ticker); stop(); dm.destroy(); };
}

// ------------------------------------------------------------ activity ----

function renderActivity() {
  const list = el("div", { class: "activity-list" });
  const empty = el("p", { class: "t-dim", style: "margin:0" },
    "No runs yet this session. Start one from New clips — it keeps going even "
    + "if you switch tabs or close this page.");

  const row = (r) => {
    const pct = Math.round((r.overall || 0) * 100);
    const label = r.state === "running" ? "Working"
      : r.state === "done" ? "Done"
      : r.state === "error" ? "Didn't work"
      : r.state === "cancelled" ? "Stopped" : r.state;
    const badge = el("span", {
      class: `badge ${r.state === "done" ? "badge-ok"
        : r.state === "error" ? "badge-warn"
        : r.state === "running" ? "badge-live" : ""}`,
    }, label);
    // running -> live progress view; anything finished -> its clips
    const href = r.state === "running"
      ? `#/run/${encodeURIComponent(r.run_id)}`
      : `#/results/${encodeURIComponent(r.run_id)}`;
    const sub = r.state === "running" ? (r.stage || "Working…")
      : r.state === "error" ? (r.error || "Didn't finish")
      : (r.stage || "");
    return el("a", { class: "activity-row", href },
      el("div", { class: "activity-meta" },
        el("div", { class: "activity-title t-mono" }, r.run_id),
        el("div", { class: "t-dim", style: "font-size:var(--text-xs)" }, sub)),
      el("div", { class: "activity-bar" },
        el("div", {
          class: `activity-fill ${r.state === "error" ? "is-error" : ""}`,
          style: `width:${r.state === "running" ? pct : 100}%`,
        })),
      el("div", { class: "activity-side" }, badge,
        el("span", { class: "t-mono t-dim" },
          r.state === "running" ? `${pct}%` : "")));
  };

  const refresh = async () => {
    let data;
    try { data = await api.get("/api/runs"); }
    catch { return; }  // server briefly unreachable — next tick retries
    list.replaceChildren(...(data.runs.length ? data.runs.map(row) : [empty]));
  };

  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "Activity")),
        el("h1", { class: "t-display" }, "Runs in progress"),
        el("p", { class: "t-dim", style: "margin:4px 0 0" },
          "Clip-making runs on the app, not this page — a run keeps going if "
          + "you switch tabs, minimize, or close the window. Reopen here to "
          + "check on it."))),
    el("div", { class: "card card-flat" }, list)));

  refresh();
  const timer = setInterval(refresh, 2000);
  cleanup = () => clearInterval(timer);
}

// ------------------------------------------------------------- results ----

async function renderResults(jobName) {
  const grid = el("div", { class: "clip-grid" },
    ...Array.from({ length: 3 }, () =>
      el("div", { class: "skeleton skeleton-card" })));
  const sub = el("p", { class: "t-dim", style: "margin:4px 0 0" }, "Loading…");
  const ytBtn = el("button", { class: "btn", type: "button" },
    "Schedule to YouTube");
  ytBtn.addEventListener("click", async () => {
    ytBtn.disabled = true;
    ytBtn.textContent = "Sending…";
    try {
      const r = await api.post("/api/youtube/upload", { job_name: jobName });
      toast(`Sent ${r.uploaded.length} clip(s) to YouTube.`, "is-ok");
    } catch (e) {
      toast(e.message, "is-error");
    }
    ytBtn.disabled = false;
    ytBtn.textContent = "Schedule to YouTube";
  });

  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/activity", "← Back to Activity"),
          el("div", { class: "t-label", style: "margin:0;" }, "Your clips")),
        el("h1", { class: "t-display" }, "Ready to post"),
        sub),
      el("div", { style: "display:flex;gap:12px;flex-wrap:wrap" },
        ytBtn,
        el("a", { class: "btn", href: `/api/jobs/${encodeURIComponent(jobName)}/zip` },
          "Download everything"),
        el("a", { class: "btn btn-ghost", href: "#/" }, "Make more clips"))),
    grid));

  const dots = [];
  cleanup = () => dots.forEach((d) => d.destroy());

  let job;
  try {
    job = await api.get(`/api/jobs/${encodeURIComponent(jobName)}`);
  } catch (e) {
    sub.textContent = e.message;
    grid.replaceChildren();
    return;
  }

  const clips = [...(job.clips || [])]
    .sort((a, b) => isKept(b) - isKept(a) || a.index - b.index);
  const src = clips[0]?.source_name || job.source || "";
  sub.textContent = clips.length
    ? `${clips.filter(isKept).length} clips from ${src}`
    : "";

  if (!clips.length) {
    grid.replaceChildren(el("div", { class: "card" },
      el("p", { class: "t-dim", style: "margin:0" },
        "No clips came out of this video — try a longer one with clear speech.")));
    return;
  }

  grid.replaceChildren(...clips.map((clip) => clipCard(jobName, clip, dots)));
}

function formatEngagementLabel(reason) {
  if (!reason) return "";
  const text = String(reason).trim();
  if (text.startsWith("rule-based:")) {
    const kw = text.match(/(\d+)\s*hook keywords/);
    if (kw && parseInt(kw[1], 10) > 0) return `${kw[1]} hook kw`;
    const m = text.match(/([\d\.]+)\s*words\/sec/);
    if (m) return `${m[1]} w/s`;
    return "Heuristic";
  }
  if (text.startsWith("event-cluster:")) {
    const p = text.match(/peak\s+([a-zA-Z]+)(?:\s+(\d+)\/10)?/);
    if (p) return p[2] ? `${p[1]} ${p[2]}/10` : p[1];
    return "Event Cluster";
  }
  return text.length > 22 ? `${text.slice(0, 20)}…` : text;
}

function clipCard(jobName, clip, dots) {
  const nn = String(clip.index).padStart(2, "0");
  const fileUrl = (name) =>
    `/api/files/${encodeURIComponent(jobName)}/clip_${nn}/${name}`;

  const score = clip.virality?.score;
  const band = clip.virality?.band;
  const scoreRow = el("div", { class: "clip-score" });
  if (score != null) {
    const mini = el("canvas");
    scoreRow.append(mini,
      el("span", { class: "t-mono" }, String(score)),
      band ? el("span", {
        class: `badge ${band === "Strong" ? "badge-ok"
          : band === "Weak" ? "badge-warn" : ""}`,
      }, band) : null);
    dots.push(miniScore(mini, score));
  }
  if (clip.niche) scoreRow.append(el("span", { class: "badge" }, clip.niche));
  if (clip.reason) {
    const cleanLabel = formatEngagementLabel(clip.reason);
    scoreRow.append(el("span", {
      class: "badge badge-ok",
      title: `Engagement signal: ${clip.reason}`,
    }, cleanLabel));
  }

  const meta = clip.metadata || {};
  const metaBox = el("div", { class: "clip-meta-box" });
  if (meta.description) {
    metaBox.append(el("p", { class: "clip-desc-text" }, meta.description));
  }
  if (meta.hashtags && meta.hashtags.length) {
    metaBox.append(el("div", { class: "clip-tags-row" },
      ...meta.hashtags.map((t) => el("span", { class: "clip-tag" }, t))));
  }
  const copyTitleBtn = el("button", { class: "btn btn-ghost btn-sm", type: "button" }, "Copy Title");
  copyTitleBtn.addEventListener("click", () => copyToClipboard(meta.title || `Clip ${clip.index + 1}`, copyTitleBtn));

  const fullSocialText = `${meta.description || ''}\n\n${(meta.hashtags || []).join(' ')}`.trim();
  const copySocialBtn = el("button", { class: "btn btn-ghost btn-sm", type: "button" }, "Copy Description & Tags");
  copySocialBtn.addEventListener("click", () => copyToClipboard(fullSocialText, copySocialBtn));

  if (meta.title || meta.description) {
    metaBox.append(el("div", { class: "clip-copy-btns" }, copyTitleBtn, copySocialBtn));
  }

  const srcStart = clip.original_source_start_s ?? clip.start;
  const srcEnd = clip.original_source_end_s ?? clip.end;

  const keptBtn = el("button", { class: "btn btn-ghost btn-sm", type: "button" });
  const card = el("article", { class: "card card-hover clip-card" });
  const setKeptUi = (kept) => {
    keptBtn.textContent = kept ? "Discard" : "Keep";
    card.classList.toggle("is-discarded", !kept);
  };
  setKeptUi(isKept(clip));
  keptBtn.addEventListener("click", async () => {
    const next = card.classList.contains("is-discarded");
    keptBtn.disabled = true;
    try {
      await api.put(
        `/api/jobs/${encodeURIComponent(jobName)}/clips/${clip.index}/kept`,
        { kept: next });
      setKeptUi(next);
    } catch (e) {
      toast(e.message, "is-error");
    }
    keptBtn.disabled = false;
  });

  const exclInput = el("input", { type: "checkbox" });
  exclInput.checked = !clip.upload_excluded;
  exclInput.addEventListener("change", async () => {
    try {
      await api.put(
        `/api/jobs/${encodeURIComponent(jobName)}/clips/${clip.index}/exclude`,
        { exclude: !exclInput.checked });
    } catch (e) {
      exclInput.checked = !exclInput.checked;
      toast(e.message, "is-error");
    }
  });

  const key = `output/${jobName}/clip_${nn}`;
  const delBtn = el("button", { class: "btn btn-danger btn-sm", type: "button" },
    "Delete");
  delBtn.addEventListener("click", async () => {
    const size = clip.bytes ? ` (${fmtBytes(clip.bytes)})` : "";
    const approvedPending = clip.approval === "approved";
    const ok = await confirmDialog({
      title: "Delete this clip?",
      body: `This removes the clip's files from disk${size}. `
        + (approvedPending
          ? "It's approved and waiting to upload — deleting it now cancels that. "
          : "")
        + "This can't be undone.",
    });
    if (!ok) return;
    delBtn.disabled = true;
    try {
      const r = await api.del("/api/clips", { keys: [key] });
      if (r.deleted) {
        toast(`Deleted — freed ${fmtBytes(r.reclaimed_bytes)}.`, "is-ok");
        card.remove();
      } else {
        toast(r.results[0]?.status === "uploading"
          ? "That clip is uploading right now — try again once it's done."
          : "Couldn't delete that clip.", "is-error");
        delBtn.disabled = false;
      }
    } catch (e) { toast(e.message, "is-error"); delBtn.disabled = false; }
  });

  card.append(
    clipVideo(fileUrl("final.mp4"), { preload: isKept(clip) ? "metadata" : "none" }),
    el("div", { class: "clip-body" },
      el("h2", { class: "clip-title" }, clip.metadata?.title || `Clip ${clip.index + 1}`),
      el("div", { class: "clip-facts" },
        el("span", { class: "t-mono" }, fmtClock(clip.duration)),
        srcStart != null
          ? el("span", { class: "t-mono t-dim" },
              `from ${fmtClock(srcStart)}–${fmtClock(srcEnd)}`)
          : null),
      scoreRow,
      metaBox,
      el("div", { class: "clip-actions" },
        el("label", { class: "opt-toggle grow" }, "Auto-upload",
          el("span", { class: "switch" }, exclInput, el("span", { class: "knob" }))),
        el("a", {
          class: "btn btn-sm",
          href: `#/edit/${encodeURIComponent(jobName)}/${clip.index}`,
        }, "Edit"),
        el("a", {
          class: "btn btn-sm", href: fileUrl("final.mp4"),
          download: `clip_${nn}.mp4`,
        }, "Download"),
        el("a", {
          class: "btn btn-sm btn-ghost", href: fileUrl("poster.jpg"),
          download: `clip_${nn}_thumb.jpg`,
        }, "Thumbnail"),
        keptBtn, delBtn)));
  return card;
}

// -------------------------------------------------------------- editor ----

async function renderEditor(jobName, indexStr) {
  const index = Number(indexStr);
  let job, edData;
  try {
    [job, edData] = await Promise.all([
      api.get(`/api/jobs/${encodeURIComponent(jobName)}`),
      api.get(`/api/jobs/${encodeURIComponent(jobName)}/clips/${index}/editor`).catch(() => null)
    ]);
  } catch (e) {
    toast(e.message, "is-error");
    location.hash = "#/history";
    return;
  }
  const clip = (job.clips || []).find((c) => c.index === index);
  if (!clip) {
    toast("That clip can't be found.", "is-error");
    location.hash = `#/results/${encodeURIComponent(jobName)}`;
    return;
  }

  const nn = String(index).padStart(2, "0");
  const videoUrl = () =>
    `/api/files/${encodeURIComponent(jobName)}/clip_${nn}/final.mp4?t=${Date.now()}`;
  const video = clipVideo(videoUrl());

  const state = {
    preset: edData?.caption_preset || clip.preset || "modern-bold",
    styleRefine: true,
    subsMode: ""
  };

  // 1. Timeline Controls
  const startIn = el("input", { class: "input t-mono", type: "number",
                                step: "0.1", value: (edData?.start ?? clip.start).toFixed(1) });
  const endIn = el("input", { class: "input t-mono", type: "number",
                              step: "0.1", value: (edData?.end ?? clip.end).toFixed(1) });
  const durLabel = el("span", { class: "badge t-mono", style: "align-self:center;" },
    `${((edData?.end ?? clip.end) - (edData?.start ?? clip.start)).toFixed(1)}s`);

  const updateDur = () => {
    const s = Number(startIn.value);
    const e = Number(endIn.value);
    durLabel.textContent = `${Math.max(0, e - s).toFixed(1)}s`;
  };
  startIn.addEventListener("input", updateDur);
  endIn.addEventListener("input", updateDur);

  const trimStartMinus = el("button", { class: "btn btn-sm", type: "button", title: "Trim start -1s" }, "-1s");
  trimStartMinus.onclick = () => { startIn.value = Math.max(0, Number(startIn.value) - 1.0).toFixed(1); updateDur(); };
  const trimStartPlus = el("button", { class: "btn btn-sm", type: "button", title: "Trim start +1s" }, "+1s");
  trimStartPlus.onclick = () => { startIn.value = (Number(startIn.value) + 1.0).toFixed(1); updateDur(); };

  const trimEndMinus = el("button", { class: "btn btn-sm", type: "button", title: "Trim end -1s" }, "-1s");
  trimEndMinus.onclick = () => { endIn.value = Math.max(0, Number(endIn.value) - 1.0).toFixed(1); updateDur(); };
  const trimEndPlus = el("button", { class: "btn btn-sm", type: "button", title: "Trim end +1s" }, "+1s");
  trimEndPlus.onclick = () => { endIn.value = (Number(endIn.value) + 1.0).toFixed(1); updateDur(); };

  const snapBtn = el("button", { class: "btn btn-sm", type: "button" }, "Snap to sentences");
  snapBtn.addEventListener("click", async () => {
    try {
      const r = await api.get(`/api/jobs/${encodeURIComponent(jobName)}`
        + `/clips/snap?start=${startIn.value}&end=${endIn.value}`);
      startIn.value = r.start.toFixed(1);
      endIn.value = r.end.toFixed(1);
      updateDur();
    } catch (e) { toast(e.message, "is-error"); }
  });

  const resetTimelineBtn = el("button", { class: "btn btn-sm btn-ghost", type: "button" }, "Reset AI bounds");
  resetTimelineBtn.onclick = () => {
    if (edData?.original) {
      startIn.value = Number(edData.original.start).toFixed(1);
      endIn.value = Number(edData.original.end).toFixed(1);
      updateDur();
      toast("Reset timeline to AI original.", "is-ok");
    }
  };

  // 2. Video Framing & AI Director Controls
  const FRAMING_MODES = [
    { id: "smart_director", label: "Smart AI Director (Auto Multi-Layout)" },
    { id: "full", label: "Full-Screen Single Speaker" },
    { id: "split_tb", label: "Top-Bottom Split Screen" },
    { id: "split_lr", label: "Left-Right Split Screen" },
    { id: "wide", label: "Wide / Context Mode (Blurred BG)" },
    { id: "manual", label: "Manual Offsets Only" }
  ];
  const framingModeSel = el("select", { class: "select" },
    ...FRAMING_MODES.map((m) => el("option", m.id === (edData?.framing_mode || "smart_director") ? { value: m.id, selected: "" } : { value: m.id }, m.label))
  );

  const panXIn = el("input", { type: "range", min: "-100", max: "100", step: "5", value: String(edData?.crop_x_offset || 0), class: "input" });
  const panXVal = el("span", { class: "t-mono t-dim", style: "width:45px;text-align:right;" }, `${panXIn.value}px`);
  panXIn.oninput = () => { panXVal.textContent = `${panXIn.value}px`; };

  const tiltYIn = el("input", { type: "range", min: "-100", max: "100", step: "5", value: String(edData?.crop_y_offset || 0), class: "input" });
  const tiltYVal = el("span", { class: "t-mono t-dim", style: "width:45px;text-align:right;" }, `${tiltYIn.value}px`);
  tiltYIn.oninput = () => { tiltYVal.textContent = `${tiltYIn.value}px`; };

  const zoomIn = el("input", { type: "range", min: "1.0", max: "1.30", step: "0.02", value: String(edData?.zoom || 1.0), class: "input" });
  const zoomVal = el("span", { class: "t-mono t-dim", style: "width:45px;text-align:right;" }, `${Number(zoomIn.value).toFixed(2)}x`);
  zoomIn.oninput = () => { zoomVal.textContent = `${Number(zoomIn.value).toFixed(2)}x`; };

  const resetFramingBtn = el("button", { class: "btn btn-sm btn-ghost", type: "button" }, "Reset Framing");
  resetFramingBtn.onclick = () => {
    framingModeSel.value = "smart_director";
    panXIn.value = "0"; panXVal.textContent = "0px";
    tiltYIn.value = "0"; tiltYVal.textContent = "0px";
    zoomIn.value = "1.0"; zoomVal.textContent = "1.00x";
  };

  // 3. Caption & Subtitle Editor
  const captionTextIn = el("textarea", { class: "input", rows: "3", placeholder: "Clip spoken transcription text..." });
  captionTextIn.value = edData?.caption_text || "";

  const PRESET_OPTIONS = [
    { id: "modern-bold", label: "Modern Bold (High Impact)" },
    { id: "minimal-clean", label: "Minimal Clean (Subtle Fade)" },
    { id: "karaoke-highlight", label: "Karaoke Highlight (Word Pop)" },
    { id: "creator-style", label: "Creator Style (Badge Box)" },
    { id: "cinematic", label: "Cinematic (Warm Gold)" },
    { id: "dynamic-social", label: "Dynamic Social (Neon Green)" },
    { id: "karaoke-pop", label: "Karaoke Pop (Legacy)" },
    { id: "bold-impact", label: "Bold Impact (Legacy)" },
    { id: "none", label: "No captions (Clean Video)" }
  ];
  const initialPreset = state.preset || edData?.caption_preset || "modern-bold";
  const initialCapsEnabled = edData?.captions_enabled !== false && initialPreset !== "none";

  const capsToggle = toggle(initialCapsEnabled, "Burn Subtitles on this clip");
  const noCapsNoticeEditor = el("div", {
    class: "card card-flat",
    style: "display:none;padding:10px 14px;background:rgba(255,255,255,0.03);border:1px dashed rgba(255,255,255,0.15);border-radius:8px;font-size:var(--text-xs);color:var(--text-dim);"
  }, "⚡ Subtitles disabled for this clip — will be exported as clean video without burned text.");

  const presetSel = el("select", { class: "select" },
    ...PRESET_OPTIONS.map((p) => el("option", p.id === initialPreset ? { value: p.id, selected: "" } : { value: p.id }, p.label)));

  const langSel = el("select", { class: "select" },
    el("option", { value: "hinglish" }, "Hinglish (Latin alphabet)"),
    el("option", { value: "hindi" }, "Hindi (Devanagari script)"),
    el("option", { value: "english" }, "English"));
  langSel.value = edData?.caption_lang || clip.lang || "hinglish";

  let initialAnchor = edData?.caption_anchor || 0.60;
  let initialPos = edData?.caption_position || (initialAnchor <= 0.30 ? "top" : initialAnchor <= 0.65 ? "center" : "bottom");

  const capPosSel = el("select", { class: "select" },
    el("option", { value: "bottom" }, "Bottom (Recommended — 78% from top)"),
    el("option", { value: "center" }, "Center (Middle — 50% from top)"),
    el("option", { value: "top" }, "Top (Upper third — 20% from top)"),
    el("option", { value: "custom" }, "Custom Position (Use slider below)"));
  capPosSel.value = initialPos;

  const anchorIn = el("input", { type: "range", min: "0.15", max: "0.85", step: "0.01", value: String(initialAnchor), class: "input" });
  const anchorVal = el("span", { class: "t-mono t-dim", style: "width:45px;text-align:right;" }, `${Math.round(Number(anchorIn.value) * 100)}%`);

  capPosSel.onchange = () => {
    if (capPosSel.value === "bottom") {
      anchorIn.value = "0.78";
    } else if (capPosSel.value === "center") {
      anchorIn.value = "0.50";
    } else if (capPosSel.value === "top") {
      anchorIn.value = "0.20";
    }
    anchorVal.textContent = `${Math.round(Number(anchorIn.value) * 100)}%`;
  };

  anchorIn.oninput = () => {
    anchorVal.textContent = `${Math.round(Number(anchorIn.value) * 100)}%`;
    capPosSel.value = "custom";
  };

  const captionControlsGroup = el("div", { style: "display:grid;gap:12px;" },
    field("Transcription Text", captionTextIn),
    field("Caption Position", capPosSel),
    field("Vertical Position (% from top)", el("div", { style: "display:flex;align-items:center;gap:10px;" }, anchorIn, anchorVal)),
    field("Preset Style", presetSel),
    field("Language", langSel));

  function updateEditorCaptionsUi() {
    const on = capsToggle.input.checked && presetSel.value !== "none";
    captionControlsGroup.style.display = on ? "" : "none";
    noCapsNoticeEditor.style.display = on ? "none" : "";
  }

  capsToggle.input.onchange = () => {
    if (capsToggle.input.checked) {
      if (presetSel.value === "none") presetSel.value = "modern-bold";
    } else {
      presetSel.value = "none";
    }
    state.preset = presetSel.value;
    updateEditorCaptionsUi();
  };

  presetSel.onchange = () => {
    state.preset = presetSel.value;
    if (presetSel.value === "none") {
      capsToggle.input.checked = false;
    } else {
      capsToggle.input.checked = true;
    }
    updateEditorCaptionsUi();
  };

  updateEditorCaptionsUi();

  // 4. Metadata Editor
  const titleIn = el("input", { class: "input", type: "text", value: edData?.title || clip.metadata?.title || `Clip ${index + 1}` });
  const descIn = el("textarea", { class: "input", rows: "2", placeholder: "Clip description..." });
  descIn.value = edData?.description || clip.metadata?.description || "";
  const tagsIn = el("input", { class: "input", type: "text", value: (edData?.hashtags || clip.metadata?.hashtags || []).join(" ") });
  const hookIn = el("input", { class: "input", type: "text", value: edData?.hook || clip.hook || "" });

  const regenBtn = el("button", { class: "btn btn-sm", type: "button" }, "Rewrite with AI");
  regenBtn.addEventListener("click", async () => {
    regenBtn.disabled = true;
    try {
      const meta = await api.post(`/api/jobs/${encodeURIComponent(jobName)}/clips/${index}/metadata`);
      titleIn.value = meta.title || titleIn.value;
      descIn.value = meta.description || descIn.value;
      tagsIn.value = (meta.hashtags || []).join(" ");
      toast("Title and description rewritten.", "is-ok");
    } catch (e) { toast(e.message, "is-error"); }
    regenBtn.disabled = false;
  });

  // 5. Audio Controls
  const speechEnhanceToggle = toggle(edData?.speech_enhance ?? true, "Voice clarity & noise reduction (80Hz highpass, FFT denoise)");
  const musicToggle = toggle(edData?.music_enabled ?? false, "Background music");
  const musicVolIn = el("input", { type: "range", min: "-28", max: "-8", step: "1", value: String(edData?.music_volume_db || -18), class: "input" });
  const musicVolVal = el("span", { class: "t-mono t-dim", style: "width:45px;text-align:right;" }, `${musicVolIn.value} dB`);
  musicVolIn.oninput = () => { musicVolVal.textContent = `${musicVolIn.value} dB`; };

  // Progress Bar for Export
  const progWrap = el("div", { class: "edit-progress", style: "display:none;" });
  const progCanvas = el("canvas");
  const progLabel = el("span", { class: "t-mono t-dim" }, "");
  progWrap.append(progCanvas, progLabel);

  // Save Edits (without full export)
  const saveBtn = el("button", { class: "btn", type: "button" }, "Save Edits");
  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    try {
      await api.post(`/api/jobs/${encodeURIComponent(jobName)}/clips/${index}/editor/save`, {
        start: Number(startIn.value),
        end: Number(endIn.value),
        title: titleIn.value,
        description: descIn.value,
        hashtags: tagsIn.value.split(" ").filter(Boolean),
        hook: hookIn.value,
        caption_text: captionTextIn.value,
        caption_preset: presetSel.value,
        caption_lang: langSel.value,
        caption_anchor: Number(anchorIn.value),
        caption_position: capPosSel.value === "custom" ? (Number(anchorIn.value) < 0.35 ? "top" : Number(anchorIn.value) < 0.65 ? "center" : "bottom") : capPosSel.value,
        captions_enabled: Boolean(capsToggle.input.checked && presetSel.value !== "none"),
        crop_x_offset: Number(panXIn.value),
        crop_y_offset: Number(tiltYIn.value),
        zoom: Number(zoomIn.value),
        framing_mode: framingModeSel.value,
        music_enabled: musicToggle.input.checked,
        music_volume_db: Number(musicVolIn.value),
        speech_enhance: speechEnhanceToggle.input.checked,
      });
      toast("Clip edits saved.", "is-ok");
    } catch (err) { toast(err.message, "is-error"); }
    saveBtn.disabled = false;
  };

  // Full Export
  const exportBtn = el("button", { class: "btn btn-primary", type: "button" }, "Export Edited Clip");
  let dm = null;
  let stopWatch = null;

  exportBtn.addEventListener("click", async () => {
    const start = Number(startIn.value);
    const end = Number(endIn.value);
    if (!(end > start)) {
      toast("The end time must be after the start time.", "is-error");
      return;
    }
    exportBtn.disabled = true;
    progWrap.style.display = "";
    if (!dm) dm = createDotMatrix(progCanvas, { variant: "progress" });
    dm.update({ fraction: 0, state: "running" });

    try {
      const { run_id } = await api.post(
        `/api/jobs/${encodeURIComponent(jobName)}/clips/${index}/editor/export`, {
          start, end,
          title: titleIn.value,
          description: descIn.value,
          hashtags: tagsIn.value.split(" ").filter(Boolean),
          hook: hookIn.value,
          caption_text: captionTextIn.value,
          caption_preset: presetSel.value,
          caption_lang: langSel.value,
          caption_anchor: Number(anchorIn.value),
          caption_position: capPosSel.value === "custom" ? (Number(anchorIn.value) < 0.35 ? "top" : Number(anchorIn.value) < 0.65 ? "center" : "bottom") : capPosSel.value,
          captions_enabled: Boolean(capsToggle.input.checked && presetSel.value !== "none"),
          crop_x_offset: Number(panXIn.value),
          crop_y_offset: Number(tiltYIn.value),
          zoom: Number(zoomIn.value),
          framing_mode: framingModeSel.value,
          music_enabled: musicToggle.input.checked,
          music_volume_db: Number(musicVolIn.value),
          speech_enhance: speechEnhanceToggle.input.checked,
        });

      stopWatch = watchRun(run_id, {
        onSnapshot: (snap) => {
          dm.update({ fraction: snap.overall || 0, state: "running" });
          const running = (snap.stages || []).filter((s) => s.state === "running").at(-1);
          progLabel.textContent = running ? running.label : "";
        },
        onDone: (result) => {
          dm.update({ state: "done" });
          progLabel.textContent = "Exported";
          video.src = videoUrl();
          toast("Edited clip successfully exported!", "is-ok");
          exportBtn.disabled = false;
        },
        onCancelled: () => { exportBtn.disabled = false; },
        onError: (message) => {
          dm.update({ state: "error" });
          progLabel.textContent = "";
          toast(message, "is-error");
          exportBtn.disabled = false;
        },
      });
    } catch (e) {
      toast(e.message, "is-error");
      progWrap.style.display = "none";
      exportBtn.disabled = false;
    }
  });

  const fullResetBtn = el("button", { class: "btn btn-danger btn-sm", type: "button" }, "Reset All to AI Original");
  fullResetBtn.onclick = async () => {
    const ok = await confirmDialog({
      title: "Reset all edits?",
      body: "This discards all manual trim, crop, caption, and metadata adjustments and reverts to the original AI settings.",
      confirmLabel: "Reset Clip"
    });
    if (!ok) return;
    try {
      await api.post(`/api/jobs/${encodeURIComponent(jobName)}/clips/${index}/editor/reset`);
      toast("Reverted to AI original.", "is-ok");
      renderEditor(jobName, indexStr);
    } catch (err) { toast(err.message, "is-error"); }
  };

  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { class: "t-label" }, "Advanced Clip Editing Studio"),
        el("h1", { class: "t-display" }, `Clip ${index + 1}`),
        el("p", { class: "t-dim", style: "margin:4px 0 0" }, clip.source_name || job.source || "")),
      el("div", { style: "display:flex;gap:8px;align-items:center;" },
        fullResetBtn,
        el("a", { class: "btn btn-ghost", href: `#/results/${encodeURIComponent(jobName)}` }, "← Back to clips"))),
    el("div", { class: "editor-grid" },
      el("div", { class: "editor-video" }, video),
      el("div", { class: "editor-controls", style: "display:grid;gap:16px;" },

        // Section 1: Timeline
        el("div", { class: "card card-flat", style: "display:grid;gap:12px;" },
          el("div", { style: "display:flex;justify-content:space-between;align-items:center;" },
            el("h3", { class: "t-title", style: "margin:0;" }, "1. Timeline & Duration"),
            durLabel),
          field("Clip Start & End (seconds)",
            el("div", { class: "field-inline", style: "flex-wrap:wrap;gap:8px;" },
              startIn, "to", endIn, snapBtn)),
          el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;align-items:center;" },
            el("span", { class: "t-dim", style: "font-size:var(--text-xs);" }, "Trim start:"),
            trimStartMinus, trimStartPlus,
            el("span", { class: "t-dim", style: "font-size:var(--text-xs);margin-left:8px;" }, "Trim end:"),
            trimEndMinus, trimEndPlus,
            resetTimelineBtn)),

        // Section 2: Framing & AI Director
        el("div", { class: "card card-flat", style: "display:grid;gap:12px;" },
          el("div", { style: "display:flex;justify-content:space-between;align-items:center;" },
            el("h3", { class: "t-title", style: "margin:0;" }, "2. Framing & AI Director"),
            resetFramingBtn),
          field("Framing Mode", framingModeSel),
          field("Horizontal Pan (px)", el("div", { style: "display:flex;align-items:center;gap:10px;" }, panXIn, panXVal)),
          field("Vertical Tilt (px)", el("div", { style: "display:flex;align-items:center;gap:10px;" }, tiltYIn, tiltYVal)),
          field("Manual Zoom Factor", el("div", { style: "display:flex;align-items:center;gap:10px;" }, zoomIn, zoomVal))),

        // Section 3: Captions
        el("div", { class: "card card-flat", style: "display:grid;gap:12px;" },
          el("h3", { class: "t-title", style: "margin:0;" }, "3. Captions & Typography"),
          capsToggle.node,
          noCapsNoticeEditor,
          captionControlsGroup),

        // Section 4: Metadata
        el("div", { class: "card card-flat", style: "display:grid;gap:12px;" },
          el("div", { style: "display:flex;justify-content:space-between;align-items:center;" },
            el("h3", { class: "t-title", style: "margin:0;" }, "4. Title & Metadata"),
            regenBtn),
          field("Title", titleIn),
          field("Description", descIn),
          field("Hashtags", tagsIn),
          field("Opening Hook", hookIn)),

        // Section 5: Audio
        el("div", { class: "card card-flat", style: "display:grid;gap:12px;" },
          el("h3", { class: "t-title", style: "margin:0;" }, "5. Professional Audio"),
          speechEnhanceToggle.node,
          musicToggle.node,
          field("Music Bed Volume (dB)", el("div", { style: "display:flex;align-items:center;gap:10px;" }, musicVolIn, musicVolVal))),

        // Action Buttons
        el("div", { class: "field-inline", style: "gap:12px;margin-top:8px;" },
          saveBtn,
          exportBtn,
          progWrap)))));

  cleanup = () => { if (stopWatch) stopWatch(); if (dm) dm.destroy(); };
}

// -------------------------------------------------------------- library ----

function renderLibrary() {
  const body = el("div", { class: "card card-flat", style: "display:grid;gap:12px" });
  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "Library")),
        el("h1", { class: "t-display" }, "All clips"),
        el("p", { class: "t-dim", style: "margin:4px 0 0" },
          "Every clip on disk, from every run — samples, pending, approved, "
          + "uploaded, everything."))),
    body));

  const lib = mountLibrary(body);
  cleanup = lib.dispose;
}

// --------------------------------------------------------------- queue ----

function renderQueue() {
  const linesIn = el("textarea", {
    class: "textarea", rows: "5",
    placeholder: "One link or file path per line",
  });
  const musicState = { value: "" };
  const musicRow = pickerRow("Background music for these", "No music",
    async () => {
      const v = await pickMusic(musicState.value);
      if (v !== null) {
        musicState.value = v;
        musicRow.btn.textContent =
          v === "" ? "No music" : v === "auto" ? "Pick for me"
            : v === "random" ? "Surprise me" : v;
      }
    });
  const addBtn = el("button", { class: "btn btn-primary", type: "button" },
    "Add to the queue");
  const table = el("div", { class: "queue-table" });
  const inboxToggle = toggle(false, "Watch the inbox folder for new videos");
  inboxToggle.input.addEventListener("change", async () => {
    try {
      const r = await api.put("/api/batch/inbox",
        { enabled: inboxToggle.input.checked });
      toast(r.message || "Saved.");
    } catch (e) {
      inboxToggle.input.checked = !inboxToggle.input.checked;
      toast(e.message, "is-error");
    }
  });

  const renderRows = (rows) => {
    if (!rows.length) {
      table.replaceChildren(el("p", { class: "t-dim", style: "margin:0" },
        "The queue is empty — add a few links above."));
      return;
    }
    table.replaceChildren(...rows.map(([id, source, status, message]) =>
      el("div", { class: "queue-row" },
        el("span", { class: "t-mono t-dim" }, id),
        el("span", { class: "queue-src" }, source),
        el("span", {
          class: `badge ${status === "done" ? "badge-ok"
            : status === "error" ? "badge-warn"
              : status === "running" ? "badge-live" : ""}`,
        }, status === "done" ? "Done" : status === "error" ? "Didn't work"
          : status === "running" ? "Working" : "Waiting"),
        el("span", { class: "t-dim queue-msg" }, message || ""))));
  };
  const refresh = async () => {
    try { renderRows((await api.get("/api/batch")).rows); }
    catch { /* server briefly unreachable — next tick retries */ }
  };

  addBtn.addEventListener("click", async () => {
    const sources = linesIn.value.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!sources.length) {
      toast("Add at least one link or file path first.", "is-error");
      return;
    }
    addBtn.disabled = true;
    try {
      const r = await api.post("/api/batch",
        { sources, music: musicState.value });
      toast(`Queued ${r.queued} video(s).`, "is-ok");
      linesIn.value = "";
      renderRows(r.rows);
    } catch (e) { toast(e.message, "is-error"); }
    addBtn.disabled = false;
  });

  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "Queue")),
        el("h1", { class: "t-display" }, "Several videos at once"),
        el("p", { class: "t-dim", style: "margin:4px 0 0" },
          "They run one after another — leave this open or come back later.")),
      el("a", { class: "btn", href: "/api/batch/zip" }, "Download everything")),
    el("div", { class: "queue-grid" },
      el("div", { class: "card", style: "display:grid;gap:16px" },
        field("Videos to turn into clips", linesIn),
        musicRow.node,
        el("div", {}, addBtn),
        inboxToggle.node),
      el("div", { class: "card card-flat" }, table))));

  refresh();
  const timer = setInterval(refresh, 3000);
  cleanup = () => clearInterval(timer);
}

// ------------------------------------------------------------- youtube ----

async function renderYouTube() {
  const body = el("div", { class: "yt-grid" },
    el("div", { class: "skeleton", style: "height:220px" }),
    el("div", { class: "skeleton", style: "height:220px" }));
  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "YouTube")),
        el("h1", { class: "t-display" }, "Auto-upload"),
        el("p", { class: "t-dim", style: "margin:4px 0 0" },
          "Finished clips get scheduled to your channel at good times of day."))),
    body));

  let st;
  try {
    st = await api.get("/api/youtube/state");
  } catch (e) {
    body.replaceChildren(el("div", { class: "card" },
      el("p", { class: "t-dim", style: "margin:0" }, e.message)));
    return;
  }

  const left = el("div", { class: "card", style: "display:grid;gap:16px" });
  const right = el("div", { class: "card card-flat", style: "display:grid;gap:12px" });
  const postQueueCard = el("div", {
    class: "card uq-span", style: "display:grid;gap:12px",
  });
  body.replaceChildren(left, right, postQueueCard);
  mountPostQueue(postQueueCard);

  if (!st.configured) {
    left.append(
      el("h2", { class: "t-title", style: "margin:0" }, "One-time setup"),
      el("p", { class: "t-dim", style: "margin:0" },
        "Frameza needs a Google credentials file to talk to your channel. ",
        "It stays on this computer."),
      el("pre", { class: "setup-pre t-mono" },
        (st.setup_instructions || "").replace(/[#*`]/g, "")));
  } else if (!st.authorized) {
    const connectBtn = el("button", { class: "btn btn-primary", type: "button" },
      "Connect to YouTube");
    connectBtn.addEventListener("click", async () => {
      connectBtn.disabled = true;
      connectBtn.textContent = "A Google window is open — finish there…";
      try {
        await api.post("/api/youtube/authorize");
        toast("Connected.", "is-ok");
        navigate();   // re-render with the authorized panel
      } catch (e) {
        toast(e.message, "is-error");
        connectBtn.disabled = false;
        connectBtn.textContent = "Connect to YouTube";
      }
    });
    left.append(
      el("h2", { class: "t-title", style: "margin:0" }, "Connect your channel"),
      el("p", { class: "t-dim", style: "margin:0" },
        "One-time sign-in with Google. Nothing uploads until you turn ",
        "auto-upload on."),
      el("div", {}, connectBtn));
  } else {
    const panel = st.panel || {};
    const autoToggle = toggle(!!panel.auto_enabled, "Upload new clips automatically");
    autoToggle.input.addEventListener("change", async () => {
      try {
        await api.put("/api/youtube/auto",
          { enabled: autoToggle.input.checked });
        toast(autoToggle.input.checked
          ? "On — good clips get scheduled after each run."
          : "Off — nothing uploads on its own.", "is-ok");
      } catch (e) {
        autoToggle.input.checked = !autoToggle.input.checked;
        toast(e.message, "is-error");
      }
    });
    const slot = panel.next_slot_ist
      ? new Date(panel.next_slot_ist).toLocaleString([], {
          weekday: "short", hour: "2-digit", minute: "2-digit" })
      : "—";
    left.append(
      el("div", { class: "field-inline" },
        el("span", { class: "badge badge-ok" }, "Connected"), autoToggle.node),
      el("div", { class: "yt-stats" },
        el("div", {},
          el("div", { class: "t-hero-num" },
            `${panel.uploads_today ?? 0}/${panel.max_per_day ?? 3}`),
          el("div", { class: "t-label" }, "Scheduled today")),
        el("div", {},
          el("div", { class: "t-title" }, slot),
          el("div", { class: "t-label" }, "Next upload slot"))),
      el("p", { class: "t-dim", style: "margin:0;font-size:var(--text-sm)" },
        "Uploads start private and go public at the scheduled time. ",
        "Use a clip's Auto-upload switch to leave it out."));
  }

  const recent = st.panel?.recent || [];
  right.append(el("h2", { class: "t-title", style: "margin:0" }, "Recent uploads"));
  right.append(el("p", { class: "t-dim", style: "margin:0" }, recent.length
    ? "Your uploaded videos are listed in the Uploaded section below, with links."
    : "Nothing uploaded yet — finished clips appear in the Uploaded section below."));

  if (st.authorized) {
    const queueCard = el("div", { class: "card uq-span", style: "display:grid;gap:16px" },
      el("div", {},
        el("h2", { class: "t-title", style: "margin:0" }, "Upload queue"),
        el("p", { class: "t-dim", style: "margin:4px 0 0" },
          "Send clips right now instead of waiting for the next slot.")));
    body.append(queueCard);
    const uq = mountUploadQueue(queueCard);
    cleanup = uq.dispose;
  }
}

// ----------------------------------------------------------- analytics ----

function fmtIstHour(h) {
  return `${((h + 11) % 12) + 1} ${h < 12 ? "AM" : "PM"}`;
}

// publish_at is always an ISO string with a fixed +05:30 offset (see
// upload_scheduler.py's IST constant) — read the hour off the string
// directly rather than through Date, which would convert to the browser's
// local timezone and disagree with the backend's IST-based recommendations.
function istHourFromIso(iso) {
  const m = /T(\d{2}):/.exec(iso || "");
  return m ? Number(m[1]) : null;
}

function analyticsRecCard(rec) {
  const evidence = Object.entries(rec.evidence || {})
    .map(([k, v]) => `${k}: ${Array.isArray(v) || typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" · ");
  const applyBtn = rec.action?.kind === "apply_publish_slot"
    ? el("button", { class: "btn btn-sm", type: "button" },
        `Add ${fmtIstHour(rec.action.hour)} to publish slots`)
    : null;
  if (applyBtn) {
    applyBtn.addEventListener("click", async () => {
      applyBtn.disabled = true;
      try {
        await api.put("/api/analytics/publish-slot", { hour: rec.action.hour });
        toast("Added to your publish slots.", "is-ok");
      } catch (e) {
        toast(e.message, "is-error");
      } finally {
        applyBtn.disabled = false;
      }
    });
  }
  return el("div", { class: "an-rec" },
    el("p", { style: "margin:0" }, rec.message),
    evidence ? el("p", { class: "an-evidence" }, evidence) : null,
    applyBtn);
}

function analyticsVideoHeadRow() {
  return el("div", { class: "an-row an-row-head" },
    el("div", { class: "an-title" }, "Title"),
    el("div", {}, "Views"), el("div", {}, "Avg %"),
    el("div", {}, "Likes"), el("div", {}, "Subs+"));
}

function analyticsVideoRow(v) {
  return el("div", { class: "an-row" },
    el("div", { class: "an-title" }, v.title),
    el("div", {}, v.views),
    el("div", {}, `${v.avg_view_pct}%`),
    el("div", {}, v.likes),
    el("div", {}, v.subs_gained));
}

async function renderPresets() {
  const card = el("div", { class: "card", style: "display:grid;gap:12px" });
  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "Presets")),
        el("h1", { class: "t-display" }, "Your editing styles"))),
    card));
  mountPresets(card);
}

async function renderChannels() {
  const card = el("div", { class: "card", style: "display:grid;gap:12px" });
  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "Channels")),
        el("h1", { class: "t-display" }, "Approved channels"))),
    card));
  mountChannels(card);
}

async function renderAnalytics() {
  const body = el("div", { class: "an-dashboard-grid" },
    el("div", { class: "skeleton", style: "height:220px" }),
    el("div", { class: "skeleton", style: "height:220px" }));
  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "Analytics")),
        el("h1", { class: "t-display" }, "How your clips are doing"),
        el("p", { class: "t-dim", style: "margin:4px 0 0" },
          "Numbers and suggestions come only from your own uploaded clips."))),
    body));

  // Reads only local files (upload_log.json, config, its own stats store) —
  // mounted independently so it shows real gate status even before YouTube
  // is connected or if the main analytics fetch below fails.
  const scheduleCard = el("div", { class: "card", style: "display:grid;gap:12px" });
  mountPublishTiming(scheduleCard);

  // Operation dashboard: per-channel pull/clip/post counts + preset usage.
  // Local files only; renders regardless of YouTube connection state.
  const opsCard = el("div", { class: "card", style: "display:grid;gap:12px" });
  (async () => {
    try {
      const [ch, pr] = await Promise.all([
        api.get("/api/analytics/channels"),
        api.get("/api/analytics/presets"),
      ]);
      opsCard.append(el("h2", { class: "t-title", style: "margin:0" }, "Operation"));
      opsCard.append(el("div", { class: "an-table" },
        el("div", { class: "an-row an-row-head" },
          el("div", {}, "Channel"), el("div", {}, "Pulled"),
          el("div", {}, "Clips made"), el("div", {}, "Posted"),
          el("div", {}, "Queued")),
        ...(ch.channels.length
          ? ch.channels.map((c) => el("div", { class: "an-row" },
              el("div", {}, c.name), el("div", {}, String(c.videos_pulled)),
              el("div", {}, String(c.clips_made)),
              el("div", {}, String(c.clips_posted)),
              el("div", {}, String(c.pending))))
          : [el("p", { class: "t-dim", style: "margin:8px 0" },
              "No approved channels yet.")])));
      opsCard.append(el("p", { class: "t-dim", style: "margin:0" },
        ch.accounts.map((a) =>
          `${a.account}: ${a.uploads_today}/${a.max_per_day} posted today`)
          .join(" · ")));
      if (pr.presets.length) {
        opsCard.append(el("h3", { class: "t-label", style: "margin:8px 0 0" },
          "Preset usage"));
        opsCard.append(barsSVG(pr.presets.map((p) =>
          ({ label: p.name, value: p.jobs }))));
      }
    } catch (e) {
      opsCard.append(el("p", { class: "t-dim", style: "margin:0" }, e.message));
    }
  })();

  let st;
  try {
    st = await api.get("/api/analytics/state");
  } catch (e) {
    body.replaceChildren(opsCard, scheduleCard,
      el("div", { class: "card" }, el("p", { class: "t-dim", style: "margin:0" }, e.message)));
    return;
  }

  const left = el("div", { class: "card", style: "display:grid;gap:16px" });
  const right = el("div", { class: "card card-flat", style: "display:grid;gap:12px" });
  body.replaceChildren(opsCard, scheduleCard, left, right);

  if (!st.configured) {
    left.append(
      el("h2", { class: "t-title", style: "margin:0" }, "One-time setup"),
      el("p", { class: "t-dim", style: "margin:0" },
        "Connect YouTube on the YouTube tab first — Analytics uses the same connection."),
      el("pre", { class: "setup-pre t-mono" },
        (st.setup_instructions || "").replace(/[#*`]/g, "")));
    return;
  }
  if (!st.authorized) {
    left.append(
      el("h2", { class: "t-title", style: "margin:0" }, "Connect your channel"),
      el("p", { class: "t-dim", style: "margin:0" },
        "Go to the YouTube tab and connect your channel — Analytics will show up here once you have."));
    return;
  }

  const overview = st.overview || {};
  const videos = st.videos || [];
  const recs = st.recommendations || [];

  const statTile = (label, stats) => el("div", {},
    el("div", { class: "t-hero-num" }, (stats?.views ?? 0).toLocaleString()),
    el("div", { class: "t-label" }, label),
    el("p", { class: "t-dim", style: "margin:4px 0 0;font-size:var(--text-sm)" },
      `${(stats?.avg_view_pct ?? 0)}% avg watch · +${stats?.subs_gained ?? 0} subs`));

  const refreshBtn = el("button", { class: "btn btn-sm", type: "button" }, "Refresh");
  refreshBtn.addEventListener("click", async () => {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "Refreshing…";
    try {
      await api.get("/api/analytics/state?refresh=1");
      navigate();
    } catch (e) {
      toast(e.message, "is-error");
      refreshBtn.disabled = false;
      refreshBtn.textContent = "Refresh";
    }
  });

  left.append(
    el("div", { class: "field-inline" },
      el("h2", { class: "t-title", style: "margin:0" }, "Channel overview"),
      refreshBtn),
    el("div", { class: "yt-stats" },
      statTile("Last 28 days", overview["28d"]),
      statTile("Last 90 days", overview["90d"])),
    el("p", { class: "t-dim", style: "margin:0;font-size:var(--text-xs)" },
      st.fetched_at ? `Updated ${new Date(st.fetched_at).toLocaleString()}` : ""));

  const sorted = [...videos].sort((a, b) =>
    new Date(a.publish_at || 0) - new Date(b.publish_at || 0));
  left.append(
    el("h2", { class: "t-title", style: "margin:0" }, "Views over time"),
    sparklineSVG(sorted.map((v) => ({ y: v.views }))));

  const byHour = {};
  for (const v of videos) {
    const h = istHourFromIso(v.publish_at);
    if (h === null) continue;
    if (!byHour[h]) byHour[h] = [];
    byHour[h].push(v.views);
  }
  const hourBars = Object.keys(byHour).map(Number).sort((a, b) => a - b)
    .map((h) => ({ label: `${h}:00`,
                  value: byHour[h].reduce((a, b) => a + b, 0) / byHour[h].length }));
  left.append(
    el("h2", { class: "t-title", style: "margin:0" }, "Performance by publish hour"),
    barsSVG(hourBars));

  right.append(el("h2", { class: "t-title", style: "margin:0" }, "Recommendations"));
  if (recs.length) recs.forEach((r) => right.append(analyticsRecCard(r)));
  else right.append(el("p", { class: "t-dim", style: "margin:0" },
    "No recommendations yet."));

  const tableCard = el("div", { class: "card uq-span", style: "display:grid;gap:12px" },
    el("h2", { class: "t-title", style: "margin:0" }, "Your uploaded clips"),
    videos.length
      ? el("div", { class: "an-table" },
          analyticsVideoHeadRow(),
          ...videos.map((v) => analyticsVideoRow(v)))
      : el("p", { class: "t-dim", style: "margin:0" },
          "Nothing uploaded through Frameza yet."));
  body.append(tableCard);
}

// ------------------------------------------------------------ settings ----

const PROVIDER_LABELS = {
  mock: "Offline — no API key needed",
  gemini: "Google Gemini (free key)",
  groq: "Groq (free key)",
  ollama: "Ollama (runs on this computer)",
  openrouter: "OpenRouter (free key)",
};

async function renderSettings() {
  const wrap = el("div", { class: "settings-grid" },
    el("div", { class: "skeleton", style: "height:320px" }),
    el("div", { class: "skeleton", style: "height:220px" }));
  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "Settings")),
        el("h1", { class: "t-display" }, "How Frameza works"))),
    wrap));

  let s, sys, upd, storage;
  try {
    [s, sys, upd, storage] = await Promise.all([
      api.get("/api/settings"), api.get("/api/system"), api.get("/api/update"),
      api.get("/api/storage").catch(() => null)]);
  } catch (e) {
    wrap.replaceChildren(el("div", { class: "card" },
      el("p", { class: "t-dim", style: "margin:0" }, e.message)));
    return;
  }

  const providerSel = el("select", { class: "select" },
    ...Object.entries(PROVIDER_LABELS).map(([v, label]) =>
      el("option", v === s.provider ? { value: v, selected: "" } : { value: v },
        label)));
  const computeSel = el("select", { class: "select" },
    el("option", { value: "auto" }, "Decide for me"),
    el("option", { value: "gpu" }, "Always use the graphics card"),
    el("option", { value: "cpu" }, "Processor only (slower, always works)"));
  computeSel.value = s.compute || "auto";
  const whisperSel = el("select", { class: "select" },
    el("option", { value: "" }, "Automatic"),
    ...["tiny", "base", "small", "medium", "large-v3"].map((m) =>
      el("option", { value: m }, m)));
  whisperSel.value = s.whisper_model || "";
  const gemIn = el("input", { class: "input", type: "text",
                              placeholder: "e.g. gemini-2.0-flash" });
  gemIn.value = s.gemini_model || "";
  const groqIn = el("input", { class: "input", type: "text" });
  groqIn.value = s.groq_model || "";
  const ollamaIn = el("input", { class: "input", type: "text" });
  ollamaIn.value = s.ollama_model || "";
  const nichesIn = el("input", { class: "input", type: "text",
                                 placeholder: "e.g. cooking, travel" });
  nichesIn.value = s.custom_niches || "";
  const approvalToggle = toggle(!!s.require_approval,
    "Require approval before upload");

  const saveBtn = el("button", { class: "btn btn-primary", type: "button" }, "Save");
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    try {
      await api.put("/api/settings", {
        compute: computeSel.value,
        whisper_model: whisperSel.value,
        provider: providerSel.value,
        gemini_model: gemIn.value,
        groq_model: groqIn.value,
        ollama_model: ollamaIn.value,
        custom_niches: nichesIn.value,
        require_approval: approvalToggle.input.checked,
      });
      toast("Saved — takes effect on the next run.", "is-ok");
    } catch (e) { toast(e.message, "is-error"); }
    saveBtn.disabled = false;
  });

  const accel = { full: "Full speed (graphics card)",
                  partial: "Partly accelerated",
                  cpu: "Processor only" }[sys.acceleration] || sys.acceleration;

  const updRow = el("div", { class: "field-inline" });
  const renderUpdate = (state) => {
    updRow.replaceChildren();
    if (state?.update_available) {
      const installBtn = el("button", { class: "btn btn-primary btn-sm",
                                        type: "button" },
        `Install ${state.latest}`);
      installBtn.addEventListener("click", async () => {
        installBtn.disabled = true;
        installBtn.textContent = "Installing…";
        try {
          const r = await api.post("/api/update/apply");
          toast(r.message || "Updated — restart Frameza to finish.", "is-ok");
        } catch (e) { toast(e.message, "is-error"); }
        installBtn.disabled = false;
      });
      updRow.append(
        el("span", { class: "badge badge-warn" }, `New: ${state.latest}`),
        installBtn);
    } else {
      const checkBtn = el("button", { class: "btn btn-sm", type: "button" },
        "Check for updates");
      checkBtn.addEventListener("click", async () => {
        checkBtn.disabled = true;
        checkBtn.textContent = "Checking…";
        try {
          const r = await api.post("/api/update/check");
          if (r.state?.update_available) renderUpdate(r.state);
          else toast("You're on the newest version.", "is-ok");
        } catch (e) { toast(e.message, "is-error"); }
        checkBtn.disabled = false;
        checkBtn.textContent = "Check for updates";
      });
      updRow.append(checkBtn);
    }
  };
  renderUpdate(upd.state);

  // Dynamic Ollama Model Manager (Stage 7)
  let modelsData = null;
  try {
    modelsData = await api.get("/api/models");
  } catch (err) {
    console.warn("Could not load /api/models:", err);
  }

  const modelSelect = el("select", { class: "select" });
  const modelInfoPanel = el("div", { class: "card card-flat", style: "display:grid;gap:8px;font-size:var(--text-sm);background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);padding:12px;border-radius:8px;" });

  const populateModelDropdown = (models, activeModel) => {
    modelSelect.replaceChildren();
    if (!models || models.length === 0) {
      modelSelect.append(el("option", { value: "" }, "No Ollama models found — is Ollama running?"));
      modelInfoPanel.replaceChildren(el("div", { class: "t-dim" }, "No local models detected. Run 'ollama pull qwen3.5:4b' in your terminal."));
      return;
    }
    models.forEach((m) => {
      const isSel = m.name === activeModel;
      const opt = el("option", isSel ? { value: m.name, selected: "" } : { value: m.name },
        `${m.name} [${m.category || "INSTALLED"}] - ${m.size_gb} GB`);
      modelSelect.append(opt);
    });
    updateModelInfo(activeModel, models);
  };

  const updateModelInfo = (modelName, models) => {
    const m = (models || []).find((x) => x.name === modelName);
    if (!m) {
      modelInfoPanel.replaceChildren(el("div", { class: "t-dim" }, `Model "${modelName}" is not currently installed.`));
      return;
    }
    const badgesRow = el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px;" },
      ...(m.badges || [m.category || "LOCAL"]).map((b) => {
        let badgeCls = "badge";
        if (b.includes("RECOMMENDED")) badgeCls = "badge badge-ok";
        else if (b.includes("HEAVY") || b.includes("NOT RECOMMENDED")) badgeCls = "badge badge-warn";
        else if (b.includes("VISION")) badgeCls = "badge badge-info";
        return el("span", { class: badgeCls }, b);
      })
    );
    modelInfoPanel.replaceChildren(
      el("div", { style: "display:flex;justify-content:space-between;align-items:center;" },
        el("strong", { class: "t-mono" }, m.name),
        el("span", { class: "t-dim" }, `${m.parameter_size || "Unknown params"} | ${m.size_gb} GB`)),
      badgesRow,
      el("div", { class: "t-dim", style: "font-size:var(--text-xs);" }, m.hardware_notes || m.description || "Local Ollama model.")
    );
  };

  const initialModels = modelsData?.installed_models || [];
  const currentActiveModel = s.ollama_model || modelsData?.active_model || "qwen3.5:4b";
  populateModelDropdown(initialModels, currentActiveModel);

  modelSelect.addEventListener("change", () => {
    ollamaIn.value = modelSelect.value;
    updateModelInfo(modelSelect.value, modelsData?.installed_models || []);
  });

  const refreshBtn = el("button", { class: "btn btn-sm", type: "button" }, "Refresh Models");
  refreshBtn.addEventListener("click", async () => {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "Scanning Ollama…";
    try {
      const res = await api.post("/api/models/refresh");
      modelsData = { installed_models: res.models, active_model: res.active_model };
      populateModelDropdown(res.models, modelSelect.value || res.active_model);
      toast(`Refreshed: ${res.count} Ollama model${res.count === 1 ? "" : "s"} detected.`, "is-ok");
    } catch (err) {
      toast(err.message, "is-error");
    }
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Refresh Models";
  });

  const switchModelBtn = el("button", { class: "btn btn-sm btn-primary", type: "button" }, "Switch Active Model");
  switchModelBtn.addEventListener("click", async () => {
    const target = modelSelect.value || ollamaIn.value;
    if (!target) return;
    switchModelBtn.disabled = true;
    switchModelBtn.textContent = "Switching…";
    try {
      await api.post("/api/models/active", { model: target });
      ollamaIn.value = target;
      toast(`Active model set to ${target}.`, "is-ok");
    } catch (err) {
      toast(err.message, "is-error");
    }
    switchModelBtn.disabled = false;
    switchModelBtn.textContent = "Switch Active Model";
  });

  const memActionsRow = el("div", { style: "display:flex;gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap;" },
    switchModelBtn,
    refreshBtn,
    el("button", {
      class: "btn btn-sm btn-ghost",
      type: "button",
      onclick: async (e) => {
        e.target.disabled = true;
        try {
          await api.post("/api/models/unload");
          toast("Model unloaded — GPU VRAM freed.", "is-ok");
        } catch (err) { toast(err.message, "is-error"); }
        e.target.disabled = false;
      }
    }, "Free GPU VRAM (Unload)"));

  const modelCard = el("div", { class: "card", style: "display:grid;gap:14px" },
    el("h2", { class: "t-title", style: "margin:0" }, "Local AI Model Manager (Ollama)"),
    el("div", { class: "t-dim", style: "font-size:var(--text-xs)" },
      "Frameza operates 100% locally with high-accuracy content understanding, multimodal vision, and zero cloud API fees. Context is bounded to 8,192 tokens to fit safely within your RTX 5050 8GB VRAM."),
    field("Select Installed Model", modelSelect),
    modelInfoPanel,
    memActionsRow);

  wrap.replaceChildren(
    modelCard,
    el("div", { class: "card", style: "display:grid;gap:14px" },
      el("h2", { class: "t-title", style: "margin:0" }, "AI & Highlight Engine"),
      field("Highlight Provider", providerSel),
      field("Gemini model (cloud optional)", gemIn),
      field("Groq model (cloud optional)", groqIn),
      field("Custom niches (comma-separated)", nichesIn)),
    el("div", { class: "card", style: "display:grid;gap:14px" },
      el("h2", { class: "t-title", style: "margin:0" }, "Processing & Speech"),
      field("Hardware Acceleration", computeSel),
      field("Speech Recognition (Whisper)", whisperSel),
      approvalToggle.node,
      el("div", { style: "margin-top:auto;padding-top:8px" }, saveBtn)),
    el("div", { class: "card card-flat", style: "display:grid;gap:12px" },
      el("h2", { class: "t-title", style: "margin:0" }, "This Computer"),
      kv("Version", upd.current || sys.version),
      kv("Speed", accel),
      kv("Processor cores", String(sys.cpu_count ?? "—")),
      storage ? kv("Clips on disk", storage.cleanable_bytes
        ? `${fmtBytes(storage.total_bytes)} · ${fmtBytes(storage.cleanable_bytes)} reclaimable`
        : fmtBytes(storage.total_bytes)) : null,
      el("div", { class: "hr", style: "margin:4px 0" }),
      updRow));
}

function kv(k, v) {
  return el("div", { class: "kv" },
    el("span", { class: "t-label" }, k),
    el("span", { class: "t-mono" }, v));
}

// ------------------------------------------------------------- history ----

async function renderHistory() {
  const grid = el("div", { class: "history-grid" },
    ...Array.from({ length: 4 }, () =>
      el("div", { class: "skeleton", style: "height:120px" })));

  const searchIn = el("input", {
    class: "input search-input", type: "text",
    placeholder: "Search past runs by title or file…",
  });
  const statusTabs = el("div", { class: "field-inline" });
  const filterRow = el("div", { class: "field-inline", style: "flex-wrap:wrap" });
  const backfillBtn = el("button", { class: "btn btn-ghost", type: "button" },
    "Tag older clips");

  const toolbar = el("div", { class: "history-toolbar" },
    searchIn,
    statusTabs);

  view.append(el("section", { class: "screen" },
    el("div", { class: "results-head" },
      el("div", {},
        el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px;" },
          backButton("#/", "← Back"),
          el("div", { class: "t-label", style: "margin:0;" }, "History")),
        el("h1", { class: "t-display" }, "Past runs")),
      el("div", { class: "field-inline" },
        backfillBtn,
        el("a", { class: "btn btn-ghost", href: "#/" }, "Make new clips"))),
    toolbar,
    filterRow,
    grid));

  let jobs = [];
  let activeNiche = null;
  let activeStatus = "all";
  let searchQuery = "";

  searchIn.addEventListener("input", () => {
    searchQuery = searchIn.value.trim().toLowerCase();
    renderGrid();
  });

  const renderStatusTabs = () => {
    const tabs = [
      { label: "All Runs", value: "all" },
      { label: "Completed", value: "done" },
      { label: "Failed", value: "error" },
    ];
    statusTabs.replaceChildren(...tabs.map((t) => {
      const b = el("button", {
        class: `btn btn-sm ${activeStatus === t.value ? "btn-primary" : "btn-ghost"}`,
        type: "button",
      }, t.label);
      b.addEventListener("click", () => {
        activeStatus = t.value;
        renderStatusTabs();
        renderGrid();
      });
      return b;
    }));
  };
  renderStatusTabs();

  const jobCard = (j) => {
    const when = j.name.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
    const date = when
      ? `${when[3]}.${when[2]}.${when[1]} ${when[4]}:${when[5]}` : j.created;

    const delBtn = el("button", { class: "btn btn-danger btn-sm", type: "button" }, "Delete");
    delBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const ok = await confirmDialog({
        title: `Delete project "${j.source || j.name}"?`,
        body: "This permanently deletes all clips, thumbnails, subtitles, and files for this run.",
        confirmLabel: "Delete Project",
      });
      if (!ok) return;
      delBtn.disabled = true;
      try {
        await api.del(`/api/jobs/${encodeURIComponent(j.name)}`);
        toast(`Deleted ${j.name}`, "is-ok");
        await load();
      } catch (err) {
        toast(err.message, "is-error");
        delBtn.disabled = false;
      }
    });

    const zipLink = el("a", {
      class: "btn btn-ghost btn-sm",
      href: `/api/jobs/${encodeURIComponent(j.name)}/zip`,
      onclick: (e) => e.stopPropagation(),
    }, "ZIP");

    const card = el("a", {
      class: "card card-hover history-card",
      href: `#/results/${encodeURIComponent(j.name)}`,
    },
      el("div", { class: "t-mono t-dim" }, date),
      el("div", { class: "history-src" }, j.source || j.name),
      el("div", { class: "field-inline", style: "flex-wrap:wrap" },
        el("span", { class: "badge" }, `${j.kept} of ${j.clip_count} clips kept`),
        ...(j.niches || []).map((n) => el("span", { class: "badge" }, n)),
        j.status && j.status !== "done"
          ? el("span", { class: "badge badge-warn" }, j.status)
          : null),
      el("div", { class: "field-inline", style: "margin-top:8px" }, zipLink, delBtn));

    return card;
  };

  const renderGrid = () => {
    let list = jobs;
    if (activeStatus === "done") {
      list = list.filter((j) => j.status === "done");
    } else if (activeStatus === "error") {
      list = list.filter((j) => j.status && j.status !== "done");
    }
    if (activeNiche) {
      list = list.filter((j) => (j.niches || []).includes(activeNiche));
    }
    if (searchQuery) {
      list = list.filter((j) =>
        (j.source || "").toLowerCase().includes(searchQuery)
        || j.name.toLowerCase().includes(searchQuery)
        || (j.niches || []).some((n) => n.toLowerCase().includes(searchQuery))
      );
    }

    if (!list.length) {
      grid.replaceChildren(el("div", { class: "card", style: "grid-column:1/-1" },
        el("p", { class: "t-dim", style: "margin:0" },
          searchQuery ? "No runs match your search query."
          : activeNiche ? "No runs with clips in that niche."
          : "No clips yet — paste a link on the first screen to start.")));
      return;
    }
    grid.replaceChildren(...list.map(jobCard));
  };

  const renderFilters = () => {
    const niches = [...new Set(jobs.flatMap((j) => j.niches || []))].sort();
    if (!niches.length) { filterRow.replaceChildren(); return; }
    const chip = (label, value) => {
      const b = el("button", {
        class: `btn btn-sm ${activeNiche === value ? "btn-primary" : "btn-ghost"}`,
        type: "button",
      }, label);
      b.addEventListener("click", () => {
        activeNiche = value;
        renderFilters();
        renderGrid();
      });
      return b;
    };
    filterRow.replaceChildren(chip("All Niches", null),
      ...niches.map((n) => chip(n, n)));
  };

  const load = async () => {
    try {
      jobs = (await api.get("/api/jobs")).jobs;
    } catch (e) {
      grid.replaceChildren(el("div", { class: "card" },
        el("p", { class: "t-dim", style: "margin:0" }, e.message)));
      return;
    }
    renderFilters();
    renderGrid();
  };

  backfillBtn.addEventListener("click", async () => {
    backfillBtn.disabled = true;
    backfillBtn.textContent = "Tagging…";
    try {
      const r = await api.post("/api/classify/backfill");
      toast(r.classified
        ? `Tagged ${r.classified} clip${r.classified === 1 ? "" : "s"} by niche.`
        : "Everything is already tagged.", "is-ok");
      await load();
    } catch (e) { toast(e.message, "is-error"); }
    backfillBtn.disabled = false;
    backfillBtn.textContent = "Tag older clips";
  });

  await load();
}

// ---------------------------------------------------------------- boot ----

navigate();

// quiet once-per-launch nudge when a newer version is available
api.get("/api/update").then((u) => {
  if (u.state?.update_available) {
    toast(`A new version (${u.state.latest}) is ready — install it in Settings.`);
  }
}).catch(() => { /* offline or updater unavailable — stay quiet */ });
