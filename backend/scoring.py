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


def multi_question_correct_after_rulings(
    user_choices: List[int],
    correct_choices: List[int],
    n_choices: int,
    rulings_by_index: Dict[int, str],
) -> bool:
    """上級（複数選択）1問が、選択肢単位の裁定を適用した後に「正解（満点）」になるか。

    各選択肢について「自分の対応が正しいか」を判定する:
      - 正しい記述(correct_choices に含む)は「選んでいれば正しい対応」
      - 誤った記述は「選んでいなければ正しい対応」
    裁定(rulings_by_index[index]):
      - "correct" … その選択肢の自分の対応を正しいとみなす（強制的にOK）
      - "void"    … その選択肢を判定から除外する
      - "reject" / 無指定 … 元の対応のまま
    非除外の全選択肢で対応が正しければ True（＝設問正解）。
    """
    picked = set(user_choices or [])
    correct = set(correct_choices or [])
    all_ok = True
    counted = 0
    for i in range(n_choices):
        ruling = rulings_by_index.get(i)
        if ruling == "void":
            continue                 # ノーカウント: 判定から除外
        counted += 1
        if ruling == "correct":
            continue                 # 正解扱い: この選択肢はOK
        handled_ok = (i in picked) == (i in correct)
        if not handled_ok:
            all_ok = False
    return counted > 0 and all_ok


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
