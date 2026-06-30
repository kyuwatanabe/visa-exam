"""RAG出題の生成エンジン。

観点サンプリング → プロンプト構築 → LLM呼び出し → JSON検証 → リトライ、までを担う。
原本テキスト（rag_source）を根拠として渡し、観点1つにつき1問を生成させる。
LLM呼び出しは llm_call 引数で差し替え可能（テストではモックを注入する）。
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, List, Optional, Tuple

from backend import rag_perspectives, rag_source
from backend.config import (
    ANTHROPIC_API_KEY,
    CHOICES_BY_LEVEL,
    DIFFICULTY_BY_LEVEL,
    QUESTION_FORMAT_BY_LEVEL,
    RAG_CHOICES,
    RAG_MAX_TOKENS,
    RAG_MODEL,
    YESNO_CHOICES,
)

# LLM呼び出しの戻り値: (本文テキスト, usage: {"input_tokens": int, "output_tokens": int})
LLMCall = Callable[[list, str], Tuple[str, dict]]

_SYSTEM_INSTRUCTIONS = (
    "あなたは米国ビザ実務の検定問題を作成する専門家。"
    "以下の「原本」と「観点」に明記された内容のみに基づき問題を作成する。"
    "原本・観点にない事実・数値・条文・呼称・訳語を創作してはならない。"
    "特にビザ種別の名称・通称は原本の表記をそのまま使い、原本にない言い換え"
    "（例: 原本にない『研修者』等の語）を付け加えない。"
    "難度は指定レベルに厳密に合わせる。"
    "解説は根拠を自分の言葉で1〜2文で簡潔に述べる。原本の文の引用やページ番号への言及はしない。"
    "出力は指定のJSONのみ。前後の説明文やMarkdownのコードフェンスを一切付けない。"
)


def _format_for_level(level: str) -> str:
    """レベル→出題形式（yesno / choice）。未知レベルは choice。"""
    return QUESTION_FORMAT_BY_LEVEL.get(level, "choice")


def _choices_for_level(level: str) -> int:
    """レベル→選択肢数。未設定は全体既定 RAG_CHOICES。"""
    return CHOICES_BY_LEVEL.get(level, RAG_CHOICES)


class RAGGenerationError(Exception):
    """RAG生成に失敗したことを表す（API未設定・JSON不正・件数不足など）。"""


def _build_user_prompt(
    level: str,
    unit_name: str,
    level_description: str,
    perspectives: List[dict],
    fmt: str,
    n_choices: int,
) -> str:
    """LLMへ渡すユーザープロンプトを組み立てる（出題形式 fmt で出力スキーマを切替）。

    原本テキストはキャッシュ効率のため system 側（キャッシュ対象ブロック）に置き、
    ここには難度・単元・観点・出力形式だけを入れる。
    """
    lines: List[str] = []
    lines.append(f"# 難度\n{level} … {level_description}")
    difficulty = DIFFICULTY_BY_LEVEL.get(level)
    if difficulty:
        lines.append(f"\n# 難易度の方針\n{difficulty}")
    lines.append("")
    lines.append(f"# 単元\n{unit_name}")
    lines.append("")
    lines.append(f"# 出題する観点（各観点につき1問、計{len(perspectives)}問）")
    for p in perspectives:
        pages = ",".join(str(x) for x in p.get("source_pages", []))
        lines.append(
            f"- {p['id']}: {p['name']} / {p.get('summary','')} / 根拠ページ {pages}"
        )
    lines.append("")

    if fmt == "yesno":
        lines.append(
            "# 出力JSON形式（このスキーマちょうど。questions は上の観点と同数）\n"
            '{ "questions": [ { "perspective_id": "観点id", '
            '"question": "はい/いいえで答えられる断定文の設問", '
            '"answer": "yes", "explanation": "解説", "source_pages": [21] } ] }\n'
            "- 初級。各設問は「はい / いいえ」のいずれかで答えられる二択にする。\n"
            "- 設問文は断定文（〜である／〜が必要である 等）にし、その正誤を問う形にする。\n"
            '- answer は "yes"（その断定が正しい）/ "no"（誤り）のいずれかちょうど。\n'
            '- 重要: この一連の設問で "yes" と "no" がおおむね半々になるよう、正しい断定文と'
            "誤った断定文を意図的に混ぜること（「はい」に偏らせない）。誤りの断定文は原本に"
            "照らして明確に誤っている内容にする（曖昧・引っかけ過ぎは避ける）。\n"
            "- 選択肢（choices）は出力しない。はい/いいえは固定で付与される。"
        )
    elif fmt == "fill_in":
        lines.append(
            "# 出力JSON形式（このスキーマちょうど。questions は上の観点と同数）\n"
            '{ "questions": [ { "perspective_id": "観点id", '
            '"source_sentence": "原本からそのまま抜き出した一文（語句を伏せない完全な文）", '
            '"question": "上のsource_sentenceの重要語句を ____ で伏せた文", '
            '"blanks": [ { "variants": ["正解の表記", "表記揺れ1", "略称など"] } ], '
            '"source_pages": [21] } ] }\n'
            "- 上級。**必ず原本テキスト中に実在する一文をそのまま source_sentence に抜き出すこと。"
            "言い換え・要約・新しい文の作文は禁止。**\n"
            "- question は、その source_sentence の重要語句を1〜2箇所、半角アンダースコア4つ"
            "「____」で伏せたものにする（伏せる以外は source_sentence と一字一句同じにする）。\n"
            "- 空欄の数だけ blanks を文中の出現順に並べる（1〜2個）。\n"
            "- 各空欄の variants には、伏せた語句の正解表記と、その表記揺れ候補（漢字／ひらがな／"
            "カタカナ、略称、別称、英字略号など、解答として正答扱いすべき表記）を併記する。\n"
            "- explanation は出力しない。choices や answer_index も出力しない。"
        )
    elif fmt == "multi":
        lines.append(
            "# 出力JSON形式（このスキーマちょうど。questions は上の観点と同数）\n"
            '{ "questions": [ { "perspective_id": "観点id", '
            '"question": "次のうち正しいものをすべて選びなさい。", '
            f'"choices": [{"、".join([chr(34)+"選択肢"+str(i+1)+chr(34) for i in range(n_choices)])}], '
            '"answer_indices": [0, 2], "explanation": "解説", "source_pages": [21] } ] }\n'
            f"- choices はちょうど {n_choices} 個。\n"
            "- answer_indices は0始まりの配列で、**正しい選択肢を1〜2個**含める"
            "（必ず1個以上2個以下）。残りは誤答にする。\n"
            "- 重要: 正答が1個の設問と2個の設問を混在させ、正答の個数・位置を設問ごとに"
            "ばらつかせること（毎回2個などに偏らせない）。\n"
            "- 上級なので誤答は『一見もっともらしいが原本に照らすと誤り』にし、正答との差を"
            "細部に置く。明らかに無関係な選択肢ばかりにしない。\n"
            "- 設問文は「次のうち正しいものをすべて選びなさい。」等に統一する"
            "（正答の個数は文中に書かない）。\n"
            "- explanation（解説）は、どれが正しくなぜかを自分の言葉で1〜2文で簡潔に述べる。"
            "**原本の文をそのまま引用したり「原本p.◯に『…』と記されており」のような引用形式で"
            "書いたりしてはならない。ページ番号への言及も不要。**"
        )
    else:  # choice
        lines.append(
            "# 出力JSON形式（このスキーマちょうど。questions は上の観点と同数）\n"
            '{ "questions": [ { "perspective_id": "観点id", "question": "…として正しいものはどれか。", '
            f'"choices": [{"、".join([chr(34)+"選択肢"+str(i+1)+chr(34) for i in range(n_choices)])}], '
            '"answer_index": 0, "explanation": "解説", "source_pages": [21] } ] }\n'
            f"- choices はちょうど {n_choices} 個。\n"
            "- answer_index は0始まり（正答の選択肢の位置）。\n"
            "- 重要: 正答の位置（answer_index）は設問ごとにばらつかせ、特定の位置（0 など）に"
            "偏らせないこと。\n"
            "- 設問文の語尾は「〜として正しいものはどれか。」等で統一する。\n"
            "- 誤答は『ありそうだが原本に照らすと誤り』にする。明らかすぎる誤答は避ける。\n"
            "- explanation（解説）は、なぜその選択肢が正解かを自分の言葉で1〜2文で簡潔に述べる。"
            "**原本の文をそのまま引用したり「原本p.◯に『…』と記されており」のような引用形式で"
            "書いたりしてはならない。ページ番号への言及も不要。**"
        )
    return "\n".join(lines)


def _real_llm_call(system_blocks: list, user_text: str) -> Tuple[str, dict]:
    """Anthropic Messages API を実呼び出しする。プロンプトキャッシュ利用。"""
    if not ANTHROPIC_API_KEY:
        raise RAGGenerationError(
            "ANTHROPIC_API_KEY が未設定です。RAG出題には API キーが必要です。"
        )
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=RAG_MODEL,
        max_tokens=RAG_MAX_TOKENS,
        system=system_blocks,
        messages=[{"role": "user", "content": user_text}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    usage = {
        "input_tokens": getattr(resp.usage, "input_tokens", None),
        "output_tokens": getattr(resp.usage, "output_tokens", None),
    }
    return text, usage


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    return text


def _validate_choice(i: int, q: dict, expected_choices: int) -> dict:
    """選択式（中級）の1問を検証し、内部形式に正規化する。"""
    question = q.get("question")
    choices = q.get("choices")
    answer_index = q.get("answer_index")
    if not isinstance(question, str) or not question.strip():
        raise ValueError(f"questions[{i}].question が不正")
    if not isinstance(choices, list) or len(choices) != expected_choices:
        raise ValueError(
            f"questions[{i}].choices は {expected_choices} 個必要"
            f"（実際 {len(choices) if isinstance(choices, list) else 'N/A'}）"
        )
    if not all(isinstance(c, str) and c.strip() for c in choices):
        raise ValueError(f"questions[{i}].choices に空文字が含まれる")
    if not isinstance(answer_index, int) or not (0 <= answer_index < expected_choices):
        raise ValueError(f"questions[{i}].answer_index が範囲外")
    return {
        "perspective_id": q.get("perspective_id", ""),
        "type": "choice",
        "question": question.strip(),
        "choices": [c.strip() for c in choices],
        "answer": answer_index,  # 0始まり
        "explanation": (q.get("explanation") or "").strip(),
        "source_pages": q.get("source_pages", []),
    }


def _validate_yesno(i: int, q: dict) -> dict:
    """Yes/No（初級）の1問を検証し、はい/いいえ固定の選択式に正規化する。"""
    question = q.get("question")
    answer = q.get("answer")
    if not isinstance(question, str) or not question.strip():
        raise ValueError(f"questions[{i}].question が不正")
    if not isinstance(answer, str) or answer.strip().lower() not in ("yes", "no"):
        raise ValueError(f"questions[{i}].answer は 'yes' / 'no' のいずれか")
    answer_index = 0 if answer.strip().lower() == "yes" else 1  # はい=0 / いいえ=1
    return {
        "perspective_id": q.get("perspective_id", ""),
        "type": "choice",  # 採点は選択式を流用（はい/いいえの2択）
        "question": question.strip(),
        "choices": list(YESNO_CHOICES),
        "answer": answer_index,
        "explanation": (q.get("explanation") or "").strip(),
        "source_pages": q.get("source_pages", []),
    }


def _validate_fill_in(i: int, q: dict) -> dict:
    """穴埋め（上級）の1問を検証し、内部形式に正規化する。"""
    question = q.get("question")
    blanks = q.get("blanks")
    if not isinstance(question, str) or not question.strip():
        raise ValueError(f"questions[{i}].question が不正")
    if not isinstance(blanks, list) or not (1 <= len(blanks) <= 2):
        raise ValueError(f"questions[{i}].blanks は1〜2個の配列が必要")
    norm_blanks = []
    for j, b in enumerate(blanks):
        if not isinstance(b, dict):
            raise ValueError(f"questions[{i}].blanks[{j}] が dict でない")
        variants = b.get("variants")
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"questions[{i}].blanks[{j}].variants が空")
        clean = [v.strip() for v in variants if isinstance(v, str) and v.strip()]
        if not clean:
            raise ValueError(f"questions[{i}].blanks[{j}].variants に有効な候補がない")
        norm_blanks.append({"variants": clean})

    # 原文（空欄が埋まった完全文）。LLMが付けなければ question の ____ を
    # 各空欄の代表表記で埋めて復元する。
    source_sentence = q.get("source_sentence")
    if isinstance(source_sentence, str) and source_sentence.strip():
        source_sentence = source_sentence.strip()
    else:
        filled = question.strip()
        for b in norm_blanks:
            filled = filled.replace("____", b["variants"][0], 1)
        source_sentence = filled

    return {
        "perspective_id": q.get("perspective_id", ""),
        "type": "fill_in",
        "question": question.strip(),
        "blanks": norm_blanks,
        "source_sentence": source_sentence,
        "explanation": "",
        "source_pages": q.get("source_pages", []),
    }


def _validate_multi(i: int, q: dict, expected_choices: int) -> dict:
    """複数選択（上級）の1問を検証し、内部形式に正規化する。

    choices ちょうど expected_choices 個、answer_indices は1〜2個の正答位置。
    """
    question = q.get("question")
    choices = q.get("choices")
    answer_indices = q.get("answer_indices")
    if not isinstance(question, str) or not question.strip():
        raise ValueError(f"questions[{i}].question が不正")
    if not isinstance(choices, list) or len(choices) != expected_choices:
        raise ValueError(
            f"questions[{i}].choices は {expected_choices} 個必要"
            f"（実際 {len(choices) if isinstance(choices, list) else 'N/A'}）"
        )
    if not all(isinstance(c, str) and c.strip() for c in choices):
        raise ValueError(f"questions[{i}].choices に空文字が含まれる")
    if not isinstance(answer_indices, list) or not (1 <= len(answer_indices) <= 2):
        raise ValueError(f"questions[{i}].answer_indices は1〜2個の配列が必要")
    norm = sorted({a for a in answer_indices if isinstance(a, int)})
    if not norm or not all(0 <= a < expected_choices for a in norm):
        raise ValueError(f"questions[{i}].answer_indices に範囲外の値がある")
    if not (1 <= len(norm) <= 2):
        raise ValueError(f"questions[{i}].answer_indices は重複排除後も1〜2個であること")
    return {
        "perspective_id": q.get("perspective_id", ""),
        "type": "multi",
        "question": question.strip(),
        "choices": [c.strip() for c in choices],
        "answer_indices": norm,  # 0始まり、1〜2個
        "explanation": (q.get("explanation") or "").strip(),
        "source_pages": q.get("source_pages", []),
    }


def _parse_and_validate(raw: str, fmt: str, expected_choices: int) -> List[dict]:
    """LLM応答JSONをパースし、出題形式 fmt に応じて検証・正規化する。不正なら ValueError。"""
    data = json.loads(_strip_fences(raw))
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions 配列が空または不正")
    out = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            raise ValueError(f"questions[{i}] が dict でない")
        if fmt == "yesno":
            out.append(_validate_yesno(i, q))
        elif fmt == "fill_in":
            out.append(_validate_fill_in(i, q))
        elif fmt == "multi":
            out.append(_validate_multi(i, q, expected_choices))
        else:
            out.append(_validate_choice(i, q, expected_choices))
    return out


def generate_questions(
    level: str,
    unit_id: str,
    perspectives: List[dict],
    seed: Optional[int] = None,
    llm_call: Optional[LLMCall] = None,
    max_retries: int = 2,
) -> dict:
    """与えられた観点リストから問題を生成する（サンプリングは呼び出し側の責務）。

    ヘッド／テイル分割（開始の体感待ち短縮）のため、サンプリングと生成を分離した。
    渡された観点ちょうどの数だけ問題を返す（多く返ってきたら先頭で切り詰める）。
    原本テキストはこの観点群の source_pages から都度組み立てる。

    ただし LLM 呼び出しの経路自体は本番と同一（配線の動作確認を兼ねる）。

    Args:
        seed: メトリクス記録用に渡された seed をそのまま載せるだけ（生成には未使用）。

    Returns:
        {"questions": [...], "metrics": {...}}
        questions は answer/explanation を含む内部形式。
    Raises:
        RAGGenerationError: 観点メタ不在・観点0件・API未設定・検証失敗の最終リトライ超過など。
    """
    meta = rag_perspectives.get_meta(level, unit_id)
    if meta is None:
        raise RAGGenerationError(
            f"観点メタがありません: level={level}, unit={unit_id}"
        )
    if not perspectives:
        raise RAGGenerationError(
            f"観点が0件です: level={level}, unit={unit_id}"
        )
    want = len(perspectives)
    fmt = _format_for_level(level)
    n_choices = _choices_for_level(level)

    # 根拠テキスト: 観点の語句（name/summary）と単元名で原本を全文検索して引く。
    # 観点メタの source_pages は元PDF基準でアップロード版とズレるため使わない。
    keywords: List[str] = [meta.get("unit_name", unit_id)]
    for p in perspectives:
        if p.get("name"):
            keywords.append(p["name"])
        # summary は語句を分割して拾う（句点・読点・スペースで割る）
        summ = p.get("summary", "") or ""
        for frag in re.split(r"[、。\s・／/（）()]+", summ):
            frag = frag.strip()
            if len(frag) >= 2:
                keywords.append(frag)
    source_text = rag_source.text_for_keywords(keywords, max_pages=8)
    grounding = "pdf" if source_text else "summary"

    user_prompt = _build_user_prompt(
        level=level,
        unit_name=meta.get("unit_name", unit_id),
        level_description=meta.get("level_description", ""),
        perspectives=perspectives,
        fmt=fmt,
        n_choices=n_choices,
    )
    # システムブロック: 指示は静的。原本テキストは大きく同一単元の連続生成で
    # 使い回せるため、キャッシュ対象（ephemeral）ブロックとして置く。
    # ヘッド生成でキャッシュが温まり、テイル生成では原本入力が実質タダになる。
    system_blocks = [{"type": "text", "text": _SYSTEM_INSTRUCTIONS}]
    if source_text:
        system_blocks.append(
            {
                "type": "text",
                "text": f"# 原本（この記述の範囲だけを使う）\n{source_text}",
                "cache_control": {"type": "ephemeral"},
            }
        )

    call = llm_call or _real_llm_call

    start = time.monotonic()
    usage = {"input_tokens": None, "output_tokens": None}
    last_err: Optional[Exception] = None
    questions: List[dict] = []
    attempts_used = 0
    for attempt in range(max_retries + 1):
        attempts_used = attempt + 1
        try:
            raw, usage = call(system_blocks, user_prompt)
            parsed = _parse_and_validate(raw, fmt, n_choices)
            if len(parsed) < want:
                # 要求した観点数に満たない生成は無音で通さずリトライ対象にする
                raise ValueError(
                    f"生成数が不足（要求 {want} / 実際 {len(parsed)}）"
                )
            questions = parsed[:want]  # 多い場合は先頭で切り詰める
            break
        except RAGGenerationError:
            raise  # API未設定などは即時に上げる
        except Exception as e:  # JSON不正・検証失敗・件数不足はリトライ対象
            last_err = e
            questions = []
    if not questions:
        raise RAGGenerationError(
            f"LLM応答の検証に失敗しました（{attempts_used}回試行）: {last_err}"
        )

    latency_ms = round((time.monotonic() - start) * 1000)
    metrics = {
        "model": RAG_MODEL,
        "latency_ms": latency_ms,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "seed": seed,
        "perspective_ids": [p["id"] for p in perspectives],
        "grounding": grounding,
        "retries": attempts_used - 1,
        "n_choices": n_choices,
        "format": fmt,
    }
    return {"questions": questions, "metrics": metrics}

def merge_metrics(head: dict, tail: dict) -> dict:
    """ヘッド／テイル2回ぶんの生成メトリクスを1つに合算する。

    result 画面が参照するキー（latency_ms・input/output_tokens・perspective_ids 等）は
    全て維持しつつ、トークンは合算・観点idは連結・seedはヘッドのものを採る。
    """
    def _add(a, b):
        if a is None and b is None:
            return None
        return (a or 0) + (b or 0)

    head = head or {}
    tail = tail or {}
    return {
        "model": head.get("model") or tail.get("model"),
        "latency_ms": _add(head.get("latency_ms"), tail.get("latency_ms")),
        "input_tokens": _add(head.get("input_tokens"), tail.get("input_tokens")),
        "output_tokens": _add(head.get("output_tokens"), tail.get("output_tokens")),
        "seed": head.get("seed"),
        "perspective_ids": (head.get("perspective_ids") or [])
        + (tail.get("perspective_ids") or []),
        "grounding": head.get("grounding") or tail.get("grounding"),
        "retries": _add(head.get("retries"), tail.get("retries")),
        "n_choices": head.get("n_choices") or tail.get("n_choices"),
        "format": head.get("format") or tail.get("format"),
        "test": bool(head.get("test") or tail.get("test")),
        # 内訳（参考。result 画面は未参照でも害なし）
        "head_latency_ms": head.get("latency_ms"),
        "tail_latency_ms": tail.get("latency_ms"),
    }
