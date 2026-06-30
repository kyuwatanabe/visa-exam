"""管理者用エンドポイント（URLトークンで難読化のみ）。

  GET  /api/{token}/admin/users             受験者一覧（名前＋単元別進捗・クリア数降順）
  GET  /api/{token}/admin/history?username= 個別の受験履歴（得点は返さず正答率のみ）

RAG出題専用。サマリー・受験回数・最高点・平均点・全件履歴は廃止した
（受験者ごとの単元クリア状況の把握と、個別履歴の正答率確認に絞る）。
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from backend import auth, db, rag_perspectives
from backend.config import ADMIN_TOKEN, CHALLENGE_STATUS_LABELS, UNIT_CLEAR_REQUIRED_STREAK
from backend.db import SOURCE_RAG

router = APIRouter()


def _check_token(token: str) -> None:
    if token != ADMIN_TOKEN:
        raise HTTPException(404)


def _unit_name_map() -> dict:
    """unit_id → 表示名。除外単元（永住権等）の履歴も名前を引けるよう全観点から作る。"""
    m = {}
    for c in rag_perspectives.available_cells():
        m[c["unit_id"]] = c.get("unit_name", c["unit_id"])
    return m


@router.get("/api/{token}/admin/users")
def admin_users(token: str):
    """アカウント一覧。各アカウントの単元別進捗（満点回数 / クリア状況）と、
    クリア済み単元の総数を返す。クリア数の降順、同数は表示名の昇順で並べる。

    進捗のないアカウント（登録のみ）も一覧に出す。
    user_id の紐づかない旧データ（氏名のみの記録）は表示しない。
    """
    _check_token(token)
    name_map = _unit_name_map()
    rows = db.get_all_unit_progress_by_account(source=SOURCE_RAG)

    by_uid: dict = {}
    for r in rows:
        bucket = by_uid.setdefault(r["user_id"], [])
        cleared = r.get("graduated_at") is not None or \
            r.get("perfect_count", 0) >= UNIT_CLEAR_REQUIRED_STREAK
        bucket.append(
            {
                "level": r["level"],
                "unit_id": r["unit_id"],
                "unit_name": name_map.get(r["unit_id"], r["unit_id"]),
                "perfect_count": r.get("perfect_count", 0),
                "required": UNIT_CLEAR_REQUIRED_STREAK,
                "cleared": cleared,
                "last_taken_at": r.get("last_taken_at"),
            }
        )

    users = []
    for account in db.list_users():
        units = by_uid.get(account["id"], [])
        # 表示順: 直近に受験した単元ほど前（クライアント要望）。未受験日時は末尾。
        units.sort(key=lambda u: u.get("last_taken_at") or "", reverse=True)
        cleared_count = sum(1 for u in units if u["cleared"])
        last_taken_at = max((u.get("last_taken_at") or "" for u in units), default="") or None
        users.append(
            {
                "user_id": account["id"],
                "username": account["display_name"],
                "email": account["email"],
                "cleared_count": cleared_count,
                "last_taken_at": last_taken_at,
                "units": units,
            }
        )
    # クリア数の降順、同数は表示名の昇順
    users.sort(key=lambda u: (-u["cleared_count"], u["username"]))
    return {"users": users, "required": UNIT_CLEAR_REQUIRED_STREAK}


@router.get("/api/{token}/admin/history")
def admin_history(token: str, user_id: int):
    """指定アカウントの受験履歴。得点（score/total）は返さず、正答率（%）のみを返す。

    各回ごとに 日時・レベル・単元・正答率 を返す（どの受験か特定できる情報は維持）。
    """
    _check_token(token)
    account = db.get_user_by_id(user_id)
    if account is None:
        raise HTTPException(404, "アカウントが見つかりません。")
    name_map = _unit_name_map()
    # 満点の通し番号付与のため余裕を持って取得（時系列の古い側から数える）
    attempts = db.get_history_by_user_id(user_id, limit=1000)

    # 満点の通し番号: 同一 (level, unit) で時系列昇順に 1, 2, 3... と数える。
    # attempts は新しい順なので、逆順に走査してカウンタを進める。
    perfect_no_by_id: dict = {}
    counters: dict = {}
    for a in reversed(attempts):
        total = a.get("total") or 0
        score = a.get("score") or 0
        if total and score == total:
            key = (a.get("level"), a.get("unit"))
            counters[key] = counters.get(key, 0) + 1
            perfect_no_by_id[a.get("id")] = counters[key]

    out = []
    for a in attempts[:50]:  # 表示件数は従来どおり50件まで
        total = a.get("total") or 0
        score = a.get("score") or 0
        pct = round(score * 100 / total) if total else 0
        unit_id = a.get("unit")
        out.append(
            {
                "taken_at": a.get("taken_at"),
                "level": a.get("level"),
                "unit_id": unit_id,
                "unit_name": name_map.get(unit_id) if unit_id else None,
                "pct": pct,  # 正答率のみ。得点は意図的に返さない。
                # 満点なら「何回目の満点か」（同一レベル×単元の通算。クリア閾値と並べて表示する用）
                "perfect_no": perfect_no_by_id.get(a.get("id")),
            }
        )
    return {
        "username": account["display_name"],
        "email": account["email"],
        "attempts": out,
        "required": UNIT_CLEAR_REQUIRED_STREAK,
    }


class AdminPasswordResetRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/api/{token}/admin/users/{user_id}/password")
def admin_reset_password(token: str, user_id: int, req: AdminPasswordResetRequest):
    """パスワードを忘れたユーザーのために管理者が再設定する（メール送信基盤は持たない）。

    再設定後、そのユーザーの全ログインセッションは失効する（update_user_password 内）。
    新しいパスワードは管理者が口頭等で本人へ伝える運用。
    """
    _check_token(token)
    if db.get_user_by_id(user_id) is None:
        raise HTTPException(404, "アカウントが見つかりません。")
    db.update_user_password(user_id, auth.hash_password(req.new_password))
    return {"ok": True, "user_id": user_id}


# ----------------------------------------------------------------------
# チャレンジ（異議申し立て）の裁定
# ----------------------------------------------------------------------
class AdminChallengeResolveRequest(BaseModel):
    """認容／却下のリクエスト。受験者向けメッセージと内部の対応メモ（任意）。

    resolution は認容の種別（accept でのみ使用）:
      "correct"（正解に訂正）／"void"（ノーカウント）。既定は correct。
    """
    resolution: Optional[str] = "correct"
    admin_message: Optional[str] = Field(None, max_length=2000)
    admin_note: Optional[str] = Field(None, max_length=2000)


class AdminChallengeCloseRequest(BaseModel):
    """クローズ（根本是正の完了印）のリクエスト。対応メモ（任意）。"""
    admin_note: Optional[str] = Field(None, max_length=2000)


@router.get("/api/{token}/admin/challenges")
def admin_challenges(token: str, status: Optional[str] = None):
    """チャレンジ一覧（新しい順）。status 指定でフィルタ。

    設問スナップショット（本文・正答・解説）込みで返す（裁定の判断材料）。
    """
    _check_token(token)
    name_map = _unit_name_map()
    # user_id → 表示名（申請者はメールでなく名前で出す）
    name_by_uid = {u["id"]: u["display_name"] for u in db.list_users()}
    items = []
    for c in db.list_challenges(status=status):
        try:
            snap = json.loads(c.get("snapshot") or "{}")
        except (ValueError, TypeError):
            snap = {}
        items.append(
            {
                "id": c["id"],
                "applicant": name_by_uid.get(c.get("user_id")) or c["username"],
                "level": c["level"],
                "unit_id": c["unit_id"],
                "unit_name": name_map.get(c["unit_id"], c["unit_id"]),
                "question_id": c["question_id"],
                "attempt_id": c.get("attempt_id"),
                "kind": c.get("kind"),
                "reason": c.get("reason"),
                "snapshot": snap,
                "status": c["status"],
                "status_label": CHALLENGE_STATUS_LABELS.get(c["status"], c["status"]),
                "resolution": c.get("resolution"),
                "admin_message": c.get("admin_message"),
                "admin_note": c.get("admin_note"),
                "created_at": c.get("created_at"),
                "resolved_at": c.get("resolved_at"),
                "closed_at": c.get("closed_at"),
            }
        )
    return {"challenges": items}


@router.post("/api/{token}/admin/challenges/{challenge_id}/accept")
def admin_accept_challenge(token: str, challenge_id: int, req: AdminChallengeResolveRequest):
    """チャレンジを認容する。resolution に応じて採点を遡及訂正する。

    - correct: 当該設問を正解にセット（誤答→正解で +1）
    - void: 当該設問をノーカウント化（total -1、正解分は score も -1）
    """
    _check_token(token)
    res = db.accept_challenge(
        challenge_id, resolution=(req.resolution or "correct"),
        admin_message=req.admin_message, admin_note=req.admin_note,
    )
    if not res["ok"]:
        if res.get("error") == "not_found":
            raise HTTPException(404, "チャレンジが見つかりません。")
        if res.get("error") == "bad_resolution":
            raise HTTPException(400, "resolution は correct / void のいずれか。")
        raise HTTPException(409, "未処理のチャレンジのみ認容できます。")

    # 採点に反映されなかった場合（確定した受験が見つからない＝中断/やり直し等）は
    # 無音で成功扱いにせず、明示して返す。
    scoring = res.get("scoring") or {}
    out = {"ok": True, "scoring": scoring}
    if not scoring.get("applied"):
        out["warning"] = (
            "この異議に対応する確定した受験が見つからないため、採点（正解訂正／"
            "ノーカウント）は反映されていません。"
        )
    return out


@router.post("/api/{token}/admin/challenges/{challenge_id}/reject")
def admin_reject_challenge(token: str, challenge_id: int, req: AdminChallengeResolveRequest):
    """チャレンジを却下する（終端）。採点は変えない。"""
    _check_token(token)
    res = db.reject_challenge(
        challenge_id, admin_message=req.admin_message, admin_note=req.admin_note
    )
    if not res["ok"]:
        if res.get("error") == "not_found":
            raise HTTPException(404, "チャレンジが見つかりません。")
        raise HTTPException(409, "未処理のチャレンジのみ却下できます。")
    return {"ok": True}


@router.post("/api/{token}/admin/challenges/{challenge_id}/close")
def admin_close_challenge(token: str, challenge_id: int, req: AdminChallengeCloseRequest):
    """認容済み（処理済）のチャレンジを手動でクローズする（根本是正の完了印・終端）。

    観点メタ／システムプロンプトの是正自体はサイト外（Git push）で行い、反映後に
    管理者がここでクローズする。対応メモに是正内容（または是正不要の理由）を残す。
    """
    _check_token(token)
    res = db.close_challenge(challenge_id, admin_note=req.admin_note)
    if not res["ok"]:
        if res.get("error") == "not_found":
            raise HTTPException(404, "チャレンジが見つかりません。")
        raise HTTPException(409, "認容済み（処理済）のチャレンジのみクローズできます。")
    return {"ok": True}


@router.get("/api/{token}/admin/source/files")
def admin_get_source_files(token: str):
    """保存されているソースファイル一覧を返す（ファイル名、サイズ、更新日時）。"""
    _check_token(token)
    from backend.config import SOURCE_DIR
    from datetime import datetime
    
    files = []
    source_dir = SOURCE_DIR
    
    if source_dir.exists():
        for path in sorted(source_dir.glob("*")):
            if path.is_file():
                stat = path.stat()
                files.append({
                    "name": path.name,
                    "size": stat.st_size,
                    "size_display": f"{stat.st_size / 1024:.1f} KB" if stat.st_size < 1024*1024 else f"{stat.st_size / (1024*1024):.1f} MB",
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
    
    return {"files": files}


@router.post("/api/{token}/admin/source/upload")
async def admin_upload_source(token: str, file: UploadFile = File(...)):
    """PDF をアップロードして、自動的にテキストに変換する。"""
    _check_token(token)
    from backend.config import SOURCE_DIR, SOURCE_PDF_PATH, SOURCE_TXT_PATH
    from pypdf import PdfReader
    import io
    
    if not file.filename.endswith('.pdf'):
        raise HTTPException(400, "PDF ファイルのみアップロード可能です。")
    
    try:
        # PDF ファイルを読み込み
        content = await file.read()
        pdf_bytes = io.BytesIO(content)
        reader = PdfReader(pdf_bytes)
        
        # PDF をローカルに保存
        SOURCE_DIR.mkdir(parents=True, exist_ok=True)
        SOURCE_PDF_PATH.write_bytes(content)
        
        # テキストに変換して保存
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
        
        # ページごとにフォームフィード区切りで結合
        full_text = "\f".join(text_parts)
        SOURCE_TXT_PATH.write_text(full_text, encoding="utf-8")
        
        return {
            "ok": True,
            "pdf_size": len(content),
            "txt_size": len(full_text.encode("utf-8")),
            "pages": len(reader.pages),
        }
    except Exception as e:
        raise HTTPException(400, f"ファイル処理に失敗しました: {str(e)}")


@router.delete("/api/{token}/admin/source/delete")
async def delete_source_file(token: str, filename: str):
    """ソースファイルを削除。"""
    _check_token(token)
    
    # セキュリティ: パストトラバーサル防止
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "不正なファイル名です")
    
    try:
        file_path = SOURCE_DIR / filename
        
        if file_path.exists():
            file_path.unlink()
        
        return {"ok": True, "deleted": filename}
    except Exception as e:
        raise HTTPException(400, f"削除に失敗しました: {str(e)}")
