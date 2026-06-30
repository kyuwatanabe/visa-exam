"""採点の単一の計算源（derive方式）。

ここは純粋関数のみ。DBに触らない。
「素の採点（answers[].is_correct）」と「認容済みチャレンジの裁定（resolution）」から、
実効スコアをその場で計算する。保存された score / total / perfect_count は、この計算結果の
キャッシュにすぎず真実源ではない（db 側が変更のたびにここで計算し直して上書きする）。

裁定の意味:
  - "void"    … その設問を集計から除外する（分母から外す。例: 9/10 で1問 void → 9/9 や 8/9）
  - "correct" … その設問を正解扱いにする（誤答→正解で +1。既に正解ならそのまま）
"""
from typing import Dict, List, Optional


def effective_attempt(
    raw_answers: List[dict],
    resolutions_by_qid: Dict[str, str],
) -> dict:
    """1回の受験の実効スコアを計算する（純粋関数）。

    Args:
        raw_answers: submit 時に確定した素の採点。各要素は少なくとも
            {"id": str, "is_correct": bool} を持つ（以後この is_correct は書き換えない）。
        resolutions_by_qid: {question_id: "correct" | "void"}（認容済みチャレンジの裁定）。

    Returns:
        {
          "score": int, "total": int, "is_perfect": bool,
          "items": [{"id", "raw_is_correct", "voided", "effective_is_correct"}],
        }
    """
    items = []
    score = 0
    total = 0
    for a in raw_answers:
        qid = a.get("id")
        raw_correct = bool(a.get("is_correct"))
        res = resolutions_by_qid.get(qid)
        voided = res == "void"
        eff_correct = raw_correct or (res == "correct")
        if not voided:
            total += 1
            if eff_correct:
                score += 1
        items.append(
            {
                "id": qid,
                "raw_is_correct": raw_correct,
                "voided": voided,
                "effective_is_correct": eff_correct,
            }
        )
    return {
        "score": score,
        "total": total,
        "is_perfect": total > 0 and score == total,
        "items": items,
    }
