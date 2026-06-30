"""RAGセッション問題プールのライフサイクル管理。

生成した問題（正答・解説込み）を session_id 紐付けで一時保存し、
フロントへは正答・解説を伏せた形だけを返す。採点時はこのセッションから
正答を引く（固定プール方式が questions.json を引くのと対になる経路）。

DBの生SQLは db.py に集約し、本モジュールはドメイン操作（採番・整形・採点）を担う。
"""
from __future__ import annotations

import unicodedata
import uuid
from typing import List, Optional

from backend import db
from backend.config import RAG_SESSION_TTL_SEC


def _qid(session_id: str, idx: int) -> str:
    """セッション内の問題ID。固定プールの永続ID（b001等）とは衝突しない形式。"""
    return f"{session_id}#{idx}"


def normalize_answer(s: str) -> str:
    """上級（穴埋め）採点用の正規化。

    - NFKC 正規化（全角英数→半角、全角スペース→半角 等）
    - 前後空白の除去
    - 大文字→小文字（英字）
    かな⇄カナの機械変換は行わない（誤判定を避け、表記揺れは候補配列で吸収する）。
    """
    if not isinstance(s, str):
        return ""
    return unicodedata.normalize("NFKC", s).strip().casefold()


def _to_public(qid: str, unit_id: str, q: dict) -> dict:
    """内部問題（正答・解説込み）をフロント向け（伏せた形）に整える。

    選択式は choices を返し、穴埋め（fill_in）は choices を返さず空欄数だけ返す。
    """
    qtype = q.get("type", "choice")
    pub = {
        "id": qid,
        "category": q.get("perspective_id", ""),
        "unit": unit_id,
        "type": qtype,
        "question": q["question"],
    }
    if qtype == "fill_in":
        pub["blank_count"] = len(q.get("blanks", []))
    else:
        pub["choices"] = q["choices"]
    return pub


def _to_stored(qid: str, q: dict) -> dict:
    """内部保持用（正答・解説込み）に整える。形式により持つ鍵が変わる。"""
    qtype = q.get("type", "choice")
    stored = {
        "id": qid,
        "perspective_id": q.get("perspective_id", ""),
        "type": qtype,
        "question": q["question"],
        "explanation": q.get("explanation", ""),
        "source_pages": q.get("source_pages", []),
    }
    if qtype == "fill_in":
        stored["blanks"] = q.get("blanks", [])
    else:
        stored["choices"] = q["choices"]
        stored["answer"] = q["answer"]
    return stored


def grade_answer(
    q: dict,
    choice: Optional[int] = None,
    text_answers: Optional[List[str]] = None,
) -> dict:
    """1問を採点する。形式により照合方法を切り替える。

    Returns:
        {
          "type": "choice" | "fill_in",
          "is_correct": bool,
          "correct_choice": int | None,      # choice のとき
          "correct_answers": [str] | None,   # fill_in のとき（各空欄の代表表記）
        }
    """
    qtype = q.get("type", "choice")
    if qtype == "fill_in":
        blanks = q.get("blanks", [])
        # 代表表記（各空欄の先頭候補）を解説用に返す
        correct_answers = [b["variants"][0] for b in blanks if b.get("variants")]
        texts = text_answers or []
        # 空欄数と回答数が一致し、各空欄が候補のいずれかと正規化後完全一致したら正解
        is_correct = len(texts) == len(blanks) and len(blanks) > 0
        if is_correct:
            for i, b in enumerate(blanks):
                cand = {normalize_answer(v) for v in b.get("variants", [])}
                if normalize_answer(texts[i]) not in cand:
                    is_correct = False
                    break
        return {
            "type": "fill_in",
            "is_correct": is_correct,
            "correct_choice": None,
            "correct_answers": correct_answers,
        }
    # choice（初級Yes/No・中級選択 共通）
    is_correct = choice == q.get("answer")
    return {
        "type": "choice",
        "is_correct": bool(is_correct),
        "correct_choice": q.get("answer"),
        "correct_answers": None,
    }


