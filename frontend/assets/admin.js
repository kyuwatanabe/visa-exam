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
  const historyTitle = document.getElementById("history-title");
  const historyArea = document.getElementById("history-area");
  const challengesArea = document.getElementById("challenges-area");
  const challengeStatusFilter = document.getElementById("challenge-status-filter");
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
      renderUsers(data.users || [], data.total_by_level || {});
      loadingEl.style.display = "none";
      contentEl.style.display = "block";
      // 起動時にチャレンジ件数を取得してバッジを更新（一覧描画も兼ねる）
      loadChallenges();
      challengesLoaded = true;
    } catch (e) {
      showError(`データの取得に失敗しました: ${e.message}`);
    }
  }

  // 級ごとの進捗をボックスで表す。級の単元数ぶんのボックスを並べ、クリア済みの数だけ塗る。
  // 級は色で区別（初級=緑 / 中級=黄 / 上級=赤）。3級を横に並べる。
  function levelBars(clearedByLevel, totalByLevel) {
    const cbl = clearedByLevel || {};
    const groups = LEVELS.map((lv) => {
      const total = totalByLevel[lv] || 0;
      if (!total) return "";
      const done = Math.min(cbl[lv] || 0, total);
      let boxes = "";
      for (let i = 0; i < total; i++) {
        boxes += `<span class="pbox pbox--${lv} ${i < done ? "is-on" : ""}"></span>`;
      }
      return `<span class="pboxgroup">${boxes}</span>`;
    });
    return `<div class="uprog">${groups.join("")}</div>`;
  }

  function renderUsers(users, totalByLevel) {
    if (users.length === 0) {
      usersArea.innerHTML = '<div class="empty">受験データはまだありません</div>';
      return;
    }
    const rows = users.map((u) => {
      return `<tr>
        <td><button type="button" class="user-link" data-user-id="${u.user_id}" data-user="${escapeHtml(u.username)}">${escapeHtml(u.username)}</button></td>
        <td class="last-taken">${u.last_taken_at ? fmtDate(u.last_taken_at) : '<span class="muted">−</span>'}</td>
        <td class="prog-cell">${levelBars(u.cleared_by_level, totalByLevel)}</td>
        <td><button type="button" class="btn btn-secondary pw-reset" data-user-id="${u.user_id}" data-user="${escapeHtml(u.username)}"
              style="padding: 4px 8px; font-size: 12px; white-space: nowrap;">PW再設定</button></td>
      </tr>`;
    }).join("");
    usersArea.innerHTML = `
      <table class="data">
        <thead><tr><th>受験者</th><th>直近の受験</th>
          <th>進捗（<span class="lg-dot lg-dot--b"></span>初級・<span class="lg-dot lg-dot--i"></span>中級・<span class="lg-dot lg-dot--a"></span>上級）</th>
          <th></th></tr></thead>
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

  let currentDetail = null;   // { userId, displayName } 個人記録画面で表示中の受験者

  async function loadHistory(userId, displayName) {
    currentDetail = { userId, displayName };
    showUserDetail();   // 一覧を隠して個人記録画面に切り替え
    historyTitle.textContent = `${displayName} さんの記録`;
    historyArea.innerHTML = '<div class="loading">読み込み中…</div>';
    historyArea.dataset.userId = userId;
    window.scrollTo({ top: 0, behavior: "smooth" });
    try {
      const data = await fetchJson(
        `/api/${ADMIN_TOKEN}/admin/history?user_id=${encodeURIComponent(userId)}`
      );
      renderUserDetail(userId, data);
    } catch (e) {
      historyArea.innerHTML = `<div class="empty">記録の取得に失敗しました: ${escapeHtml(e.message)}</div>`;
    }
  }

  // 受験者マイページと同じ2部構成（単元別進捗＋受験履歴）で表示する。
  function renderUserDetail(userId, data) {
    const progressHtml = renderUnitProgress(data.units_progress || []);
    const historyHtml = renderHistory(data.attempts || [], data.required || 3, userId);
    historyArea.innerHTML = progressHtml + historyHtml;
    // 削除ボタンのハンドラ
    historyArea.querySelectorAll(".hist-del").forEach((btn) => {
      btn.addEventListener("click", () => deleteAttempt(userId, btn.dataset.id));
    });
  }

  // 単元別進捗：行＝単元、列＝初級/中級/上級 のマトリクス。
  // どの単元のどの級まで進んでいるかが一目で分かるようにする。
  function renderUnitProgress(unitsProgress) {
    if (!unitsProgress.length) {
      return '<h3 style="margin:4px 0 10px;">単元別進捗</h3><div class="empty">進捗はまだありません</div>';
    }
    // level -> {unit_id -> cell} に組み替え、単元の一覧（順序）も作る
    const levels = unitsProgress.map((s) => s.level);
    const unitOrder = [];
    const byUnit = {};   // unit_id -> {name, cells:{level->u}}
    unitsProgress.forEach((sec) => {
      (sec.units || []).forEach((u) => {
        if (!byUnit[u.id]) { byUnit[u.id] = { name: u.name, cells: {} }; unitOrder.push(u.id); }
        byUnit[u.id].cells[sec.level] = u;
      });
    });
    const head = `<tr><th>単元</th>${levels.map((l) => `<th>${levelLabel(l)}</th>`).join("")}</tr>`;
    const rows = unitOrder.map((uid) => {
      const row = byUnit[uid];
      const tds = levels.map((l) => {
        const u = row.cells[l];
        if (!u) return '<td class="pcell">−</td>';
        const n = u.attempt_count || 0;
        const cnt = `<span class="pcount">受験${n}回</span>`;
        if (u.cleared) return `<td class="pcell"><span class="pstat pstat--done">クリア</span>${cnt}</td>`;
        if ((u.perfect_count || 0) > 0)
          return `<td class="pcell"><span class="pstat pstat--prog">${u.perfect_count}/${u.required_streak}</span>${cnt}</td>`;
        return `<td class="pcell"><span class="pstat pstat--none">未達 0/${u.required_streak}</span>${cnt}</td>`;
      }).join("");
      return `<tr><td class="pcell-unit">${escapeHtml(row.name)}</td>${tds}</tr>`;
    }).join("");
    return `<h3 style="margin:4px 0 10px;">単元別進捗</h3>
      <table class="data progress-matrix">
        <thead>${head}</thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted" style="font-size:12px; margin:8px 0 0;">
        <span class="pstat pstat--done">クリア</span> 必要回数の満点到達　
        <span class="pstat pstat--prog">n/m</span> 満点n回（必要m回）　
        <span class="pstat pstat--none">未達</span> 満点なし
      </p>`;
  }

  function renderHistory(attempts, requiredCount, userId) {
    if (!attempts.length) {
      return '<h3 style="margin:20px 0 6px;">受験履歴</h3><div class="empty">受験履歴はありません</div>';
    }
    const rows = attempts.map((a) => {
      const kind = a.unit_name ? escapeHtml(a.unit_name) : "−";
      const perfectNo = a.perfect_no ? `（${a.perfect_no}/${requiredCount}）` : "";
      const pill = `<span class="score-pill ${pillClass(a.pct)}">${a.score} / ${a.total}</span>`;
      return `<tr>
        <td>${fmtDate(a.taken_at)}</td>
        <td>${kind}</td>
        <td>${levelLabel(a.level)}${perfectNo}</td>
        <td>${pill}</td>
        <td><button type="button" class="btn btn-secondary hist-del" data-id="${a.id}"
              style="padding:3px 8px; font-size:12px; white-space:nowrap;">削除</button></td>
      </tr>`;
    }).join("");
    return `<h3 style="margin:20px 0 6px;">受験履歴</h3>
      <table class="data hist-table">
        <thead><tr><th>受験日時</th><th>単元</th><th>レベル</th><th>得点</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  async function deleteAttempt(userId, attemptId) {
    if (!confirm("この受験記録を削除しますか？\n削除すると単元の進捗（満点回数・クリア状況）も再計算されます。")) return;
    try {
      const res = await fetch(
        `/api/${ADMIN_TOKEN}/admin/attempts/${attemptId}?user_id=${encodeURIComponent(userId)}`,
        { method: "DELETE" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "削除に失敗しました");
      // 個人記録画面（進捗＋履歴）を最新化。続けて一覧も裏で更新。
      const name = (currentDetail && currentDetail.displayName) || "";
      await loadHistory(userId, name);
      load();   // 一覧の進捗チップも更新（画面は個人記録のまま）
    } catch (e) {
      alert("削除に失敗: " + e.message);
    }
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

  // 未処理（open）件数をメニューのバッジに反映する。
  function updateInboxBadge() {
    const open = allChallenges.filter((c) => c.status === "open").length;
    if (!inboxBadge) return;
    if (open > 0) {
      inboxBadge.textContent = open;
      inboxBadge.hidden = false;
    } else {
      inboxBadge.hidden = true;
    }
  }

  // 保持データを状態フィルタで絞り込んで描画する（再取得しない）。
  function renderFilteredChallenges() {
    const status = challengeStatusFilter ? challengeStatusFilter.value : "";
    const items = status ? allChallenges.filter((c) => c.status === status) : allChallenges;
    renderChallenges(items);
  }

  // スナップショット（設問・選択肢・正答・受験者の解答・解説）を整形する
  function renderSnapshot(s, opts) {
    if (!s || !s.question) return "";
    const editable = opts && opts.editable;   // open のとき選択肢別の判定UIを出す
    const chId = opts && opts.chId;
    let body = `<div class="ch-question">${escapeHtml(s.question)}</div>`;
    if (s.type === "fill_in") {
      const correct = Array.isArray(s.correct_answers) ? s.correct_answers.join(" / ") : "";
      const ua = Array.isArray(s.user_text_answers) ? s.user_text_answers.join(" / ") : "";
      body += `<div class="ch-meta">正解例：${escapeHtml(correct)}</div>`;
      body += `<div class="ch-meta">受験者の解答：${escapeHtml(ua) || "（未記入）"}（${s.is_correct ? "正解" : "不正解"}）</div>`;
    } else if (s.type === "multi") {
      // 上級: 各選択肢の正誤・受験者の選択・チャレンジ対象を表示。対象には判定セレクトを出す。
      const choices = Array.isArray(s.choices) ? s.choices : [];
      const uc = Array.isArray(s.user_choices) ? s.user_choices : [];
      const cc = Array.isArray(s.correct_choices) ? s.correct_choices : [];
      const targets = Array.isArray(s.target_choices) ? s.target_choices : [];
      const marks = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ";
      const rows = choices.map((c, i) => {
        const isCorrect = cc.includes(i);
        const picked = uc.includes(i);
        const handledOk = picked === isCorrect;
        const desc = isCorrect ? "◯正しい記述" : "×誤った記述";
        const you = picked ? "選択した" : "選ばなかった";
        const okmark = handledOk ? '<span class="ch-ok">対応◯</span>' : '<span class="ch-ng">対応×</span>';
        const isTarget = targets.includes(i);
        const targetTag = isTarget ? '<span class="ch-target-tag">チャレンジ対象</span>' : "";
        let ruleSel = "";
        if (editable && isTarget) {
          ruleSel = `
            <select class="ch-ruling" data-ch="${chId}" data-idx="${i}">
              <option value="reject">却下（変更なし）</option>
              <option value="void">ノーカウント</option>
              <option value="correct">正解扱い</option>
            </select>`;
        }
        return `<li class="ch-mrow ${isTarget ? "is-target" : ""}">
          <span class="ch-mmark">${marks[i] || (i + 1)}</span>
          <span class="ch-mtext">${escapeHtml(c)}
            <span class="ch-meta2">${desc}／${you}／${okmark} ${targetTag}</span>
          </span>
          ${ruleSel}
        </li>`;
      }).join("");
      body += `<ul class="ch-mchoices">${rows}</ul>`;
      body += `<div class="ch-meta">現在の判定：${s.is_correct ? "正解" : "不正解"}</div>`;
    } else {
      const choices = Array.isArray(s.choices) ? s.choices : [];
      const opts2 = choices.map((c, i) => {
        const marks = [];
        if (i === s.correct_choice) marks.push("正答");
        if (i === s.user_choice) marks.push("受験者");
        const tag = marks.length ? `（${marks.join("・")}）` : "";
        return `<li class="${i === s.correct_choice ? "ch-correct" : ""}">${escapeHtml(c)}${tag}</li>`;
      }).join("");
      body += `<ul class="ch-choices">${opts2}</ul>`;
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
      const isMulti = ch.snapshot && ch.snapshot.type === "multi";
      if (ch.status === "open") {
        const actions = isMulti
          ? `<div class="ch-actions">
               <button type="button" class="btn ch-apply-multi" data-id="${ch.id}">この判定で再採点</button>
               <button type="button" class="btn btn-secondary ch-reject" data-id="${ch.id}">却下</button>
             </div>
             <p class="muted" style="font-size:12px; margin:6px 0 0;">対象選択肢ごとに判定を選び、「この判定で再採点」を押します。すべての選択肢の対応が正しくなれば正解になります（ノーカウントは除外して判定）。</p>`
          : `<div class="ch-actions">
               <button type="button" class="btn ch-correct" data-id="${ch.id}">正解に訂正</button>
               <button type="button" class="btn ch-void" data-id="${ch.id}">ノーカウント</button>
               <button type="button" class="btn btn-secondary ch-reject" data-id="${ch.id}">却下</button>
             </div>`;
        panel = `
          <label class="ch-field-label">受験者への返信（任意・本人のマイページに表示）</label>
          <textarea class="ch-msg" data-id="${ch.id}" rows="2"></textarea>
          <label class="ch-field-label">対応メモ（内部・任意）</label>
          <textarea class="ch-note" data-id="${ch.id}" rows="2"></textarea>
          ${actions}`;
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
        ${renderSnapshot(ch.snapshot, { editable: ch.status === "open" && ch.snapshot && ch.snapshot.type === "multi", chId: ch.id })}
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
    // 上級: 選択肢別の判定を集めて再採点
    challengesArea.querySelectorAll(".ch-apply-multi").forEach((b) =>
      b.addEventListener("click", () => {
        const id = b.dataset.id;
        const rulings = {};
        challengesArea.querySelectorAll(`.ch-ruling[data-ch="${id}"]`).forEach((sel) => {
          rulings[sel.dataset.idx] = sel.value;
        });
        resolveChallenge(id, "accept", null, rulings);
      }));
  }

  // カード内の入力欄の値を取得（空は null）。
  function cardValue(id, cls) {
    const el = challengesArea.querySelector(`.${cls}[data-id="${id}"]`);
    if (!el) return null;
    const v = el.value.trim();
    return v || null;
  }

  // 認容（resolution: correct/void、または上級の choice_rulings）／却下。
  async function resolveChallenge(id, action, resolution, choiceRulings) {
    const body = {
      admin_message: cardValue(id, "ch-msg"),
      admin_note: cardValue(id, "ch-note"),
    };
    if (action === "accept") {
      if (resolution) body.resolution = resolution;
      if (choiceRulings) body.choice_rulings = choiceRulings;
    }
    try {
      const res = await fetch(`/api/${ADMIN_TOKEN}/admin/challenges/${id}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "処理に失敗しました");
      // 採点に反映されなかった（確定受験が見つからない等）場合は無音にせず明示する。
      if (data.warning) alert("注意: " + data.warning);
      // 上級で「正解にならなかった」場合の通知
      if (data.scoring && data.scoring.applied && choiceRulings && !data.scoring.is_perfect) {
        alert("この判定では全問正解にはならなかったため、正解扱いにはなりませんでした。");
      }
      loadChallenges();
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

  // ==================== メニュー（タブ）切り替え ====================
  const navBtns = Array.from(document.querySelectorAll(".admin-nav-btn"));
  const views = {
    users: document.getElementById("view-users"),
    source: document.getElementById("view-source"),
    challenges: document.getElementById("view-challenges"),
    prompts: document.getElementById("view-prompts"),
  };
  const userDetailView = document.getElementById("view-user-detail");
  let challengesLoaded = false;
  let promptsLoaded = false;

  function switchView(name) {
    // タブを選んだら個人記録画面は必ず閉じる
    if (userDetailView) userDetailView.style.display = "none";
    Object.entries(views).forEach(([k, el]) => {
      if (el) el.style.display = (k === name) ? "" : "none";
    });
    navBtns.forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
    if (name === "challenges" && !challengesLoaded) {
      challengesLoaded = true;
      loadChallenges();
    }
    if (name === "prompts" && !promptsLoaded) {
      promptsLoaded = true;
      loadPrompts();
    }
  }
  navBtns.forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));

  // 個人記録画面へ切り替え（受験者一覧を隠す）
  function showUserDetail() {
    Object.values(views).forEach((el) => { if (el) el.style.display = "none"; });
    if (userDetailView) userDetailView.style.display = "";
  }
  // 受験者一覧へ戻る
  function backToUsers() {
    if (userDetailView) userDetailView.style.display = "none";
    if (views.users) views.users.style.display = "";
    navBtns.forEach((b) => b.classList.toggle("is-active", b.dataset.view === "users"));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  const detailBackBtn = document.getElementById("detail-back");
  if (detailBackBtn) detailBackBtn.addEventListener("click", backToUsers);

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

  // ==================== プロンプト修正 ====================
  const promptQuestion = document.getElementById("prompt-question");
  const promptAnswer = document.getElementById("prompt-answer");
  const promptSaveBtn = document.getElementById("prompt-save-btn");
  const promptSaveStatus = document.getElementById("prompt-save-status");

  async function loadPrompts() {
    try {
      const data = await fetchJson(`/api/${ADMIN_TOKEN}/admin/prompts`);
      if (promptQuestion) promptQuestion.value = data.question || "";
      if (promptAnswer) promptAnswer.value = data.answer || "";
    } catch (e) {
      if (promptSaveStatus) {
        promptSaveStatus.textContent = `読み込み失敗: ${e.message}`;
        promptSaveStatus.style.color = "red";
      }
    }
  }

  if (promptSaveBtn) {
    promptSaveBtn.addEventListener("click", async () => {
      promptSaveBtn.disabled = true;
      promptSaveStatus.textContent = "保存中…";
      promptSaveStatus.style.color = "#666";
      try {
        const res = await fetch(`/api/${ADMIN_TOKEN}/admin/prompts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question: promptQuestion ? promptQuestion.value : "",
            answer: promptAnswer ? promptAnswer.value : "",
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "保存に失敗しました。");
        promptSaveStatus.textContent = "✓ 保存しました（以降の出題に反映されます）";
        promptSaveStatus.style.color = "green";
      } catch (e) {
        promptSaveStatus.textContent = `✗ ${e.message}`;
        promptSaveStatus.style.color = "red";
      } finally {
        promptSaveBtn.disabled = false;
      }
    });
  }
})();
