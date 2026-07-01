"""検定問題の事前生成プール。

級×単元ごとに、完成した問題セット（全問・正答/解説込み）をあらかじめ
バックグラウンドで作り置きしておく。検定開始時はプールから即座に払い出すため、
その場生成の待ち時間・失敗をユーザーが被らなくなる。

- 払い出し（pool_claim）はDB側で原子的に行い、1セットは1人にしか渡らない。
- 補充はバックグラウンドスレッドで行う（生成の遅さ・失敗はユーザーに影響しない）。
- 目標在庫（POOL_TARGET_PER_UNIT）を各単元で維持する。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import List, Optional, Tuple

from backend import db
from backend import rag_generator
from backend import rag_perspectives
from backend import rag_source
from backend.config import RAG_QUESTIONS_PER_QUIZ, VISA_TYPE_UNITS

# 各級×単元で維持したい在庫数（未払い出しセット数）
POOL_TARGET_PER_UNIT = int(os.environ.get("POOL_TARGET_PER_UNIT", "2"))
# 補充ワーカーのポーリング間隔（秒）
POOL_WORKER_INTERVAL = int(os.environ.get("POOL_WORKER_INTERVAL", "20"))
# 出題対象の級（config 側と揃える）。ここでは環境変数か既定で持つ。
POOL_LEVELS = [s.strip() for s in os.environ.get(
    "POOL_LEVELS", "beginner,intermediate,advanced"
).split(",") if s.strip()]

_worker_started = False
_worker_lock = threading.Lock()
# 単一セット生成の多重起動を防ぐ（級×単元ごと）
_gen_locks: dict = {}
_gen_locks_guard = threading.Lock()


def _get_gen_lock(level: str, unit_id: str) -> threading.Lock:
    key = f"{level}:{unit_id}"
    with _gen_locks_guard:
        lock = _gen_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _gen_locks[key] = lock
        return lock


def _target_units() -> List[Tuple[str, str]]:
    """プール対象の (level, unit_id) 一覧を返す。出題対象単元のみ。"""
    # 出題対象単元は routes 側の VISA_TYPE_UNITS に準ずるが、循環importを避け、
    # 観点メタが存在する単元を対象とする。
    out: List[Tuple[str, str]] = []
    for level in POOL_LEVELS:
        for unit_id in rag_perspectives.list_units(level):
            if unit_id in VISA_TYPE_UNITS:
                out.append((level, unit_id))
    return out


def generate_one_set(level: str, unit_id: str) -> bool:
    """1セット（全問）を生成してプールに投入する。成功で True。

    生成は重い・失敗しうるが、バックグラウンド想定なので例外は握って False を返す。
    """
    lock = _get_gen_lock(level, unit_id)
    if not lock.acquire(blocking=False):
        return False  # 同単元の生成が進行中
    try:
        if not rag_source.is_available():
            return False
        if rag_perspectives.get_meta(level, unit_id) is None:
            return False
        perspectives, seed = rag_perspectives.sample_perspectives(
            level, unit_id, RAG_QUESTIONS_PER_QUIZ
        )
        if not perspectives:
            return False
        gen = rag_generator.generate_questions(level, unit_id, perspectives, seed=seed)
        questions = gen.get("questions") or []
        if not questions:
            return False
        db.pool_add(
            level,
            unit_id,
            json.dumps(questions, ensure_ascii=False),
            json.dumps(gen.get("metrics", {}), ensure_ascii=False),
        )
        return True
    except Exception:
        # 補充失敗はユーザーに影響しない（次回のワーカーで再挑戦）
        return False
    finally:
        lock.release()


def claim_set(level: str, unit_id: str) -> Optional[dict]:
    """プールから1セットを払い出す。無ければ None。

    払い出し後は在庫が減るので、非同期で1セット補充を試みる。
    """
    got = db.pool_claim(level, unit_id)
    # 払い出したら1つ補充（バックグラウンド）
    refill_async(level, unit_id, count=1)
    return got


def refill_async(level: str, unit_id: str, count: int = 1) -> None:
    """指定単元をバックグラウンドで count セット補充する（多重起動は内部で抑止）。"""
    def _job():
        for _ in range(max(1, count)):
            generate_one_set(level, unit_id)
    t = threading.Thread(target=_job, daemon=True)
    t.start()


def _worker_loop():
    """全対象単元の在庫を目標数まで維持し続けるループ。"""
    # 起動直後は原本ロードやDB初期化の完了を少し待つ
    time.sleep(5)
    while True:
        try:
            if rag_source.is_available():
                for level, unit_id in _target_units():
                    try:
                        have = db.pool_count(level, unit_id)
                    except Exception:
                        have = POOL_TARGET_PER_UNIT  # DB不調時は生成しない
                    # 目標に満たなければ1つだけ作る（1周で1単元1セット。負荷を平準化）
                    if have < POOL_TARGET_PER_UNIT:
                        generate_one_set(level, unit_id)
        except Exception:
            pass
        time.sleep(POOL_WORKER_INTERVAL)


def start_worker() -> None:
    """補充ワーカーを1度だけ起動する（アプリ起動時に呼ぶ）。"""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        t = threading.Thread(target=_worker_loop, daemon=True)
        t.start()
