/* 1914.fun 网络测速 — 延迟 / 下载 / 上传（当前设备 → 本站链路实测）
   - 延迟：连发 10 次微型 JSON 请求，取最小值（展示）/ 平均值 / 相邻抖动
   - 下载：持续约 10 秒循环拉取服务端随机数据流（ReadableStream 逐块计数，
     块大小按已测速度自适应，单块 ≤ 8MB，与服务端钳制一致）
   - 上传：持续约 10 秒循环 POST 随机数据（XHR upload.onprogress 实时计数，
     服务端读入即弃；已登录会话按 /api 规则带 X-CSRF-Token）
   服务端接口与防滥用（流量预算 / 频率 / 并发限制）见 app/speedtest.py */
(function () {
  "use strict";

  const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const $ = (id) => document.getElementById(id);

  const PING_COUNT = 10;
  const TEST_MS = 10000;          // 下载 / 上传各持续时长
  const MB = 1024 * 1024;
  const REQ_MAX = 32 * MB;        // 单请求数据量上限（与服务端一致）
  const MIN_CHUNK = 256 * 1024;   // 自适应块下限
  const FIRST_CHUNK = MB;         // 首个请求块大小
  const CHUNK_TARGET_MS = 2000;   // 自适应目标：每个请求承载约 2 秒的传输量
                                  // （请求速率 ≈ 0.5/s，远离全站每秒 30 请求限流）
  const REQ_GAP_MS = 50;          // 相邻请求间隔下限（双保险）
  const PING_TIMEOUT = 4000;
  const UPLOAD_STALL_MS = 12000;  // 上传进度停滞多久视为无响应

  const ui = {
    start: $("st-start"), error: $("st-error"), live: $("st-live"),
    liveLabel: $("st-live-label"), liveSpeed: $("st-live-speed"),
    liveUnit: $("st-live-unit"), liveBar: $("st-live-bar"),
    liveTransferred: $("st-live-transferred"),
    phases: { ping: $("st-phase-ping"), dl: $("st-phase-dl"), ul: $("st-phase-ul") },
    val: { ping: $("st-val-ping"), dl: $("st-val-dl"), ul: $("st-val-ul") },
    sub: { ping: $("st-sub-ping"), dl: $("st-sub-dl"), ul: $("st-sub-ul") },
    badge: { ping: $("st-badge-ping"), dl: $("st-badge-dl"), ul: $("st-badge-ul") },
    sum: { dl: $("st-sum-dl"), ul: $("st-sum-ul"), time: $("st-sum-time"),
           panel: $("st-summary") },
  };

  let runSeq = 0;        // 递增令牌：点击"停止"即令牌失效
  let aborters = [];     // 进行中的 AbortController / XHR
  let running = false;
  let win = [];          // 实时速度的 1 秒滑动窗口 [time, bytes]

  class Stopped extends Error {}

  /* 服务端 429/503 且响应体不是 JSON（全站限流的纯文本响应）时给出可读文案 */
  function friendlyError(e) {
    const m = /服务器错误 \((\d+)\)/.exec(e.message);
    if (m && m[1] === "429") return "请求过于频繁（全站限流），请一分钟后再试";
    if (m && m[1] === "503") return "服务器繁忙，请稍后再试";
    return e.message;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fmtNum = (x) => (x >= 100 ? String(Math.round(x)) : x.toFixed(1));
  const fmtMB = (bytes) => {
    const mb = bytes / MB;
    return (mb >= 100 ? Math.round(mb) : mb.toFixed(1)) + " MB";
  };

  /* 结果档位：延迟 / 下载 / 上传（阈值分别适配） */
  const QUALITY = {
    ping: (ms) => ms < 50 ? ["顺畅", "badge-olive"] : ms < 150 ? ["可用", "badge-brass"] : ["偏慢", "badge-red"],
    dl: (v) => v >= 50 ? ["顺畅", "badge-olive"] : v >= 10 ? ["可用", "badge-brass"] : ["偏慢", "badge-red"],
    ul: (v) => v >= 20 ? ["顺畅", "badge-olive"] : v >= 5 ? ["可用", "badge-brass"] : ["偏慢", "badge-red"],
  };

  function setPhase(name) {
    ["ping", "dl", "ul"].forEach((k) => {
      ui.phases[k].className =
        "badge speed-phase" + (k === name ? " speed-phase-active" : "");
    });
  }

  function setBadge(key, cls, text) {
    const b = ui.badge[key];
    b.style.display = "";
    b.className = "badge " + cls;
    b.textContent = text;
  }

  function resetCard(key) {
    ui.val[key].textContent = "—";
    ui.sub[key].textContent = "测试中…";
    ui.badge[key].style.display = "none";
  }

  function showLive(label) {
    ui.live.style.display = "";
    ui.liveLabel.textContent = label;
    ui.liveSpeed.textContent = "—";
    ui.liveBar.style.width = "0%";
    ui.liveTransferred.textContent = "0 MB";
    win = [];
  }

  function liveUpdate(t0, bytes, totalMs) {
    const now = performance.now();
    win.push([now, bytes]);
    while (win.length > 2 && now - win[0][0] > 1000) win.shift();
    const first = win[0], last = win[win.length - 1];
    const dt = (last[0] - first[0]) / 1000;
    if (dt > 0.2 && last[1] >= first[1]) {
      ui.liveSpeed.textContent = fmtNum((last[1] - first[1]) * 8 / dt / 1e6);
    }
    ui.liveBar.style.width = Math.min(100, (now - t0) / totalMs * 100) + "%";
    ui.liveTransferred.textContent = fmtMB(bytes);
  }

  /* ---------- 延迟 ---------- */

  function fetchJSON(url, timeoutMs) {
    const ac = new AbortController();
    aborters.push(ac);
    const timer = setTimeout(() => ac.abort(), timeoutMs);
    return fetch(url, { cache: "no-store", signal: ac.signal })
      .then(async (r) => {
        const j = await r.json().catch(() => null);
        if (!r.ok || !j || j.ok !== true) {
          throw new Error((j && j.error) || "服务器错误 (" + r.status + ")");
        }
        return j;
      })
      .finally(() => clearTimeout(timer));
  }

  async function measurePing(check) {
    setPhase("ping");
    showLive("请求延迟");
    ui.liveUnit.textContent = "ms";
    const samples = [];
    for (let i = 0; i < PING_COUNT; i++) {
      check();
      const t0 = performance.now();
      await fetchJSON("/api/speedtest/ping?r=" + Math.random(), PING_TIMEOUT);
      samples.push(performance.now() - t0);
      ui.liveSpeed.textContent = String(Math.round(Math.min(...samples)));
      ui.liveBar.style.width = Math.round((i + 1) / PING_COUNT * 100) + "%";
      ui.liveTransferred.textContent = (i + 1) + " / " + PING_COUNT + " 次";
    }
    const min = Math.min(...samples);
    const avg = samples.reduce((a, b) => a + b, 0) / samples.length;
    let jitter = 0;
    for (let i = 1; i < samples.length; i++) {
      jitter += Math.abs(samples[i] - samples[i - 1]);
    }
    jitter /= samples.length - 1;
    return { min, avg, jitter };
  }

  function fillPing(p) {
    ui.val.ping.textContent = String(Math.round(p.min));
    ui.sub.ping.textContent = "平均 " + Math.round(p.avg) + " ms · 抖动 ±" +
      Math.round(p.jitter) + " ms";
    const [text, cls] = QUALITY.ping(p.min);
    setBadge("ping", cls, text);
  }

  /* ---------- 下载 ---------- */

  async function measureDownload(check) {
    setPhase("dl");
    showLive("下载速度");
    ui.liveUnit.textContent = "Mbps";
    const t0 = performance.now();
    let total = 0, size = FIRST_CHUNK;
    while (performance.now() - t0 < TEST_MS) {
      check();
      const ac = new AbortController();
      aborters.push(ac);
      try {
        const r = await fetch("/api/speedtest/download?size=" + size +
                              "&r=" + Math.random(),
                              { cache: "no-store", signal: ac.signal });
        if (!r.ok) {
          const j = await r.json().catch(() => null);
          throw new Error((j && j.error) || "服务器错误 (" + r.status + ")");
        }
        if (!r.body) throw new Error("当前浏览器不支持流式下载测速");
        const reader = r.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          total += value.byteLength;
          liveUpdate(t0, total, TEST_MS);
          if (performance.now() - t0 >= TEST_MS) break;
        }
        if (performance.now() - t0 >= TEST_MS) {
          try { await reader.cancel(); } catch (e) { void e; }
        }
      } catch (e) {
        check();          // 用户停止导致的 AbortError → 转 Stopped
        throw e;
      }
      await gapUntil(t0);
      // 下一个块 ≈ 按当前平均速度传约 2 秒的量（钳制在 MIN_CHUNK..REQ_MAX）
      const secs = Math.max(0.05, (performance.now() - t0) / 1000);
      size = Math.round(Math.min(REQ_MAX,
        Math.max(MIN_CHUNK, total / secs * (CHUNK_TARGET_MS / 1000))));
    }
    return { bytes: total, seconds: Math.max(0.05, (performance.now() - t0) / 1000) };
  }

  /* 请求间隔下限：上一个请求完成得太快时稍作等待，
     避免高速链路下请求频率逼近全站每秒 30 请求限流 */
  let lastReqStart = 0;
  async function gapUntil() {
    const sinceStart = performance.now() - lastReqStart;
    if (sinceStart < REQ_GAP_MS) {
      await sleep(REQ_GAP_MS - sinceStart);
    }
    lastReqStart = performance.now();
  }

  /* ---------- 上传 ---------- */

  let masterBlob = null;
  function uploadBlob() {
    if (masterBlob) return masterBlob;
    const one = new Uint8Array(MB);
    for (let off = 0; off < MB; off += 65536) {
      const seg = one.subarray(off, Math.min(off + 65536, MB));
      if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(seg);
      else for (let i = 0; i < seg.length; i++) seg[i] = (Math.random() * 256) | 0;
    }
    masterBlob = new Blob(Array.from({ length: 8 }, () => one));
    return masterBlob;
  }

  function uploadOnce(blob, onProgress, isStopped) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      aborters.push(xhr);
      let lastLoaded = 0;
      let stall = setTimeout(() => xhr.abort(), UPLOAD_STALL_MS);
      const clearStall = () => { clearTimeout(stall); };
      xhr.open("POST", "/api/speedtest/upload?r=" + Math.random(), true);
      if (CSRF) xhr.setRequestHeader("X-CSRF-Token", CSRF);
      xhr.responseType = "json";
      xhr.upload.onprogress = (e) => {
        lastLoaded = e.loaded;
        clearStall();
        stall = setTimeout(() => xhr.abort(), UPLOAD_STALL_MS);
        onProgress(e.loaded);
      };
      xhr.onload = () => {
        clearStall();
        const j = xhr.response;
        if (xhr.status === 200 && j && j.ok) resolve(j.bytes || lastLoaded);
        else reject(new Error((j && j.error) || "服务器错误 (" + xhr.status + ")"));
      };
      xhr.onerror = () => { clearStall(); reject(new Error("上传连接中断")); };
      xhr.onabort = () => {
        clearStall();
        // 用户主动停止：已传出的字节照常计入；无响应停滞：报错终止
        if (isStopped()) resolve(lastLoaded);
        else reject(new Error("上传长时间无响应，已中断"));
      };
      xhr.send(blob);
    });
  }

  async function measureUpload(isStopped, check) {
    setPhase("ul");
    showLive("上传速度");
    ui.liveUnit.textContent = "Mbps";
    const t0 = performance.now();
    const blob = uploadBlob();
    let total = 0, size = FIRST_CHUNK;
    while (performance.now() - t0 < TEST_MS) {
      check();
      await gapUntil(t0);
      const got = await uploadOnce(blob.slice(0, size), (loaded) => {
        liveUpdate(t0, total + loaded, TEST_MS);
      }, isStopped);
      total += got;
      const secs = Math.max(0.05, (performance.now() - t0) / 1000);
      size = Math.round(Math.min(REQ_MAX,
        Math.max(MIN_CHUNK, total / secs * (CHUNK_TARGET_MS / 1000))));
    }
    return { bytes: total, seconds: Math.max(0.05, (performance.now() - t0) / 1000) };
  }

  function fillSpeed(key, r) {
    const mbps = r.bytes * 8 / r.seconds / 1e6;
    ui.val[key].textContent = fmtNum(mbps);
    ui.sub[key].textContent = "≈ " + fmtNum(mbps / 8) + " MB/s · 用时 " +
      Math.round(r.seconds) + " 秒";
    const [text, cls] = QUALITY[key](mbps);
    setBadge(key, cls, text);
  }

  /* ---------- 主流程 ---------- */

  async function runTest() {
    const seq = ++runSeq;
    const isStopped = () => seq !== runSeq;
    const check = () => { if (isStopped()) throw new Stopped(); };

    running = true;
    aborters = [];
    ui.error.style.display = "none";
    ui.sum.panel.style.display = "none";
    ["ping", "dl", "ul"].forEach(resetCard);
    ui.start.textContent = "停止测速";
    ui.start.classList.remove("btn-brass");
    ui.start.classList.add("btn-danger");
    const t0 = performance.now();
    const usage = { dl: 0, ul: 0 };
    let ok = false;

    try {
      const ping = await measurePing(check);
      fillPing(ping);
      hideLiveLater(check);

      const dl = await measureDownload(check);
      usage.dl = dl.bytes;
      fillSpeed("dl", dl);
      hideLiveLater(check);

      const ul = await measureUpload(isStopped, check);
      usage.ul = ul.bytes;
      fillSpeed("ul", ul);

      setPhase(null);
      ok = true;
      ui.sum.dl.textContent = fmtMB(usage.dl);
      ui.sum.ul.textContent = fmtMB(usage.ul);
      ui.sum.time.textContent = Math.round((performance.now() - t0) / 1000) + " 秒";
      ui.sum.panel.style.display = "";
    } catch (e) {
      setPhase(null);
      const aborted = e instanceof Stopped || e.name === "AbortError" || isStopped();
      if (!aborted) {
        ui.error.textContent = "测速中断：" + friendlyError(e);
        ui.error.style.display = "";
      }
      // 中断时尚未轮到的阶段标为"未完成"，避免残留"测试中…"
      ["ping", "dl", "ul"].forEach((k) => {
        if (ui.sub[k].textContent === "测试中…") ui.sub[k].textContent = "未完成";
      });
    } finally {
      aborters.forEach((a) => { try { a.abort(); } catch (e) { void e; } });
      ui.live.style.display = "none";
      running = false;
      ui.start.textContent = "重新测试";
      ui.start.classList.add("btn-brass");
      ui.start.classList.remove("btn-danger");
      if (!ok) ui.sum.panel.style.display = "none";
    }
  }

  function hideLiveLater(check) {
    ui.live.style.display = "none";
    return sleep(300).then(check);
  }

  ui.start.addEventListener("click", () => {
    if (running) {
      runSeq++;           // 令牌失效 → 各阶段在下一个检查点停止
      aborters.forEach((a) => { try { a.abort(); } catch (e) { void e; } });
      return;
    }
    runTest();
  });
})();
