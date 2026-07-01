"""出題生成ジョブの進捗管理（インメモリ）。

プールが空でその場生成する際、開始リクエストは即座にジョブを作って返し、
生成はバックグラウンドスレッドで進める。フロントは進捗（done/total）を
ポーリングして「できた設問数」をカウントアップ表示できる。

単一プロセス（uvicorn ワーカー1）前提のインメモリ実装。生成スレッドと
進捗ポーリングは同一プロセスで動くため、辞書＋ロックで足りる。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

_JOBS: dict = {}
_LOCK = threading.Lock()
_TTL_SEC = 600  # 完了/失敗から10分で掃除


def create_job(user_email: str, level: str, unit_id: str, total: int) -> str:
    job_id = uuid.uuid4().hex
    with _LOCK:
        _JOBS[job_id] = {
            "user": user_email,
            "level": level,
            "unit_id": unit_id,
            "total": total,
            "done": 0,
            "status": "generating",   # generating | ready | error
            "session": None,          # 完了時のセッション公開情報
            "error": None,
            "updated_at": time.time(),
        }
    return job_id


def set_done(job_id: str, done: int) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is None:
            return
        # 単調増加のみ（並列完了の前後関係で戻らないように）
        j["done"] = max(j["done"], min(done, j["total"]))
        j["updated_at"] = time.time()


def finish(job_id: str, session_public: dict) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is None:
            return
        j["status"] = "ready"
        j["done"] = j["total"]
        j["session"] = session_public
        j["updated_at"] = time.time()


def fail(job_id: str, message: str) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is None:
            return
        j["status"] = "error"
        j["error"] = message
        j["updated_at"] = time.time()


def get(job_id: str) -> Optional[dict]:
    _cleanup()
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j is not None else None


def _cleanup() -> None:
    now = time.time()
    with _LOCK:
        stale = [
            k for k, v in _JOBS.items()
            if v["status"] in ("ready", "error") and now - v["updated_at"] > _TTL_SEC
        ]
        for k in stale:
            _JOBS.pop(k, None)
