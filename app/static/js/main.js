/* 1914.fun 前端交互 — 投票 / 评论 / 举报 / Markdown 预览 / 投稿预览 */
(function () {
  "use strict";
  const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || "";

  function post(url, data) {
    const body = data instanceof FormData ? data : new URLSearchParams(data || {});
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRF-Token": CSRF },
      body: body,
      credentials: "same-origin",
    }).then(async (r) => {
      const ct = r.headers.get("content-type") || "";
      if (!ct.includes("application/json")) {
        throw new Error("服务器错误 (" + r.status + ")");
      }
      const json = await r.json();
      if (!r.ok || json.ok === false) throw new Error(json.error || "操作失败");
      return json;
    });
  }

  /* ---------- 投票（列表内联 + 详情大按钮） ---------- */
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".vote-btn");
    if (!btn) return;
    e.preventDefault();
    const type = btn.dataset.type, id = btn.dataset.id;
    if (!type || !id) return;
    if (btn.dataset.requireLogin === "1") {
      location.href = "/login?next=" + encodeURIComponent(location.pathname);
      return;
    }
    btn.disabled = true;
    post("/api/vote/" + type + "/" + id)
      .then((r) => {
        document.querySelectorAll('.vote-btn[data-type="' + type + '"][data-id="' + id + '"]')
          .forEach((b) => b.classList.toggle("voted", r.voted));
        document.querySelectorAll('.vote-count[data-type="' + type + '"][data-id="' + id + '"]')
          .forEach((c) => (c.textContent = r.count));
      })
      .catch((err) => alert(err.message))
      .finally(() => (btn.disabled = false));
  });

  /* ---------- 举报对话框 ---------- */
  let reportDialog = null;
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".report-btn");
    if (!btn) return;
    e.preventDefault();
    if (btn.dataset.requireLogin === "1") {
      location.href = "/login?next=" + encodeURIComponent(location.pathname);
      return;
    }
    if (!reportDialog) {
      reportDialog = document.createElement("dialog");
      reportDialog.className = "report-dialog";
      reportDialog.innerHTML =
        '<h3 style="margin-top:0;color:var(--brass-bright)">举报内容</h3>' +
        '<form method="dialog"><input type="hidden" name="target_type" id="rp-type">' +
        '<input type="hidden" name="target_id" id="rp-id">' +
        '<textarea id="rp-reason" placeholder="请说明举报理由（必填）" style="width:100%;min-height:90px"></textarea>' +
        '<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:12px">' +
        '<button type="button" class="btn btn-sm" id="rp-cancel">取消</button>' +
        '<button type="button" class="btn btn-danger btn-sm" id="rp-submit">提交举报</button></div></form>';
      document.body.appendChild(reportDialog);
      reportDialog.querySelector("#rp-cancel").onclick = () => reportDialog.close();
      reportDialog.querySelector("#rp-submit").onclick = function () {
        const reason = reportDialog.querySelector("#rp-reason").value.trim();
        if (!reason) { alert("请填写举报理由"); return; }
        post("/api/report", {
          target_type: reportDialog.querySelector("#rp-type").value,
          target_id: reportDialog.querySelector("#rp-id").value,
          reason: reason,
        }).then((r) => {
          reportDialog.close();
          alert(r.message || "举报已提交");
        }).catch((err) => alert(err.message));
      };
    }
    reportDialog.querySelector("#rp-type").value = btn.dataset.type;
    reportDialog.querySelector("#rp-id").value = btn.dataset.id;
    reportDialog.querySelector("#rp-reason").value = "";
    reportDialog.showModal();
  });

  /* ---------- 评论：回复 / 编辑 / 删除 ---------- */
  document.addEventListener("click", function (e) {
    const replyBtn = e.target.closest(".comment-reply");
    if (replyBtn) {
      e.preventDefault();
      const cid = replyBtn.dataset.id;
      const form = document.querySelector("#comment-form");
      const box = document.querySelector('[data-comment-root="' + cid + '"] .comment-form-slot');
      if (box && form) {
        box.appendChild(form);
        form.querySelector("textarea").focus();
        form.querySelector("input[name=parent_id]").value = cid;
        form.querySelector("#cancel-reply").style.display = "";
      }
    }
    const cancelBtn = e.target.closest("#cancel-reply");
    if (cancelBtn) {
      e.preventDefault();
      const form = document.querySelector("#comment-form");
      document.querySelector("#comment-form-slot-main").appendChild(form);
      form.querySelector("input[name=parent_id]").value = "";
      cancelBtn.style.display = "none";
    }
    const editBtn = e.target.closest(".comment-edit");
    if (editBtn) {
      e.preventDefault();
      const root = document.querySelector('[data-comment-root="' + editBtn.dataset.id + '"]');
      const bodyDiv = root.querySelector(".comment-body");
      if (root.querySelector(".edit-area")) return;
      const orig = bodyDiv.innerHTML;
      const ta = document.createElement("textarea");
      ta.className = "edit-area";
      ta.style.cssText = "width:100%;min-height:70px;background:var(--bg-2);color:var(--text);border:1px solid var(--line);border-radius:4px;padding:8px";
      ta.value = editBtn.dataset.raw || "";
      const save = document.createElement("button");
      save.className = "btn btn-sm btn-primary";
      save.textContent = "保存";
      const cancel = document.createElement("button");
      cancel.className = "btn btn-sm";
      cancel.textContent = "取消";
      const bar = document.createElement("div");
      bar.style.cssText = "display:flex;gap:8px;margin-top:6px";
      bar.append(save, cancel);
      bodyDiv.replaceWith(ta); ta.after(bar);
      cancel.onclick = () => { ta.replaceWith(bodyDiv); bar.remove(); };
      save.onclick = () => {
        post("/api/comment/" + editBtn.dataset.id + "/edit", { body: ta.value })
          .then((r) => { bodyDiv.innerHTML = r.html; ta.replaceWith(bodyDiv); bar.remove();
            editBtn.dataset.raw = ta.value; })
          .catch((err) => alert(err.message));
      };
      void orig;
    }
    const delBtn = e.target.closest(".comment-delete");
    if (delBtn) {
      e.preventDefault();
      if (!confirm("确定删除这条评论吗？")) return;
      post("/api/comment/" + delBtn.dataset.id + "/delete")
        .then(() => {
          const root = document.querySelector('[data-comment-root="' + delBtn.dataset.id + '"]');
          if (root) root.outerHTML = '<div class="comment deleted">（已删除）</div>';
        })
        .catch((err) => alert(err.message));
    }
  });

  const commentForm = document.querySelector("#comment-form");
  if (commentForm) {
    commentForm.addEventListener("submit", function (e) {
      e.preventDefault();
      const ta = commentForm.querySelector("textarea");
      const type = commentForm.dataset.type, id = commentForm.dataset.id;
      if (!ta.value.trim()) return;
      const submitBtn = commentForm.querySelector("button[type=submit]");
      submitBtn.disabled = true;
      post("/api/comment/" + type + "/" + id, new FormData(commentForm))
        .then((r) => {
          location.reload();
        })
        .catch((err) => { alert(err.message); submitBtn.disabled = false; });
    });
  }

  /* ---------- Markdown 实时预览 ---------- */
  const mdFields = document.querySelectorAll("[data-md-preview]");
  mdFields.forEach(function (field) {
    const pane = document.querySelector(field.dataset.mdPreview);
    if (!pane) return;
    let timer = null;
    field.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(() => {
        post("/api/preview", { body: field.value })
          .then((r) => { pane.innerHTML = r.html; pane.classList.add("active"); })
          .catch(() => {});
      }, 350);
    });
  });

  /* ---------- 卡牌性质徽章：点击显示详情 ---------- */
  try {
    var tagJson = document.getElementById("card-tag-descs");
    window.CARD_TAG_DESCS = tagJson ? JSON.parse(tagJson.textContent) : {};
  } catch (err) { window.CARD_TAG_DESCS = {}; }
  var tagDialog = null;
  document.addEventListener("click", function (e) {
    var badge = e.target.closest(".card-tag-badge");
    if (!badge) return;
    e.preventDefault();
    e.stopPropagation();
    var tag = badge.dataset.tag || "";
    var desc = (window.CARD_TAG_DESCS || {})[tag] || "";
    if (!tagDialog) {
      tagDialog = document.createElement("dialog");
      tagDialog.className = "report-dialog";
      document.body.appendChild(tagDialog);
    }
    tagDialog.innerHTML =
      '<h3 style="margin:0 0 10px;color:var(--brass-bright)">' + tag + '</h3>' +
      '<p style="margin:0 0 14px">' + desc + '</p>' +
      '<form method="dialog" style="text-align:right">' +
      '<button class="btn btn-sm btn-primary">知道了</button></form>';
    tagDialog.showModal();
  });

  /* ---------- 移动端抽屉菜单 ---------- */
  var menuToggle = document.getElementById("menu-toggle");
  if (menuToggle) {
    var header = document.querySelector(".site-header");
    menuToggle.addEventListener("click", function (e) {
      e.stopPropagation();
      header.classList.toggle("open");
      menuToggle.textContent = header.classList.contains("open") ? "✕" : "☰";
    });
    // 点击站点链接后自动收起
    header.addEventListener("click", function (e) {
      var link = e.target.closest("a");
      if (link && header.classList.contains("open")) {
        header.classList.remove("open");
        menuToggle.textContent = "☰";
      }
    });
    // 点击页面其他区域收起
    document.addEventListener("click", function (e) {
      if (header.classList.contains("open") && !header.contains(e.target)) {
        header.classList.remove("open");
        menuToggle.textContent = "☰";
      }
    });
  }

  /* ---------- 后台批量操作：全选 / 计数 / 提交确认 ---------- */
  document.querySelectorAll("form[id$='-batch']").forEach(function (form) {
    var selectAll = form.querySelector("#select-all");
    var countEl = document.getElementById("sel-count");
    var boxes = function () {
      return Array.from(document.querySelectorAll('input[name="ids"][form="' + form.id + '"]'));
    };
    var update = function () {
      var all = boxes();
      var n = all.filter(function (b) { return b.checked; }).length;
      if (countEl) countEl.textContent = n ? "已选 " + n + " 项" : "";
      if (selectAll) selectAll.checked = all.length > 0 && n === all.length;
    };
    if (selectAll) {
      selectAll.addEventListener("change", function () {
        boxes().forEach(function (b) { b.checked = selectAll.checked; });
        update();
      });
    }
    document.addEventListener("change", function (e) {
      if (e.target && e.target.name === "ids" && e.target.getAttribute("form") === form.id) update();
    });
    form.addEventListener("submit", function (e) {
      var n = boxes().filter(function (b) { return b.checked; }).length;
      if (!n) { e.preventDefault(); alert("请先勾选要操作的行"); return; }
      var sel = form.querySelector("select[name=action]");
      if (sel && !sel.value) { e.preventDefault(); alert("请选择要执行的批量操作"); return; }
      var label = sel ? sel.options[sel.selectedIndex].text : "";
      if (!confirm("确定对选中的 " + n + " 项执行「" + label + "」？此操作可能不可恢复。")) {
        e.preventDefault();
      }
    });
  });

  /* ---------- 卡牌投稿：实时卡面预览 ---------- */
  const cardForm = document.querySelector("#card-form");
  if (cardForm) {
    const update = function () {
      const g = (n) => cardForm.querySelector('[name="' + n + '"]');
      const val = (n) => (g(n) ? g(n).value : "");
      const preview = document.querySelector("#gcard-preview");
      if (!preview) return;
      preview.querySelector(".gcard-name").textContent = val("name") || "卡牌名称";
      preview.querySelector(".cost-g").innerHTML = "⚙ " + (val("cost_g") || "0");
      preview.querySelector(".cost-k").innerHTML = "✦ " + (val("cost_k") || "0");
      preview.querySelector(".stat-atk").textContent = val("attack") || "0";
      preview.querySelector(".stat-def").textContent = val("defense") || "0";
      const rangeEl = preview.querySelector(".gcard-range");
      const vR = val("vision_range"), aR = val("attack_range");
      const R = window.RANGES || {};
      if (vR || aR) {
        rangeEl.textContent = "视野: " + (R[vR] || vR || "—") + " · 射程: " + (R[aR] || aR || "—");
      } else { rangeEl.textContent = ""; }
      const abWrap = preview.querySelector(".gcard-abilities");
      abWrap.innerHTML = "";
      cardForm.querySelectorAll('input[name="abilities"]:checked').forEach((cb) => {
        const chip = document.createElement("span");
        chip.className = "ability-chip";
        const lvlInput = cardForm.querySelector('[name="ability_level_' + cb.value + '"]');
        chip.textContent = cb.value + (lvlInput && lvlInput.value > 1 ? lvlInput.value : "");
        abWrap.appendChild(chip);
      });
      const flavorEl = preview.querySelector(".gcard-flavor");
      flavorEl.textContent = val("flavor_text") || " ";
      preview.querySelector(".nation").textContent = (val("nation") || "neutral").toUpperCase();
      const cls = val("unit_class");
      preview.querySelector(".cls").textContent = cls || "—";
      const cardEl = preview.querySelector(".gcard");
      cardEl.className = "gcard rarity-" + (val("rarity") || "common") + (val("type") === "order" ? " type-order" : "");
      const artBox = preview.querySelector(".gcard-art");
      const file = g("art") && g("art").files && g("art").files[0];
      if (file) {
        let img = artBox.querySelector("img");
        if (!img) { img = document.createElement("img"); artBox.appendChild(img); }
        img.src = URL.createObjectURL(file);
        const ph = artBox.querySelector(".placeholder-icon");
        if (ph) ph.style.display = "none";
      }
    };
    cardForm.addEventListener("input", update);
    cardForm.addEventListener("change", update);
    update();
  }
})();