def create_session(
    username: str,
    level: str,
    unit_id: str,
    questions: List[dict],
    metrics: dict,
    pending_perspectives: Optional[List[dict]] = None,
) -> dict:
    """生成問題からセッションを作り、フロント向けの整形済み問題を返す。

    pending_perspectives を渡すと、テイル（残り問題）の未消化観点として保持する。
    テイルは後続の /api/rag/quiz/continue で生成され、同じセッションへ追記される。

    Returns:
        {"session_id": str, "questions": [フロント向け（正答・解説なし）], "metrics": {...}}
    """
    session_id = "sess_" + uuid.uuid4().hex[:16]
    stored = []
    public = []
    for i, q in enumerate(questions):
        qid = _qid(session_id, i)
        stored.append(_to_stored(qid, q))
        public.append(_to_public(qid, unit_id, q))

    pending = None
    if pending_perspectives:
        # テイル問題のID採番がヘッドと連続するよう next_index を持たせる
        pending = {
            "perspectives": pending_perspectives,
            "next_index": len(questions),
        }

    db.cleanup_expired_sessions()
    db.save_quiz_session(
        session_id=session_id,
        username=username,
        level=level,
        unit_id=unit_id,
        questions=stored,
        meta=metrics,
        ttl_sec=RAG_SESSION_TTL_SEC,
        pending=pending,
    )
    return {"session_id": session_id, "questions": public, "metrics": metrics}


def append_tail_questions(
    session: dict,
    questions: List[dict],
    merged_metrics: dict,
) -> List[dict]:
    """既存セッションにテイル問題を追記し、フロント向け（伏せた形）を返す。

    pending の next_index から連番でIDを採番する。追記後、pending はクリアする。
    """
    session_id = session["session_id"]
    unit_id = session["unit_id"]
    pending = session.get("pending") or {}
    start_idx = pending.get("next_index", len(session.get("questions", [])))

    existing_stored = list(session.get("questions", []))
    new_public = []
    for j, q in enumerate(questions):
        qid = _qid(session_id, start_idx + j)
        existing_stored.append(_to_stored(qid, q))
        new_public.append(_to_public(qid, unit_id, q))

    db.update_quiz_session_questions(
        session_id=session_id,
        questions=existing_stored,
        meta=merged_metrics,
        pending=None,  # テイル消化済みなのでクリア
    )
    return new_public


def get_session(session_id: str) -> Optional[dict]:
    """セッションを取得（期限切れ・不存在なら None）。"""
    return db.get_quiz_session(session_id)


def claim_pending(session_id: str, pending_raw: str) -> bool:
    """テイル生成権を原子的に取得する（成功時 True）。

    取得時の生JSON（session["pending_raw"]）と DB の現在値が一致する場合のみ
    クリアに成功する（CAS）。同時リクエストの二重生成・二重追記を防ぐ。
    """
    return db.claim_quiz_session_pending(session_id, pending_raw)


def restore_pending(session_id: str, pending_raw: str) -> None:
    """テイル生成失敗時に pending を書き戻し、再試行可能な状態へ戻す。"""
    db.restore_quiz_session_pending(session_id, pending_raw)


def question_in_session(session: dict, qid: str) -> Optional[dict]:
    """セッション内の問題を qid で引く（正答・解説込み）。なければ None。"""
    for q in session.get("questions", []):
        if q["id"] == qid:
            return q
    return None


def build_challenge_snapshot(
    q: dict,
    choice: Optional[int] = None,
    text_answers: Optional[List[str]] = None,
) -> dict:
    """チャレンジ（異議申し立て）起票用に、設問の完全なスナップショットを作る。

    設問本文・正答・解説は ephemeral なセッションにしか無いため、起票時にここで保存する。
    管理画面のみで表示する前提（受験者の起票応答には正答を載せない）。
    起票時点の自分の解答（choice / text_answers）も参考情報として同梱する。
    """
    graded = grade_answer(q, choice=choice, text_answers=text_answers)
    snap = {
        "question": q.get("question", ""),
        "type": q.get("type", "choice"),
        "explanation": q.get("explanation", ""),
        "perspective_id": q.get("perspective_id", ""),
        "source_pages": q.get("source_pages", []),
        "user_choice": choice,
        "user_text_answers": text_answers,
        "is_correct": graded["is_correct"],
        "correct_choice": graded["correct_choice"],
        "correct_answers": graded["correct_answers"],
    }
    if q.get("type") != "fill_in":
        snap["choices"] = q.get("choices", [])
    return snap
