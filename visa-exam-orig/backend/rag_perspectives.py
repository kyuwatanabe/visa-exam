"""観点メタ（perspectives/*.json）のロードとサンプリング。

RAG出題は「原本に書かれた論点（観点）」を起点に問題を作る。各セルの観点メタを
起動時にメモリへ読み込み、出題のたびに観点を重複なくサンプリングして返す。
観点メタ自体は不変なので、起動時に一度ロードしてメモリ保持する。
"""
from __future__ import annotations

import json
import random
from typing import List, Optional

from backend.config import PERSPECTIVES_DIR

# {(level, unit_id): perspective_meta_dict}
_PERSPECTIVES: dict = {}


def load() -> None:
    """perspectives/*.json を全て読み込み、(level, unit_id) で索引する。"""
    global _PERSPECTIVES
    loaded = {}
    if not PERSPECTIVES_DIR.exists():
        _PERSPECTIVES = loaded
        return
    for path in sorted(PERSPECTIVES_DIR.glob("*.json")):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        level = data.get("level")
        unit_id = data.get("unit_id")
        if not level or not unit_id:
            continue
        loaded[(level, unit_id)] = data
    _PERSPECTIVES = loaded


def get_meta(level: str, unit_id: str) -> Optional[dict]:
    """指定セルの観点メタ全体を返す。無ければ None。"""
    return _PERSPECTIVES.get((level, unit_id))


def available_cells() -> List[dict]:
    """観点メタが用意されているセル一覧を返す（RAGの単元選択UI用）。"""
    out = []
    for (level, unit_id), data in _PERSPECTIVES.items():
        out.append(
            {
                "level": level,
                "unit_id": unit_id,
                "unit_name": data.get("unit_name", unit_id),
                "perspective_count": len(data.get("perspectives", [])),
            }
        )
    out.sort(key=lambda x: (x["level"], x["unit_id"]))
    return out


def sample_perspectives(
    level: str, unit_id: str, n: int, seed: Optional[int] = None
) -> tuple[List[dict], int]:
    """観点を重複なく n 個サンプリングする。

    観点総数が n 未満なら全数を返す（無音劣化を避け、件数は呼び出し側で検証）。
    再現性のため seed を受け取り、実際に使った seed も返す（attempts に記録する）。

    Returns:
        (サンプリングした観点リスト, 使用した seed)
    """
    meta = get_meta(level, unit_id)
    if meta is None:
        return [], 0
    perspectives = list(meta.get("perspectives", []))
    if seed is None:
        seed = random.randrange(1, 2**31 - 1)
    rng = random.Random(seed)
    k = min(n, len(perspectives))
    sampled = rng.sample(perspectives, k)
    return sampled, seed
