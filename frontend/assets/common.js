// ===========================================================
// common.js — フロント共通ユーティリティ
//
// result.html / units.html / admin.js / quiz.js に重複定義されていた
// 小さなヘルパをここへ一本化する。各HTMLで <script src="/assets/common.js">
// を他のスクリプトより前に読み込むことで、グローバル関数として使える。
//
// 挙動は従来の各実装と同一（ロジックは変えていない）。
// ===========================================================

// HTMLエスケープ（XSS対策）
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// 難易度レベル（順序つき）と日本語表示名の単一の定義元。
const LEVELS = ["beginner", "intermediate", "advanced"];
const LEVEL_NAMES = { beginner: "初級", intermediate: "中級", advanced: "上級" };

// レベルIDを日本語表示名へ
function levelLabel(id) {
  return LEVEL_NAMES[id] || id;
}

// ISO日時 → "YYYY-MM-DD HH:MM"（不正な値はそのまま返す）
function fmtDate(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ISO日時 → "YYYY/MM/DD"（時刻なし。不正・空は null）
function fmtDateShort(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d)) return null;
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
}

// ログイン必須ページの共通ガード。未ログインならトップ（ログイン画面）へ送り null を返す。
// 成功時はログイン中ユーザー（id/email/display_name）を返す。
async function requireLogin() {
  try {
    const res = await fetch("/api/auth/me");
    if (!res.ok) { location.href = "/"; return null; }
    return await res.json();
  } catch (e) {
    location.href = "/";
    return null;
  }
}

// ログアウトしてログイン画面へ戻す共通処理。
async function logoutAndRedirect() {
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) {}
  location.href = "/";
}

// ログイン後画面の共通ヘッダーナビ。各ページのヘッダー右側へ
// ホーム / マイページ / ログアウト を差し込む。
//   active : "home" / "mypage" を渡すと現在ページのボタンを強調する。
//   opts.confirmLeave : 文字列を渡すと、移動・ログアウト前に confirm を挟む
//                       （受験中の誤離脱防止用）。
// ヘッダー（.header）が無いページでは何もしない。
function mountHeaderNav(active, opts) {
  const header = document.querySelector(".header");
  if (!header || header.querySelector(".header-nav")) return;
  const confirmLeave = (opts && opts.confirmLeave) || null;

  const nav = document.createElement("nav");
  nav.className = "header-nav";

  const links = [
    { key: "home", label: "ホーム", href: "/home.html" },
    { key: "mypage", label: "マイページ", href: "/mypage.html" },
  ];
  for (const l of links) {
    const a = document.createElement("a");
    a.href = l.href;
    a.textContent = l.label;
    a.className = "btn btn-secondary" + (active === l.key ? " is-active" : "");
    if (confirmLeave) {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        if (confirm(confirmLeave)) location.href = l.href;
      });
    }
    nav.appendChild(a);
  }

  const out = document.createElement("button");
  out.type = "button";
  out.className = "btn btn-secondary";
  out.textContent = "ログアウト";
  out.addEventListener("click", () => {
    if (!confirmLeave || confirm(confirmLeave)) logoutAndRedirect();
  });
  nav.appendChild(out);

  header.appendChild(nav);
}

// 正答率(%) → スコアピルのCSSクラス（管理画面 rateClass と同基準に統一）
//   緑(high): 満点 / 黄(mid): 61〜99% / 赤(low): 60%以下
function pillClass(pct) {
  if (pct >= 100) return "high";
  if (pct >= 61) return "mid";
  return "low";
}

// チャレンジのステータス内部コード → 表示ラベル（管理画面用）
const CHALLENGE_STATUS_LABEL = {
  open: "未処理",
  accepted: "処理済",
  closed: "クローズ",
  rejected: "却下",
};

// アプリのバージョン（唯一の定義箇所）。管理画面・受験画面はここを参照する。
const APP_VERSION = "v1.8.2";

// バージョン表示。#app-version-slot があればそこへ、#app-title があればその右、
// どちらも無ければ画面上部右に出す。
function initVersionDisplay() {
  // 管理画面など、専用スロットがあればそこに入れて終わり
  const slot = document.getElementById("app-version-slot");
  if (slot) {
    slot.textContent = APP_VERSION;
  }
  if (document.getElementById("app-version")) return;  // 二重表示防止
  const versionEl = document.createElement("span");
  versionEl.id = "app-version";
  versionEl.textContent = APP_VERSION;
  const title = document.getElementById("app-title");
  if (title) {
    versionEl.style.cssText =
      "color: #888; font-size: 13px; font-weight: 600; margin-left: 10px;";
    title.appendChild(versionEl);
  } else if (!slot) {
    versionEl.style.cssText =
      "display:block; text-align: right; color: #555; font-size: 13px; font-weight: 600; padding: 6px 16px;";
    document.body.insertBefore(versionEl, document.body.firstChild);
  }
}

// ページロード時に実行
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initVersionDisplay);
} else {
  initVersionDisplay();
}
