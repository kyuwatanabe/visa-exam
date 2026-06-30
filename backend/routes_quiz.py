"""受験系エンドポイント（RAG出題専用）。

  GET  /api/rag/cells              観点メタのあるセル一覧 + 原本利用可否
  GET  /api/rag/units              単元一覧 + 進捗
  POST /api/rag/quiz/start         観点サンプリング→LLM生成→セッション保存→出題
  POST /api/quiz/check             1問即時判定（セッションの正答を照合）
  POST /api/quiz/submit            採点・保存・進捗更新
  GET  /api/history                個人履歴

出題は常にRAG方式。問題はセッション（quiz_sessions）に伏せて保持し、
正答・解説はサーバ側でのみ照合する（フロントへは返さない）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from backend import auth, db
from backend import rag_generator, rag_perspectives, rag_session_store, rag_source
from backend.config import (
    ALLOWED_LEVELS,
    CHALLENGE_USER_STATUS_LABELS,
    RAG_HEAD_COUNT,
    RAG_QUESTIONS_PER_QUIZ,
    UNIT_CLEAR_REQUIRED_STREAK,
    VISA_TYPE_UNITS,
)
from backend.db import SOURCE_RAG
from backend.models import (
    ChallengeCreateRequest,
    CheckRequest,
    RagContinueRequest,
    RagStartRequest,
    SubmitRequest,
)

router = APIRouter()


# ----------------------------------------------------------------------
# RAG 出題
# ----------------------------------------------------------------------
def _offered_cells():
    """出題対象（ビザ種別）の単元セルだけを返す。

    永住権・ビザの基本など、VISA_TYPE_UNITS に含まれない単元は除外する。
    cells / units / start すべてここを通すことで、絞り込みの真実源を1つにする。
    """
    return [
        c
        for c in rag_perspectives.available_cells()
        if c["unit_id"] in VISA_TYPE_UNITS
    ]


@router.get("/api/rag/cells")
def rag_cells():
    """観点メタが用意されているセル一覧と、原本テキストの利用可否を返す。

    出題対象（ビザ種別）の単元のみを返す。これにより index 側の難易度導出も
    出題対象のある難易度だけが有効になる。
    """
    return {
        "cells": _offered_cells(),
        "source_available": rag_source.is_available(),
        "source_error": rag_source.load_error(),
        "questions_per_quiz": RAG_QUESTIONS_PER_QUIZ,
    }


@router.get("/api/rag/units")
def rag_units(
    level: str = Query(..., description="beginner / intermediate / advanced"),
    user: dict = Depends(auth.get_current_user),
):
    """単元一覧。プールサイズの代わりに観点数を表示し、進捗を返す（ログイン必須）。"""
    if level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")

    cells = [c for c in _offered_cells() if c["level"] == level]
    if not cells:
        raise HTTPException(404, f"このレベルには出題対象の単元がありません: {level}")

    progress_map = db.get_progress_map_by_user_id(user["id"], level, source=SOURCE_RAG)
    units_out = []
    for c in cells:
        unit_id = c["unit_id"]
        prog = progress_map.get(unit_id) or {}
        best_streak = prog.get("best_streak", 0)
        perfect_count = prog.get("perfect_count", 0)
        graduated_at = prog.get("graduated_at")
        units_out.append(
            {
                "id": unit_id,
                "name": c["unit_name"],
                "perspective_count": c["perspective_count"],
                "questions_per_quiz": RAG_QUESTIONS_PER_QUIZ,
                "perfect_count": perfect_count,      # 通算満点回数（クリア進捗）
                "streak_count": prog.get("streak_count", 0),
                "best_streak": best_streak,
                "required_streak": UNIT_CLEAR_REQUIRED_STREAK,
                "cleared": graduated_at is not None or perfect_count >= UNIT_CLEAR_REQUIRED_STREAK,
                "graduated_at": graduated_at,
                "last_taken_at": prog.get("last_taken_at"),
                "playable": c["perspective_count"] > 0,
            }
        )
    return {
        "level": level,
        "username": user["display_name"],
        "units": units_out,
        "source_available": rag_source.is_available(),
    }


@router.post("/api/rag/quiz/start")
def rag_quiz_start(req: RagStartRequest, user: dict = Depends(auth.get_current_user)):
    """RAG出題（ヘッド）: 観点サンプリング → 先頭 RAG_HEAD_COUNT 問だけ生成して即返す。

    残り（テイル）は未消化観点としてセッションに保持し、/api/rag/quiz/continue で
    生成・追記する。開始時の体感待ちを縮めるためのヘッド／テイル分割。
    """
    # ソースファイルが利用可能か確認
    if not rag_source.is_available():
        error_msg = rag_source.load_error()
        raise HTTPException(503, error_msg or "RAG出題が利用できません。管理者がソースファイルをアップロードしてください。")
    
    if req.level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")
    if req.unit not in VISA_TYPE_UNITS:
        # 出題対象外（永住権・ビザの基本など）。URL直打ち等での到達を塞ぐ。
        # データは保持しているが、当面は出題しない。
        raise HTTPException(404, f"出題対象外の単元です: {req.unit}")
    if rag_perspectives.get_meta(req.level, req.unit) is None:
        raise HTTPException(404, f"観点メタがありません: level={req.level}, unit={req.unit}")

    # 観点は最初に全数サンプリング（LLM不要）。ヘッド／テイルに分割する。
    perspectives, seed = rag_perspectives.sample_perspectives(
        req.level, req.unit, RAG_QUESTIONS_PER_QUIZ
    )
    if not perspectives:
        raise HTTPException(502, f"観点が0件です: level={req.level}, unit={req.unit}")
    head = perspectives[:RAG_HEAD_COUNT]
    tail = perspectives[RAG_HEAD_COUNT:]

    try:
        gen = rag_generator.generate_questions(
            req.level, req.unit, head, seed=seed
        )
    except rag_generator.RAGGenerationError as e:
        msg = str(e)
        if "ANTHROPIC_API_KEY" in msg:
            raise HTTPException(503, msg)
        raise HTTPException(502, f"RAG出題の生成に失敗しました: {msg}")

    session = rag_session_store.create_session(
        username=user["email"],  # セッション帰属の識別はメール（UNIQUE）で行う
        level=req.level,
        unit_id=req.unit,
        questions=gen["questions"],
        metrics=gen["metrics"],
        pending_perspectives=tail,
    )
    return {
        "level": req.level,
        "unit": req.unit,
        "session_id": session["session_id"],
        "questions": session["questions"],
        "total_questions": len(perspectives),
        "head_count": len(head),
        "pending_count": len(tail),
        "gen_metrics": gen["metrics"],
    }


@router.post("/api/rag/quiz/continue")
def rag_quiz_continue(req: RagContinueRequest):
    """RAG出題（テイル）: セッションの未消化観点から残り問題を生成・追記する。

    ユーザーがヘッドを解いている間に裏で呼ばれる想定。pending が空なら何もしない。
    """
    # ソースファイルが利用可能か確認
    if not rag_source.is_available():
        error_msg = rag_source.load_error()
        raise HTTPException(503, error_msg or "RAG出題が利用できません。管理者がソースファイルをアップロードしてください。")
    
    if not req.session_id:
        raise HTTPException(400, "session_id が必要です。")
    session = rag_session_store.get_session(req.session_id)
    if session is None:
        raise HTTPException(404, "セッションが見つからない、または期限切れです。")

    pending = session.get("pending") or {}
    pend_perspectives = pending.get("perspectives") or []
    if not pend_perspectives:
        # 既に消化済み or テイル無し。冪等に空を返す。
        return {
            "session_id": req.session_id,
            "questions": [],
            "gen_metrics": session.get("meta", {}),
        }

    # テイル生成権を原子的に取得（CAS）。同時リクエストが来ても勝者は1つだけで、
    # 敗者は冪等に空を返す（テイルの二重生成・二重追記を防ぐ）。
    pending_raw = session.get("pending_raw")
    if not pending_raw or not rag_session_store.claim_pending(req.session_id, pending_raw):
        return {
            "session_id": req.session_id,
            "questions": [],
            "gen_metrics": session.get("meta", {}),
        }

    try:
        gen = rag_generator.generate_questions(
            session["level"], session["unit_id"], pend_perspectives
        )
    except rag_generator.RAGGenerationError as e:
        # 生成失敗時は pending を復元し、フロントの再試行で再生成できるようにする
        # （クレームしたまま握り潰すと、テイルが永久に欠けた検定になる）。
        rag_session_store.restore_pending(req.session_id, pending_raw)
        msg = str(e)
        if "ANTHROPIC_API_KEY" in msg:
            raise HTTPException(503, msg)
        raise HTTPException(502, f"RAG出題（残り）の生成に失敗しました: {msg}")

    merged = rag_generator.merge_metrics(session.get("meta", {}), gen["metrics"])
    public = rag_session_store.append_tail_questions(session, gen["questions"], merged)
    return {
        "session_id": req.session_id,
        "questions": public,
        "gen_metrics": merged,
    }


# ----------------------------------------------------------------------
# 採点・即時判定・履歴
# ----------------------------------------------------------------------
@router.post("/api/quiz/check")
def check_answer(req: CheckRequest):
    """1問だけの即時正誤判定。セッションの正答を照合する。

    採点結果は履歴にも進捗にも一切記録しない（記録は /api/quiz/submit が担う）。
    """
    if not req.session_id:
        raise HTTPException(400, "session_id が必要です。")
    session = rag_session_store.get_session(req.session_id)
    if session is None:
        raise HTTPException(404, "セッションが見つからない、または期限切れです。")
    q = rag_session_store.question_in_session(session, req.id)
    if q is None:
        raise HTTPException(404, f"問題が見つからない: id={req.id}")
    graded = rag_session_store.grade_answer(
        q, choice=req.choice, text_answers=req.text_answers, choices=req.choices
    )
    return {
        "id": q["id"],
        "type": graded["type"],
        "correct_choice": graded["correct_choice"],
        "correct_choices": graded.get("correct_choices"),
        "choice_explanations": graded.get("choice_explanations"),
        "correct_answers": graded["correct_answers"],
        "source_sentence": graded.get("source_sentence"),
        "is_correct": graded["is_correct"],
        "explanation": q.get("explanation", ""),
    }


@router.post("/api/quiz/submit")
def submit_quiz(req: SubmitRequest, user: dict = Depends(auth.get_current_user)):
    if req.level not in ALLOWED_LEVELS:
        raise HTTPException(400, f"level は {','.join(ALLOWED_LEVELS)} のいずれか。")
    if not req.session_id:
        raise HTTPException(400, "session_id が必要です。")

    session = rag_session_store.get_session(req.session_id)
    if session is None:
        raise HTTPException(404, "セッションが見つからない、または期限切れです。")
    qlookup = {q["id"]: q for q in session.get("questions", [])}
    session_meta = session.get("meta", {})

    # 採点
    results = []
    score = 0
    for ans in req.answers:
        q = qlookup.get(ans.id)
        if q is None:
            continue
        graded = rag_session_store.grade_answer(
            q, choice=ans.choice, text_answers=ans.text_answers, choices=ans.choices
        )
        if graded["is_correct"]:
            score += 1
        results.append(
            {
                "id": q["id"],
                "category": q.get("perspective_id"),
                "unit": q.get("unit", req.unit or ""),
                "type": graded["type"],
                "question": q["question"],
                "choices": q.get("choices"),
                "user_choice": ans.choice,
                "user_choices": ans.choices,
                "user_text_answers": ans.text_answers,
                "correct_choice": graded["correct_choice"],
                "correct_choices": graded.get("correct_choices"),
                "choice_explanations": graded.get("choice_explanations"),
                "correct_answers": graded["correct_answers"],
                "is_correct": graded["is_correct"],
                "explanation": q.get("explanation", ""),
            }
        )

    total = len(results)
    if total == 0:
        raise HTTPException(400, "有効な解答がない")

    # 履歴を保存。単元情報・生成メタは details JSON 内の meta に格納する。
    details_payload = json.dumps(
        {
            "meta": {
                "unit": req.unit,
                "source": SOURCE_RAG,
                "metrics": session_meta,
            },
            "answers": [
                {
                    "id": r["id"],
                    "type": r["type"],
                    "user_choice": r["user_choice"],
                    "user_text_answers": r["user_text_answers"],
                    "is_correct": r["is_correct"],
                }
                for r in results
            ],
        },
        ensure_ascii=False,
    )
    # username 列にはメール（UNIQUE）を入れて互換を保ち、本流の紐付けは user_id で行う
    attempt_id = db.save_attempt(
        username=user["email"],
        level=req.level,
        score=score,
        total=total,
        details=details_payload,
        source=SOURCE_RAG,
        user_id=user["id"],
    )

    # 受験中に起票されたチャレンジ（異議申し立て）を、この確定受験へ後付けで紐付ける。
    # 認容時の採点遡及訂正はこの attempt を辿って行う。
    db.link_challenges_to_attempt(req.session_id, attempt_id, user["id"])

    # 単元進捗を更新（derive方式：この単元の全受験を実効採点して数え直す。差分加算はしない）
    unit_progress = None
    if req.unit:
        unit_progress = db.recompute_unit_progress_tx(
            user["id"], user["email"], req.level, req.unit, source=SOURCE_RAG
        )

    return {
        "attempt_id": attempt_id,
        "username": user["display_name"],
        "level": req.level,
        "unit": req.unit,
        "score": score,
        "total": total,
        "passed": score == total,
        "required_streak": UNIT_CLEAR_REQUIRED_STREAK,
        "unit_progress": unit_progress,
        "results": results,
    }


@router.get("/api/history")
def get_history(user: dict = Depends(auth.get_current_user)):
    """ログイン中ユーザー自身の受験履歴（マイページ・結果画面用）。"""
    return {
        "username": user["display_name"],
        "attempts": db.get_history_by_user_id(user["id"]),
    }


# ----------------------------------------------------------------------
# チャレンジ（異議申し立て）
# ----------------------------------------------------------------------
@router.post("/api/quiz/challenge")
def create_challenge(
    req: ChallengeCreateRequest, user: dict = Depends(auth.get_current_user)
):
    """出題・採点への異議申し立て（チャレンジ）を起票する（受験中の解説パネルから）。

    設問スナップショット（本文・正答・解説込み）をサーバ側でセッションから生成して保存する。
    正答は応答に載せない（受理可否と challenge_id のみ返す）。同一設問への再起票は 409。
    """
    if not req.session_id or not req.question_id:
        raise HTTPException(400, "session_id と question_id が必要です。")
    reason = (req.reason or "").strip()
    if not reason:
        raise HTTPException(400, "申し立ての理由を入力してください。")

    session = rag_session_store.get_session(req.session_id)
    if session is None:
        raise HTTPException(404, "セッションが見つからない、または期限切れです。")
    # セッションの帰属（メール）を確認し、他人のセッションへの起票を塞ぐ
    if session.get("username") != user["email"]:
        raise HTTPException(403, "このセッションに対する操作は許可されていません。")
    q = rag_session_store.question_in_session(session, req.question_id)
    if q is None:
        raise HTTPException(404, f"問題が見つからない: id={req.question_id}")

    snapshot = rag_session_store.build_challenge_snapshot(
        q, choice=req.choice, text_answers=req.text_answers
    )
    challenge_id = db.create_challenge(
        user_id=user["id"],
        username=user["email"],
        session_id=req.session_id,
        question_id=req.question_id,
        level=session["level"],
        unit_id=session["unit_id"],
        source=SOURCE_RAG,
        kind=req.kind,
        reason=reason,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
    )
    if challenge_id is None:
        raise HTTPException(409, "この問題には既に異議を申し立てています。")
    return {"ok": True, "challenge_id": challenge_id}


@router.get("/api/my/challenges")
def my_challenges(user: dict = Depends(auth.get_current_user)):
    """ログイン中ユーザー自身のチャレンジ一覧（マイページ用）。

    設問本文・理由・ステータス（表示ラベル）・裁定結果・管理者メッセージを返す。
    正答・解説は受験中に既に提示済みのため設問本文のみ要約として返す。
    """
    items = []
    for c in db.list_challenges_by_user(user["id"]):
        snap = {}
        try:
            snap = json.loads(c.get("snapshot") or "{}")
        except (ValueError, TypeError):
            snap = {}
        items.append(
            {
                "id": c["id"],
                "attempt_id": c.get("attempt_id"),
                "level": c["level"],
                "unit_id": c["unit_id"],
                "question": snap.get("question", ""),
                "reason": c.get("reason"),
                "kind": c.get("kind"),
                "status": c["status"],
                "status_label": CHALLENGE_USER_STATUS_LABELS.get(c["status"], c["status"]),
                "admin_message": c.get("admin_message"),
                "created_at": c.get("created_at"),
                "resolved_at": c.get("resolved_at"),
                "closed_at": c.get("closed_at"),
            }
        )
    return {"challenges": items}
