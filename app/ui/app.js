/* Handwrite UI. Talks to Python through window.pywebview.api in the app, or /api/* in a browser. */
(() => {
  const $ = (id) => document.getElementById(id);

  // ---- bridge --------------------------------------------------------------------------------
  // Everything goes over the local HTTP server; only the native save dialogs use the JS bridge.
  const DIALOGS = new Set(["export_pdf", "export_pngs"]);
  const api = new Proxy({}, {
    get: (_, name) => async (...args) => {
      if (DIALOGS.has(name) && window.pywebview && window.pywebview.api) return window.pywebview.api[name](...args);
      const r = await fetch("/api/" + name, { method: "POST", body: JSON.stringify(args) });
      return r.json();
    },
  });
  const ready = Promise.resolve();

  // ---- options -------------------------------------------------------------------------------
  const OPTIONS = {
    paper: { label: "纸张", choices: [["lined", "横线本"], ["narrow", "窄横线"], ["wide", "宽横线"], ["looseleaf", "活页纸"], ["legal", "双线草稿纸"], ["grid", "方格纸"], ["dotted", "点阵纸"], ["fourline", "四线三格"], ["squared", "大方格"], ["plain", "白纸"]] },
    texture: { label: "纸质", choices: [["white", "纯白"], ["cream", "米白"], ["ivory", "象牙"], ["yellow", "淡黄"], ["aged", "泛黄旧纸"], ["parchment", "羊皮纸"], ["recycled", "再生纸"], ["kraft", "牛皮纸"], ["gray", "灰纸"], ["bluish", "淡蓝"]] },
    font: { label: "英文字迹", choices: [] },
    zh_font: { label: "中文字迹", choices: [] },
    ink: { label: "笔色", choices: [["blue", "蓝", "#1e3487"], ["black", "黑", "#222228"], ["darkblue", "深蓝", "#162055"], ["blueblack", "蓝黑", "#1c2648"], ["gel", "中性黑", "#121216"], ["red", "红", "#ba2a2a"], ["green", "绿", "#1c603c"], ["purple", "紫", "#582c82"], ["brown", "棕", "#603e26"], ["pencil", "铅笔", "#5c5c62"]] },
    pressure: { label: "笔压", choices: [[-1, "轻"], [0, "正常"], [1, "重"]], start: 1 },
    level: { label: "潦草", choices: [[1, "1", "工整"], [2, "2", "自然"], [3, "3", "随意"], [4, "4", "放松"], [5, "5", "赶时间"], [6, "6", "潦草"], [7, "7", "很潦草"], [8, "8", "草草了事"], [9, "9", "龙飞凤舞"], [10, "10", "鬼画符"]], start: 1 },
    size: { label: "字号", choices: [[0.88, "小"], [1.0, "中"], [1.12, "大"]], start: 1 },
    slant: { label: "倾斜", choices: [[-0.1, "左斜"], [0, "正"], [0.08, "微右"], [0.16, "右斜"]], start: 1 },
    letter_gap: { label: "字距", choices: [[0.93, "紧"], [1.0, "正常"], [1.08, "松"]], start: 1 },
    word_gap: { label: "词距", choices: [[0.85, "紧"], [1.0, "正常"], [1.2, "松"]], start: 1 },
    slips: { label: "涂改", choices: [[0, "无"], [1.5, "偶尔"], [4, "常有"]], start: 1 },
    scan: { label: "扫描感", choices: [[0, "关", "打印用"], [1, "轻", "像认真拍的"], [2, "重", "像随手拍的"]], start: 0 },
  };
  const state = { index: {}, code: "", seed: 1, text: "", pages: [], page: 0, fileName: "作业" };

  function buildSteppers() {
    document.querySelectorAll(".stepper").forEach((el) => {
      const key = el.dataset.key, opt = OPTIONS[key];
      state.index[key] = opt.start || 0;
      el.innerHTML = `<span class="label">${opt.label}</span>
        <span class="ctl"><button class="arrow" data-d="-1">‹</button><span class="val"></span><button class="arrow" data-d="1">›</button></span>`;
      el.querySelectorAll(".arrow").forEach((b) => b.addEventListener("click", () => step(key, +b.dataset.d)));
      paint(key);
    });
  }
  function paint(key) {
    const el = document.querySelector(`.stepper[data-key="${key}"] .val`);
    const choice = OPTIONS[key].choices[state.index[key]];
    if (!choice) { el.textContent = "…"; return; }
    const [, name, extra] = choice;
    const swatch = key === "ink" ? `<span class="swatch" style="background:${extra}"></span>` : "";
    const note = key !== "ink" && extra ? `<small>${extra}</small>` : "";
    el.innerHTML = `<span>${swatch}${name}</span>${note}`;
  }
  function step(key, d) {
    const n = OPTIONS[key].choices.length;
    state.index[key] = (state.index[key] + d + n) % n;
    paint(key);
    saveSettings();
    scheduleRender();
  }
  // remembered between launches in a small settings file next to the app's other user data
  let saveTimer = null;
  function saveSettings() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const options = {};
      for (const key of Object.keys(OPTIONS)) {
        const choice = OPTIONS[key].choices[state.index[key]];
        if (choice) options[key] = choice[0];
      }
      api.save_settings({ code: state.code, options, auto_update: autoUpdate });
    }, 400);
  }
  function restoreOptions(saved) {
    for (const [key, value] of Object.entries(saved || {})) {
      const opt = OPTIONS[key];
      if (!opt) continue;
      const i = opt.choices.findIndex((c) => c[0] === value);
      if (i >= 0) { state.index[key] = i; paint(key); }
    }
  }
  function currentOpts() {
    const o = { code: state.code, seed: state.seed };
    for (const key of Object.keys(OPTIONS)) {
      const choice = OPTIONS[key].choices[state.index[key]];
      if (choice) o[key] = choice[0];
    }
    return o;
  }

  // ---- rendering -----------------------------------------------------------------------------
  let timer = null, rendering = false, pending = false, progressTimer = null;
  function scheduleRender(delay = 350) {
    if (!state.text.trim()) return;
    clearTimeout(timer);
    timer = setTimeout(render, delay);
  }
  let settingsLoaded = null;
  async function render() {
    if (rendering) { pending = true; return; }
    rendering = true;
    await settingsLoaded;
    const img = $("page");
    img.classList.add("dim");
    $("spinner").classList.remove("hidden");
    $("spinner-text").textContent = "正在书写…";
    $("btn-export").disabled = true;  // export only a finished version, never one half-written
    progressTimer = setInterval(async () => {
      const p = await api.progress();
      if (p.pages) $("spinner-text").textContent = `正在书写… 第 ${p.pages + 1} 页`;
    }, 600);
    try {
      const r = await api.render(state.text, currentOpts());
      if (r.error) toast("出错了：" + r.error);
      else if (!r.stale) {
        state.pages = r.pages;
        state.page = Math.min(state.page, Math.max(0, r.pages.length - 1));
        showPage();
        warnMissing(r.missing || []);
      }
    } finally {
      $("btn-export").disabled = pending || state.pages.length === 0;  // a queued render keeps it off
      clearInterval(progressTimer);
      rendering = false;
      img.classList.remove("dim");
      $("spinner").classList.add("hidden");
      if (pending) { pending = false; render(); }
    }
  }
  let lastMissing = "";
  function warnMissing(chars) {
    const key = chars.join("");
    if (key === lastMissing) return;  // say it once, not on every option change
    lastMissing = key;
    if (!chars.length) return;
    const shown = chars.slice(0, 20).join(" ") + (chars.length > 20 ? ` 等 ${chars.length} 个` : "");
    toast(`没有字体写得出这些字，会显示不对：${shown}`, 7000);
  }
  function showPage() {
    const n = state.pages.length;
    $("viewer").classList.toggle("hidden", n === 0 && view !== "text");
    if (n) $("page").src = state.pages[state.page];
    $("page-label").textContent = n ? `${state.page + 1} / ${n}` : "–";
    $("prev").disabled = state.page <= 0;
    $("next").disabled = state.page >= n - 1;
  }
  $("prev").addEventListener("click", () => { state.page--; showPage(); });
  $("next").addEventListener("click", () => { state.page++; showPage(); });
  document.addEventListener("keydown", (e) => {
    if (view !== "preview" || !state.pages.length) return;
    if (e.key === "ArrowLeft" && state.page > 0) { state.page--; showPage(); }
    if (e.key === "ArrowRight" && state.page < state.pages.length - 1) { state.page++; showPage(); }
  });

  // ---- views ---------------------------------------------------------------------------------
  let view = "preview";
  function setView(v) {
    view = v;
    document.querySelectorAll("#view-switch button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
    const changed = v === "preview" && $("text").value !== state.text;
    if (changed) { state.text = $("text").value; state.page = 0; }
    const has = !!state.text.trim();
    $("empty").classList.toggle("hidden", has || v === "text");
    $("editor").classList.toggle("hidden", v !== "text");
    $("viewer").classList.toggle("hidden", v !== "preview" || !has);
    if (changed) scheduleRender(0);
  }
  document.querySelectorAll("#view-switch button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));

  // ---- files ---------------------------------------------------------------------------------
  function readFile(file) {
    const ok = /\.(docx|pdf|txt|md)$/i.test(file.name);
    if (!ok) { toast("只支持 Word (.docx)、PDF 和 .txt 文件"); return; }
    if (!file.size) { toast("读不到文件内容，请先把它保存到电脑上再拖进来"); return; }
    const reader = new FileReader();
    reader.onerror = () => toast("读不到这个文件，试试「打开文件」按钮");
    reader.onload = async () => {
      const b64 = reader.result.split(",")[1];
      $("spinner-text").textContent = "正在读取…";
      const r = await api.load_file(file.name, b64);
      if (r.error) { toast("读不了这个文件：" + r.error); return; }
      state.text = r.text;
      state.fileName = file.name.replace(/\.[^.]+$/, "");
      $("text").value = r.text;
      state.page = 0;
      setView("preview");
      scheduleRender(0);
    };
    reader.readAsDataURL(file);
  }
  const pick = () => $("file-input").click();
  $("btn-open").addEventListener("click", pick);
  $("btn-open-2").addEventListener("click", pick);
  $("file-input").addEventListener("change", (e) => { if (e.target.files[0]) readFile(e.target.files[0]); e.target.value = ""; });

  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("drop-overlay").classList.remove("hidden"); });
  window.addEventListener("dragover", (e) => { e.preventDefault(); });
  window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("drop-overlay").classList.add("hidden"); } });
  window.addEventListener("drop", (e) => {
    e.preventDefault(); dragDepth = 0; $("drop-overlay").classList.add("hidden");
    const f = e.dataTransfer.files[0];
    if (f) readFile(f);
    else toast("这里没收到文件：请从文件夹或桌面拖入，聊天软件里的文件要先保存");
  });

  // ---- identity & export ---------------------------------------------------------------------
  $("btn-code").addEventListener("click", async () => {
    state.code = (await api.new_code()).code;
    $("code").value = state.code;
    saveSettings();
    scheduleRender(0);
  });
  async function applyTypedCode() {
    const code = (await api.check_code($("code").value)).code;
    if (!code) { $("code").value = state.code; return; }  // empty: keep the current ID
    $("code").value = code;
    if (code === state.code) return;
    state.code = code;
    saveSettings();
    scheduleRender(0);
  }
  $("code").addEventListener("change", applyTypedCode);
  $("code").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
  $("btn-shuffle").addEventListener("click", () => { state.seed = Math.floor(Math.random() * 1e6) + 1; scheduleRender(0); });
  $("btn-export").addEventListener("click", async () => {
    const r = await api.export_pdf(state.fileName + "-手写.pdf");
    if (r.saved) toast("已保存：" + r.saved);
    else if (r.download) { const a = document.createElement("a"); a.href = r.download + "?t=" + Date.now(); a.download = state.fileName + "-手写.pdf"; a.click(); }
    else if (r.error) toast(r.error);
  });

  let toastTimer = null;
  function toast(msg, ms = 3200) {
    const t = $("toast");
    t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
  }

  // ---- updates -------------------------------------------------------------------------------
  let autoUpdate = true;
  async function checkUpdate(manual) {
    if (manual) { $("btn-check").disabled = true; $("update-status").textContent = "正在检查…"; }
    const r = await api.check_update();
    $("btn-check").disabled = false;
    if (r.error) {
      if (manual) $("update-status").textContent = r.error;
      return;
    }
    if (r.newer) {
      $("btn-update").textContent = `更新到 ${r.latest}`;
      $("btn-update").classList.remove("hidden");
      $("update-status").textContent = `有新版本 ${r.latest}，点右上角「更新到 ${r.latest}」打开下载页，下载后替换旧文件即可，设置和笔迹都会保留。`;
      if (manual) toast(`有新版本 ${r.latest}`);
    } else {
      $("update-status").textContent = `已经是最新版本（${r.current}）。`;
      if (manual) toast("已经是最新版本");
    }
  }
  async function openRelease() {
    const r = await api.open_release();
    if (r.opened && !(window.pywebview && window.pywebview.api)) window.open(r.opened, "_blank");
  }
  $("btn-check").addEventListener("click", () => checkUpdate(true));
  $("btn-update").addEventListener("click", openRelease);
  $("auto-update").addEventListener("change", (e) => { autoUpdate = e.target.checked; saveSettings(); });

  // ---- start ---------------------------------------------------------------------------------
  buildSteppers();
  settingsLoaded = (async () => {
    await ready;
    const s = await api.settings();
    OPTIONS.font.choices = s.fonts.map((f) => [f.file, f.name, f.note]);
    state.index.font = 0;
    paint("font");
    OPTIONS.zh_font.choices = s.zh_fonts.map((f) => [f.file, f.name, f.note]);
    state.index.zh_font = 0;
    paint("zh_font");
    restoreOptions(s.options);
    state.code = s.code; state.seed = s.seed;
    $("code").value = s.code;
    $("version").textContent = `Handwrite ${s.version}`;
    autoUpdate = s.auto_update !== false;
    $("auto-update").checked = autoUpdate;
    if (autoUpdate) setTimeout(() => checkUpdate(false), 1500);  // after the window is up
  })();
  setView("preview");
})();
