"""DEV ONLY（撤去予定）: 管理画面確認用のデモデータ生成。

ホーム画面右上の「ダミーデータ生成」ボタンから呼ばれ、ペルソナ10人分の
attempts / unit_progress を db.py の正規関数で直接書き込む。LLMは呼ばない
（管理画面の表示確認が目的であり、問題内容は不要なため）。

- 再実行可能: 生成前に同名ペルソナの既存記録を削除してから書き直す（増殖しない）。
- 識別可能: details の meta に test / seeded フラグを残す（運用移行時の除外と整合）。
- 日時: 受験日時を過去14日間に分散させる（履歴画面の日時表示確認のため）。

撤去手順（運用移行時）:
  1. 本ファイルを削除
  2. main.py の include_router(dev_router) を削除
  3. frontend/index.html の DEV ONLY ブロックを削除
  4. config.py の DEMO_SEED_ENABLED を削除
  5. db.py の save_attempt の taken_at 引数は残しても害なし
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from backend import auth, db
from backend.config import DEMO_SEED_ENABLED, UNIT_CLEAR_REQUIRED_STREAK
from backend.db import SOURCE_RAG

router = APIRouter()

# ペルソナ定義: (氏名, [(level, unit, 正答率%), ...])
# 100%を3回積めばクリア（緑）、61〜99%は黄、60%以下は赤になる。
# 優等生・足踏み・苦戦・新人・上級専門・クリア後継続、を網羅する分布。
_PERSONAS = [
    ("田中一郎", [
        ("beginner", "b_visa", 100), ("beginner", "b_visa", 100), ("beginner", "b_visa", 100),
        ("beginner", "e_visa", 100), ("beginner", "e_visa", 100), ("beginner", "e_visa", 100),
        ("beginner", "f_visa", 100), ("beginner", "f_visa", 70),
    ]),
    ("佐藤花子", [
        ("intermediate", "b_visa", 100), ("intermediate", "b_visa", 100), ("intermediate", "b_visa", 100),
        ("advanced", "b_visa", 80), ("advanced", "b_visa", 100),
    ]),
    ("鈴木次郎", [
        ("beginner", "h1b_visa", 100), ("beginner", "h1b_visa", 90), ("beginner", "h1b_visa", 100),
        ("beginner", "h1b_visa", 80),
    ]),
    ("高橋美咲", [
        ("beginner", "j_visa", 40), ("beginner", "j_visa", 50), ("beginner", "j_visa", 60),
        ("beginner", "j_visa", 70),
    ]),
    ("伊藤健太", [
        ("beginner", "l_visa", 100), ("beginner", "l_visa", 100), ("beginner", "l_visa", 100),
    ]),
    ("渡辺さくら", [
        ("beginner", "b_visa", 100), ("beginner", "e_visa", 70),
        ("beginner", "f_visa", 100), ("beginner", "f_visa", 90),
        ("intermediate", "e_visa", 50),
    ]),
    ("山本大輔", [
        ("advanced", "e_visa", 100), ("advanced", "e_visa", 100), ("advanced", "e_visa", 100),
        ("advanced", "f_visa", 60),
    ]),
    ("中村結衣", [
        ("beginner", "b_visa", 60),
    ]),
    ("小林翔太", [
        ("intermediate", "j_visa", 90), ("intermediate", "j_visa", 80),
        ("intermediate", "j_visa", 70), ("intermediate", "j_visa", 90),
    ]),
    ("加藤愛", [
        ("intermediate", "l_visa", 100), ("intermediate", "l_visa", 100),
        ("intermediate", "l_visa", 100), ("intermediate", "l_visa", 100),
    ]),
]

_TOTAL_QUESTIONS = 10  # 1回の出題数（正答率の刻みを本番同等にする）


def _details_json(unit: str, score: int, total: int) -> str:
    """本番 submit が書く details と同構造のダミーを組み立てる。

    answers は表示確認に不要なため空。meta の test / seeded フラグで
    デモ由来であることを識別できる（運用移行時の除外方針と整合）。
    """
    return json.dumps(
        {
            "meta": {
                "unit": unit,
                "source": SOURCE_RAG,
                "test": True,
                "seeded": True,
                "metrics": {"test": True, "seeded": True},
            },
            "answers": [],
        },
        ensure_ascii=False,
    )


@router.get("/api/dev/seed-demo")
def seed_demo_status():
    """フロントがボタンの表示可否を判定するためのステータス。"""
    return {"enabled": DEMO_SEED_ENABLED}


_DEMO_PASSWORD = "demo-pass-123"  # 全デモアカウント共通（構築段階専用）


def _demo_email(idx: int) -> str:
    return f"demo{idx + 1:02d}@example.local"


@router.post("/api/dev/seed-demo")
def seed_demo():
    """ペルソナ10人分のデモアカウントと受験データを生成する（再実行時は削除して作り直す）。

    各ペルソナは実アカウント（メール＋パスワード）として作成され、ログイン動作の確認にも使える。
    メール: demo01@example.local 〜 demo10@example.local / パスワード: demo-pass-123（共通）
    """
    if not DEMO_SEED_ENABLED:
        raise HTTPException(404, "デモデータ生成は無効化されています")

    total_attempts = sum(len(p[1]) for p in _PERSONAS)
    # 受験日時を「14日前 → ほぼ現在」に等間隔で分散させる。
    # ペルソナの plan 順 = 時系列順になるよう、全受験を通し番号で並べる。
    now = datetime.now(timezone.utc)
    span = timedelta(days=14)
    step = span / max(total_attempts, 1)

    users_summary = []
    seq = 0
    for idx, (display_name, plans) in enumerate(_PERSONAS):
        email = _demo_email(idx)
        # 再実行で増殖させない: 既存デモアカウントを記録ごと削除して作り直す
        existing = db.get_user_by_email(email)
        if existing is not None:
            db.delete_user_account_records(existing["id"])
        user_id = db.create_user(email, auth.hash_password(_DEMO_PASSWORD), display_name)
        cleared_units = 0
        for level, unit, pct in plans:
            score = max(0, min(_TOTAL_QUESTIONS, round(pct / 100 * _TOTAL_QUESTIONS)))
            taken_at = (now - span + step * seq).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            seq += 1
            db.save_attempt(
                email, level, score, _TOTAL_QUESTIONS,
                details=_details_json(unit, score, _TOTAL_QUESTIONS),
                source=SOURCE_RAG,
                taken_at=taken_at,
                user_id=user_id,
            )
            prog = db.update_unit_progress(
                email, level, unit, perfect=(score == _TOTAL_QUESTIONS),
                clear_streak_required=UNIT_CLEAR_REQUIRED_STREAK,
                source=SOURCE_RAG,
                user_id=user_id,
            )
            if prog.get("newly_cleared"):
                cleared_units += 1
        users_summary.append(
            {"username": display_name, "email": email,
             "attempts": len(plans), "cleared_units": cleared_units}
        )

    return {
        "ok": True,
        "users": len(_PERSONAS),
        "attempts": total_attempts,
        "summary": users_summary,
    }
