// ===========================
// ビザ検定 - 管理画面ロジック（RAG出題）
// 受験者一覧（名前＋単元別進捗・クリア数降順）と、名前クリックでの個別履歴（正答率のみ）。
// ===========================

(function () {
  // URLのファイル名からトークンを推定（admin-Kp7vQm2xRt.html → Kp7vQm2xRt）
  function detectAdminToken() {
    const m = location.pathname.match(/admin-([a-zA-Z0-9_-]+)\.html$/);
    return m ? m[1] : "";
  }

  const ADMIN_TOKEN = detectAdminToken();

  const loadingEl = document.getElementById("loading");
  const contentEl = document.getElementById("content");
  const errorArea = document.getElementById("error-area");
  const errorMsg = document.getElementById("error-message");
  const usersArea = document.getElementById("users-area");
  const historyCard = document.getElementById("history-card");
  const historyTitle = document.getElementById("history-title");
  const historyArea = document.getElementById("history-area");
  const challengesArea = document.getElementById("challenges-area");
  const challengeStatusFilter = document.getElementById("challenge-status-filter");
  const challengesModal = document.getElementById("challenges-modal");
  const challengesClose = document.getElementById("challenges-close");
  const inboxBtn = document.getElementById("inbox-btn");
  const inboxBadge = document.getElementById("inbox-badge");
  let allChallenges = [];   // 取得した全チャレンジ（バッジ件数とフィルタ表示の元データ）

  // escapeHtml / fmtDate / levelLabel は common.js に共通化

  // 正答率(%) → 色クラス（管理画面の閾値: 満点=緑 / 61〜99=黄 / 60以下=赤）
  function rateClass(pct) {
    if (pct >= 100) return "high"; // 緑
    if (pct >= 61) return "mid";   // 黄
    return "low";                  // 赤
  }

  function showError(msg) {
    loadingEl.style.display = "none";
    contentEl.style.display = "none";
    errorArea.style.display = "block";
    errorMsg.textContent = msg;
  }

  async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  async function load() {
    if (!ADMIN_TOKEN) {
      showError("管理トークンを検出できませんでした。URLパスを確認してください。");
      return;
    }
    try {
      const data = await fetchJson(`/api/${ADMIN_TOKEN}/admin/users`);
      renderUsers(data.users || []);
      loadingEl.style.display = "none";
      contentEl.style.display = "block";
      loadChallenges();
    } catch (e) {
      showError(`データの取得に失敗しました: ${e.message}`);
    }
  }

  function progressChip(u) {
    // 単元別進捗チップ。クリア済みは緑のチップ色で示すため文言は付けない（クライアント要望）。
    // 未クリアは満点回数を N/3 表記のみで示す（「通算」の語は付けない）。
    if (u.cleared) {
      return `<span class="prog-chip prog-chip--cleared">${escapeHtml(u.unit_name)}（${levelLabel(u.level)}）</span>`;
    }
    return `<span class="prog-chip">${escapeHtml(u.unit_name)}（${levelLabel(u.level)}）${u.perfect_count}/${u.required}</span>`;
  }

  function renderUsers(users) {
    if (users.length === 0) {
      usersArea.innerHTML = '<div class="empty">受験データはまだありません</div>';
      return;
    }
    const rows = users.map((u) => {
      const chips = (u.units || []).map(progressChip).join(" ");
      return `<tr>
        <td><button type="button" class="user-link" data-user-id="${u.user_id}" data-user="${escapeHtml(u.username)}">${escapeHtml(u.username)}</button></td>
        <td class="cleared-num">${u.cleared_count}</td>
        <td class="last-taken">${u.last_taken_at ? fmtDate(u.last_taken_at) : '<span class="muted">−</span>'}</td>
        <td class="prog-cell">${chips || '<span class="muted">進捗なし</span>'}</td>
        <td><button type="button" class="btn btn-secondary pw-reset" data-user-id="${u.user_id}" data-user="${escapeHtml(u.username)}"
              style="padding: 4px 8px; font-size: 12px; white-space: nowrap;">PW再設定</button></td>
      </tr>`;
    }).join("");
    usersArea.innerHTML = `
      <table class="data">
        <thead><tr><th>受験者</th><th>クリア単元数</th><th>直近の受験</th><th>単元別進捗</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    usersArea.querySelectorAll(".user-link").forEach((btn) => {
      btn.addEventListener("click", () => loadHistory(btn.dataset.userId, btn.dataset.user));
    });
    // パスワード再設定（メール送信基盤なし＝管理者が新パスワードを決めて本人へ伝える運用）
    usersArea.querySelectorAll(".pw-reset").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const newPw = prompt(`${btn.dataset.user} さんの新しいパスワード（8文字以上）を入力してください：`);
        if (newPw === null) return;
        if (newPw.length < 8) { alert("8文字以上にしてください。"); return; }
        try {
          const res = await fetch(`/api/${ADMIN_TOKEN}/admin/users/${btn.dataset.userId}/password`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_password: newPw }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || "再設定に失敗しました");
          alert(`再設定しました。新しいパスワードを ${btn.dataset.user} さんへ伝えてください。\n（本人の既存ログインは全て無効になります）`);
        } catch (e) {
          alert("失敗: " + e.message);
        }
      });
    });
  }

  async function loadHistory(userId, displayName) {
    historyCard.style.display = "block";
    historyTitle.textContent = `受験履歴：${displayName}`;
    historyArea.innerHTML = '<div class="loading">読み込み中…</div>';
    historyCard.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const data = await fetchJson(
        `/api/${ADMIN_TOKEN}/admin/history?user_id=${encodeURIComponent(userId)}`
      );
      renderHistory(data.attempts || [], data.required || 3);
    } catch (e) {
      historyArea.innerHTML = `<div class="empty">履歴の取得に失敗しました: ${escapeHtml(e.message)}</div>`;
    }
  }

  function renderHistory(attempts, requiredCount) {
    if (attempts.length === 0) {
      historyArea.innerHTML = '<div class="empty">この受験者の履歴はありません</div>';
      return;
    }
    // 正答率の数値は表示せず、記録1行全体を正答率バンドで色付けする
    // （満点=緑 / 61〜99%=黄 / 60%以下=赤）。
    // 満点の行はレベルの右に（N/3）を付け、何回目の満点かを示す（クライアント要望）。
    const rows = attempts.map((a) => {
      const kind = a.unit_name
        ? escapeHtml(a.unit_name)
        : escapeHtml(levelLabel(a.level));
      const perfectNo = a.perfect_no
        ? `（${a.perfect_no}/${requiredCount}）`
        : "";
      return `<tr class="hist-row hist-row--${rateClass(a.pct)}">
        <td>${fmtDate(a.taken_at)}</td>
        <td>${kind}</td>
        <td>${levelLabel(a.level)}${perfectNo}</td>
      </tr>`;
    }).join("");
    historyArea.innerHTML = `
      <table class="data hist-table">
        <thead><tr><th>受験日時</th><th>単元</th><th>レベル</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted hist-legend">行の色＝正答率（<span class="lg lg-high">緑：満点</span>／<span class="lg lg-mid">黄：61〜99%</span>／<span class="lg lg-low">赤：60%以下</span>）</p>
    `;
  }

  // ===== 異議申し立て（チャレンジ） =====
  // 全件を取得して保持し、バッジ（未処理件数）と一覧（状態フィルタ）を更新する。
  async function loadChallenges() {
    challengesArea.innerHTML = '<div class="loading">読み込み中…</div>';
    try {
      const data = await fetchJson(`/api/${ADMIN_TOKEN}/admin/challenges`);
      allChallenges = data.challenges || [];
      updateInboxBadge();
      renderFilteredChallenges();
    } catch (e) {
      challengesArea.innerHTML = `<div class="empty">取得に失敗しました: ${escapeHtml(e.message)}</div>`;
    }
  }

  // 未処理（open）件数を受信箱バッジに反映する。
  function updateInboxBadge() {
    const open = allChallenges.filter((c) => c.status === "open").length;
    if (open > 0) {
      inboxBadge.textContent = open;
      inboxBadge.hidden = false;
      inboxBtn.classList.add("has-unread");
    } else {
      inboxBadge.hidden = true;
      inboxBtn.classList.remove("has-unread");
    }
  }

  // 保持データを状態フィルタで絞り込んで描画する（再取得しない）。
  function renderFilteredChallenges() {
    const status = challengeStatusFilter ? challengeStatusFilter.value : "";
    const items = status ? allChallenges.filter((c) => c.status === status) : allChallenges;
    renderChallenges(items);
  }

  // スナップショット（設問・選択肢・正答・受験者の解答・解説）を整形する
  function renderSnapshot(s) {
    if (!s || !s.question) return "";
    let body = `<div class="ch-question">${escapeHtml(s.question)}</div>`;
    if (s.type === "fill_in") {
      const correct = Array.isArray(s.correct_answers) ? s.correct_answers.join(" / ") : "";
      const ua = Array.isArray(s.user_text_answers) ? s.user_text_answers.join(" / ") : "";
      body += `<div class="ch-meta">正解例：${escapeHtml(correct)}</div>`;
      body += `<div class="ch-meta">受験者の解答：${escapeHtml(ua) || "（未記入）"}（${s.is_correct ? "正解" : "不正解"}）</div>`;
    } else {
      const choices = Array.isArray(s.choices) ? s.choices : [];
      const opts = choices.map((c, i) => {
        const marks = [];
        if (i === s.correct_choice) marks.push("正答");
        if (i === s.user_choice) marks.push("受験者");
        const tag = marks.length ? `（${marks.join("・")}）` : "";
        return `<li class="${i === s.correct_choice ? "ch-correct" : ""}">${escapeHtml(c)}${tag}</li>`;
      }).join("");
      body += `<ul class="ch-choices">${opts}</ul>`;
      body += `<div class="ch-meta">判定：${s.is_correct ? "正解" : "不正解"}</div>`;
    }
    if (s.explanation) body += `<div class="ch-meta">解説：${escapeHtml(s.explanation)}</div>`;
    return body;
  }

  const RES_LABEL = { correct: "正解に訂正", void: "ノーカウント" };

  function renderChallenges(items) {
    if (items.length === 0) {
      challengesArea.innerHTML = '<div class="empty">該当するチャレンジはありません</div>';
      return;
    }
    const cards = items.map((ch) => {
      const statusLabel = ch.status_label || CHALLENGE_STATUS_LABEL[ch.status] || ch.status;
      const noAttempt = ch.attempt_id ? "" :
        '<div class="ch-meta" style="color:var(--danger);">※受験未確定（中断）。採点には反映されません。</div>';

      // 入力欄＋操作（未処理＝返信・メモ＋3択、処理済＝メモ＋クローズ、終端＝表示のみ）
      const msgRO = ch.admin_message
        ? `<div class="ch-meta">受験者への返信：${escapeHtml(ch.admin_message)}</div>` : "";
      const resRO = ch.resolution
        ? `<div class="ch-meta">対応：${escapeHtml(RES_LABEL[ch.resolution] || ch.resolution)}</div>` : "";
      let panel = "";
      if (ch.status === "open") {
        panel = `
          <label class="ch-field-label">受験者への返信（任意・本人のマイページに表示）</label>
          <textarea class="ch-msg" data-id="${ch.id}" rows="2"></textarea>
          <label class="ch-field-label">対応メモ（内部・任意）</label>
          <textarea class="ch-note" data-id="${ch.id}" rows="2"></textarea>
          <div class="ch-actions">
            <button type="button" class="btn ch-correct" data-id="${ch.id}">正解に訂正</button>
            <button type="button" class="btn ch-void" data-id="${ch.id}">ノーカウント</button>
            <button type="button" class="btn btn-secondary ch-reject" data-id="${ch.id}">却下</button>
          </div>`;
      } else if (ch.status === "accepted") {
        panel = `
          ${resRO}${msgRO}
          <label class="ch-field-label">対応メモ（内部・任意）</label>
          <textarea class="ch-note" data-id="${ch.id}" rows="2">${escapeHtml(ch.admin_note || "")}</textarea>
          <div class="ch-actions">
            <button type="button" class="btn btn-secondary ch-close" data-id="${ch.id}">クローズ（是正完了）</button>
          </div>`;
      } else {
        // closed / rejected：表示のみ
        const noteRO = ch.admin_note
          ? `<div class="ch-meta">対応メモ：${escapeHtml(ch.admin_note)}</div>` : "";
        panel = `${resRO}${msgRO}${noteRO}`;
      }

      return `<div class="ch-card ch-card--${ch.status}">
        <div class="ch-head">
          <span class="ch-status ch-status--${ch.status}">${escapeHtml(statusLabel)}</span>
          <span class="muted">${escapeHtml(ch.applicant)} ／ ${escapeHtml(ch.unit_name)}（${levelLabel(ch.level)}）／ ${fmtDate(ch.created_at)}</span>
        </div>
        <div class="ch-reason"><strong>チャレンジ：</strong>${escapeHtml(ch.reason || "")}</div>
        ${renderSnapshot(ch.snapshot)}
        ${noAttempt}
        ${panel}
      </div>`;
    }).join("");
    challengesArea.innerHTML = cards;

    challengesArea.querySelectorAll(".ch-correct").forEach((b) =>
      b.addEventListener("click", () => resolveChallenge(b.dataset.id, "accept", "correct")));
    challengesArea.querySelectorAll(".ch-void").forEach((b) =>
      b.addEventListener("click", () => resolveChallenge(b.dataset.id, "accept", "void")));
    challengesArea.querySelectorAll(".ch-reject").forEach((b) =>
      b.addEventListener("click", () => resolveChallenge(b.dataset.id, "reject")));
    challengesArea.querySelectorAll(".ch-close").forEach((b) =>
      b.addEventListener("click", () => closeChallenge(b.dataset.id)));
  }

  // カード内の入力欄の値を取得（空は null）。
  function cardValue(id, cls) {
    const el = challengesArea.querySelector(`.${cls}[data-id="${id}"]`);
    if (!el) return null;
    const v = el.value.trim();
    return v || null;
  }

  // 認容（resolution: correct/void）／却下。確認ダイアログは出さず即時処理する。
  async function resolveChallenge(id, action, resolution) {
    const body = {
      admin_message: cardValue(id, "ch-msg"),
      admin_note: cardValue(id, "ch-note"),
    };
    if (action === "accept") body.resolution = resolution;
    try {
      const res = await fetch(`/api/${ADMIN_TOKEN}/admin/challenges/${id}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "処理に失敗しました");
      // 採点に反映されなかった（確定受験が見つからない等）場合は無音にせず明示する。
      if (data.warning) alert("注意: " + data.warning);
      loadChallenges();   // 一覧・バッジが更新されることが処理完了の合図
    } catch (e) {
      alert("失敗: " + e.message);
    }
  }

  async function closeChallenge(id) {
    try {
      const res = await fetch(`/api/${ADMIN_TOKEN}/admin/challenges/${id}/close`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ admin_note: cardValue(id, "ch-note") }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "処理に失敗しました");
      loadChallenges();
    } catch (e) {
      alert("失敗: " + e.message);
    }
  }

  if (challengeStatusFilter) {
    challengeStatusFilter.addEventListener("change", renderFilteredChallenges);
  }

  // 受信箱ボタン: 一覧モーダルを開く（開くたびに最新化）。
  function openChallenges() {
    challengesModal.hidden = false;
    loadChallenges();
  }
  function closeChallenges() {
    challengesModal.hidden = true;
  }
  if (inboxBtn) inboxBtn.addEventListener("click", openChallenges);
  if (challengesClose) challengesClose.addEventListener("click", closeChallenges);
  if (challengesModal) {
    challengesModal.addEventListener("click", (e) => {
      if (e.target === challengesModal) closeChallenges();  // 背景クリックで閉じる
    });
  }

  load();

  // ==================== ソース管理 ====================
  const pdfInput = document.getElementById("pdf-input");
  const uploadBtn = document.getElementById("upload-btn");
  const uploadStatus = document.getElementById("upload-status");
  const filesList = document.getElementById("files-list");

  async function loadSourceFiles() {
    try {
      const data = await fetchJson(`/api/${ADMIN_TOKEN}/admin/source/files`);
      filesList.innerHTML = "";
      if (data.files.length === 0) {
        filesList.innerHTML = "<p style=\"font-size:13px; color:#999; margin:0;\">ファイルがアップロードされていません。</p>";
        return;
      }
      data.files.forEach(file => {
        const div = document.createElement("div");
        div.style.cssText = "padding:8px; background:#f9f9f9; border-radius:4px; border-left:3px solid #2196F3; display:flex; justify-content:space-between; align-items:center;";
        const date = new Date(file.modified);
        const dateStr = date.toLocaleString("ja-JP");
        
        const infoDiv = document.createElement("div");
        infoDiv.innerHTML = `
          <strong>${file.name}</strong><br>
          <span style="font-size:12px; color:#666;">
            サイズ: ${file.size_display} | 更新: ${dateStr}
          </span>
        `;
        
        const deleteBtn = document.createElement("button");
        deleteBtn.textContent = "削除";
        deleteBtn.style.cssText = "padding:4px 12px; background:#f44336; color:white; border:none; border-radius:3px; cursor:pointer; font-size:12px;";
        deleteBtn.addEventListener("click", async () => {
          if (!confirm(`${file.name} を削除しますか？`)) return;
          try {
            const res = await fetch(`/api/${ADMIN_TOKEN}/admin/source/delete?filename=${encodeURIComponent(file.name)}`, {
              method: "DELETE",
            });
            if (!res.ok) {
              throw new Error("削除に失敗しました");
            }
            await loadSourceFiles();
          } catch (e) {
            alert(`削除エラー: ${e.message}`);
          }
        });
        
        div.appendChild(infoDiv);
        div.appendChild(deleteBtn);
        filesList.appendChild(div);
      });
    } catch (e) {
      filesList.innerHTML = `<p style="color:red; font-size:13px;">読み込みエラー: ${e.message}</p>`;
    }
  }

  if (uploadBtn) {
    uploadBtn.addEventListener("click", async () => {
      if (!pdfInput.files || pdfInput.files.length === 0) {
        uploadStatus.textContent = "ファイルを選択してください。";
        uploadStatus.style.color = "red";
        return;
      }

      const file = pdfInput.files[0];
      if (!file.name.endsWith(".pdf")) {
        uploadStatus.textContent = "PDF ファイルのみアップロード可能です。";
        uploadStatus.style.color = "red";
        return;
      }

      uploadBtn.disabled = true;
      uploadStatus.textContent = "アップロード中…";
      uploadStatus.style.color = "#2196F3";

      try {
        const formData = new FormData();
        formData.append("file", file);

        const res = await fetch(`/api/${ADMIN_TOKEN}/admin/source/upload`, {
          method: "POST",
          body: formData,
        });

        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data.detail || "アップロードに失敗しました。");
        }

        uploadStatus.textContent = `✓ アップロード完了 (${data.pages} ページ)`;
        uploadStatus.style.color = "green";
        pdfInput.value = "";
        await loadSourceFiles();
      } catch (e) {
        uploadStatus.textContent = `✗ エラー: ${e.message}`;
        uploadStatus.style.color = "red";
      } finally {
        uploadBtn.disabled = false;
      }
    });
  }

  // 初期化時にソースファイルを読み込む
  loadSourceFiles();
})();
