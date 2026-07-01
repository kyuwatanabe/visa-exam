"""RAG出題の生成エンジン。

観点サンプリング → プロンプト構築 → LLM呼び出し → JSON検証 → リトライ、までを担う。
原本テキスト（rag_source）を根拠として渡し、観点1つにつき1問を生成させる。
LLM呼び出しは llm_call 引数で差し替え可能（テストではモックを注入する）。
"""
from __future__ import annotations

import json
import re
import threading as _threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional, Tuple

from backend import rag_perspectives, rag_source
from backend.config import (
    ANTHROPIC_API_KEY,
    CHOICES_BY_LEVEL,
    DIFFICULTY_BY_LEVEL,
    QUESTION_FORMAT_BY_LEVEL,
    RAG_CHOICES,
    RAG_MAX_TOKENS,
    RAG_MAX_TOKENS_MULTI,
    RAG_TAIL_BATCH,
    RAG_GEN_CONCURRENCY,
    RAG_MODEL,
    YESNO_CHOICES,
)

# LLM呼び出しの戻り値: (本文テキスト, usage: {"input_tokens": int, "output_tokens": int})
LLMCall = Callable[..., Tuple[str, dict]]

_SYSTEM_INSTRUCTIONS = (
    "あなたは米国ビザ実務の検定問題を作成する専門家。"
    "以下の「原本」と「観点」に明記された内容のみに基づき問題を作成する。"
    "原本・観点にない事実・数値・条文・呼称・訳語を創作してはならない。"
    "特にビザ種別の名称・通称は原本の表記をそのまま使い、原本にない言い換え"
    "（例: 原本にない『研修者』等の語）を付け加えない。"
    "**原本に『通常』『原則』『多くの場合』『一般に』などの限定・留保の表現が付いている"
    "事項は、その限定表現を必ず残す。限定を削って断定文（『必ず』『常に』『〜に限定される』"
    "等）に変えてはならない。例: 原本が『通常B-1 in lieu of Hは有効期間が半年または1年に"
    "限定されます』なら、問題文・選択肢・解説でも『通常』を保持する。**"
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
    unit_perspectives: Optional[List[dict]] = None,
    multi_assignments: Optional[List[List[dict]]] = None,
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

    if fmt == "multi":
        # 上級（複数選択）は、各設問ごとに割り当てた観点から選択肢を1つずつ作る。
        n_q = len(perspectives)
        lines.append(f"# 出題数\n独立した設問を{n_q}問つくる。")
        lines.append("")
        if multi_assignments:
            lines.append(
                "# 各設問の選択肢に割り当てる観点（この観点から選択肢を作る）\n"
                "各設問は、下に列挙した観点から選択肢を1つずつ作る"
                "（選択肢の順番は観点の順番に対応）。"
            )
            for qi, assigned in enumerate(multi_assignments, 1):
                lines.append(f"\n## 設問{qi}")
                for ci, a in enumerate(assigned, 1):
                    p = a.get("perspective", {})
                    lines.append(
                        f"- 選択肢{ci}（観点: {p.get('name','')}）: {p.get('summary','')}"
                    )
            lines.append("")
        # perspective_id 用に、代表として使える観点idを列挙
        ids = [p.get("id", "") for p in perspectives if p.get("id")]
        if ids:
            lines.append(f"# perspective_id に使える観点id\n{', '.join(ids)}")
            lines.append("")
    else:
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
            "- 設問文は断定文にし、その正誤を問う形にする。文末はです・ます調（丁寧語）で"
            "書く（例：「〜が必要です。」「〜に該当します。」）。「〜である」のような常体は使わない。\n"
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
            "# 出力JSON形式（このスキーマちょうど。questions は上の出題数と同数）\n"
            '{ "questions": [ { "perspective_id": "観点id", '
            '"choice_items": [ { '
            '"true_text": "その観点について原本に照らして正しい記述文", '
            '"true_reason": "それが正しい理由（理由だけ。文頭に正しい/誤りと書かない）", '
            '"false_text": "その観点について一見もっともらしいが原本に照らすと誤りの記述文", '
            '"false_reason": "それが誤りである理由と正しい事実（理由だけ）" '
            f'}} … 全{n_choices}個 ], '
            '"source_pages": [21] } ] }\n'
            "## 最重要ルール（必ず守る）\n"
            f"- **choice_items はちょうど {n_choices} 個。各要素は、上の『各設問の選択肢の"
            "作り方』で割り当てた観点1つに対応する（順番も一致させる）。**\n"
            "- **各要素で、その観点について『正しい記述(true_text)』と『誤った記述(false_text)』の"
            "両方を必ず作る。** true_text は原本に照らして正しい内容。"
            "false_text は一見もっともらしいが、数値・国・条件・要否などを変えて"
            "原本に照らすと誤りにした内容（もっともらしいが確実に誤り）。\n"
            "- true_reason / false_reason は、事実そのものを述べる説明文にする。\n"
            "  * true_reason: なぜ正しいのかを、事実を直接述べて説明する。\n"
            "  * false_reason: 正しい事実を直接述べて、どこが違うのかが分かるようにする。\n"
            "- **禁止事項（解説の書き方）:**\n"
            "  * 資料への言及をしない。『原本』『手引き』『資料』『説明されている』"
            "『記載されている』『明記されている』『列挙している』等の、"
            "どこかに書いてあることを指す表現を使わない。\n"
            "  * メタな言い回しを避ける。『〜という記述は誤りである』『〜は正しい』のように"
            "正誤そのものを述べる文にしない（正誤は別途表示される）。事実だけを述べる。\n"
            "  * 例: ×『対象を米国外に限ると明記されており、この記述は誤りである。』"
            "○『対象は米国外から購入した設備に限られる。購入元の所在地は問われる。』\n"
            "- 文頭に『正しい』『誤り』とは書かない。\n"
            "- 上の割り当てにある『正しい記述にする／誤った記述にする』の指定は無視してよい"
            "（両方作れば、どちらを使うかはこちらで選ぶ）。\n"
            "## その他のルール\n"
            "- perspective_id は『perspective_id に使える観点id』のいずれかを入れる。\n"
            "**原本の文をそのまま引用したり「原本p.◯に『…』と記されており」のような引用形式で"
            "書いたりしてはならない。ページ番号への言及も不要。**"
        )
    else:  # choice
        lines.append(
            "# 出力JSON形式（このスキーマちょうど。questions は上の観点と同数）\n"
            '{ "questions": [ { "perspective_id": "観点id", "question": "…として正しいものはどれですか。", '
            f'"choices": [{"、".join([chr(34)+"選択肢"+str(i+1)+chr(34) for i in range(n_choices)])}], '
            '"answer_index": 0, "explanation": "解説", "source_pages": [21] } ] }\n'
            f"- choices はちょうど {n_choices} 個。\n"
            "- answer_index は0始まり（正答の選択肢の位置）。\n"
            "- 重要: 正答の位置（answer_index）は設問ごとにばらつかせ、特定の位置（0 など）に"
            "偏らせないこと。\n"
            "- 設問文はです・ます調（丁寧語）で書く。語尾は「〜として正しいものはどれですか。」等で"
            "統一し、「〜どれか。」のような常体（だ・である調）は使わない。\n"
            "- 誤答は『ありそうだが原本に照らすと誤り』にする。明らかすぎる誤答は避ける。\n"
            "- explanation（解説）は、なぜその選択肢が正解かを自分の言葉で1〜2文で簡潔に述べる。"
            "**原本の文をそのまま引用したり「原本p.◯に『…』と記されており」のような引用形式で"
            "書いたりしてはならない。ページ番号への言及も不要。**"
        )
    return "\n".join(lines)


# --- 管理画面から編集できるプロンプト追加指示 ---------------------------------
# 質問（問題文・選択肢）と回答（解説）に関する追加指示を管理画面から保存でき、
# 生成プロンプトの末尾に注入する。JSON構造は固定のまま、方針だけを調整する用途。
PROMPT_KEY_QUESTION = "prompt_question_extra"
PROMPT_KEY_ANSWER = "prompt_answer_extra"

_prompt_cache: dict = {"at": 0.0, "question": "", "answer": ""}
_PROMPT_CACHE_TTL = 20  # 秒


def _get_prompt_extras() -> Tuple[str, str]:
    """管理画面で設定された質問／回答の追加指示を返す（短時間キャッシュ）。"""
    now = time.monotonic()
    if now - _prompt_cache["at"] < _PROMPT_CACHE_TTL:
        return _prompt_cache["question"], _prompt_cache["answer"]
    q, a = "", ""
    try:
        from backend import db
        m = db.get_settings_map([PROMPT_KEY_QUESTION, PROMPT_KEY_ANSWER])
        q = (m.get(PROMPT_KEY_QUESTION) or "").strip()
        a = (m.get(PROMPT_KEY_ANSWER) or "").strip()
    except Exception:
        pass
    _prompt_cache.update({"at": now, "question": q, "answer": a})
    return q, a


def _append_prompt_extras(prompt: str) -> str:
    """生成プロンプトの末尾に、管理画面で設定した追加指示を注記として付ける。"""
    q, a = _get_prompt_extras()
    extra = []
    if q:
        extra.append("# 【管理者からの追加指示：質問（問題文・選択肢）】\n" + q)
    if a:
        extra.append("# 【管理者からの追加指示：回答（解説）】\n" + a)
    if not extra:
        return prompt
    return prompt + "\n\n" + "\n\n".join(extra)


def _real_llm_call(system_blocks: list, user_text: str, max_tokens: int = RAG_MAX_TOKENS) -> Tuple[str, dict]:
    """Anthropic Messages API を実呼び出しする。プロンプトキャッシュ利用。"""
    if not ANTHROPIC_API_KEY:
        raise RAGGenerationError(
            "ANTHROPIC_API_KEY が未設定です。RAG出題には API キーが必要です。"
        )
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=RAG_MODEL,
        max_tokens=max_tokens,
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


def _clean_reason(text: str) -> str:
    """解説文から資料への言及を軽く取り除く（文意を壊さない範囲に限定）。

    メタ表現（『〜という記述は誤りである』等）の除去はプロンプト側に任せ、
    ここでは文意を壊しにくい『資料に言及する言い回し』だけを整える。
    """
    if not text:
        return ""
    s = text.strip()
    # 文頭の「原本は」「原本では」「原本に照らして（は）」等を除去
    s = re.sub(r"^原本(に照らして|によれば|によると|では|は|に)?[、。]?\s*", "", s)
    s = re.sub(r"原本(に照らして|に照らすと|上|では|には|は|の記載では)?", "", s)
    # 末尾の「〜と説明されている／記載されている／示されている」→ 言い切りに（意味は保つ）
    s = re.sub(r"と(説明|記載|明記|明示)されて(いる|おり)([。\.]?)\s*$", r"\3", s)
    s = re.sub(r"と(説明|記載|明記|明示)されて(いる|おり)、", "、", s)
    # 句読点の整理
    s = re.sub(r"。+", "。", s).strip("、， 　")
    if s and not s.endswith("。"):
        s += "。"
    return s.strip()


def _validate_multi(
    i: int,
    q: dict,
    expected_choices: int,
    unit_name: str = "",
    truth: Optional[List[bool]] = None,
) -> dict:
    """複数選択（上級）の1問を検証し、内部形式に正規化する。

    choice_items（text/correct/reason を1組）方式を優先。正解位置は、サーバーが
    事前決定した truth（各選択肢の正誤目標）があればそれを採用し、無ければ
    LLMの correct フラグから導出する。これにより正解数（1〜2個）が必ず保証される。
    設問文は単元名を用いた固定文に強制上書きする。
    """
    items = q.get("choice_items")
    if isinstance(items, list) and items and isinstance(items[0], dict) and (
        "true_text" in items[0] or "false_text" in items[0]
    ):
        # 新方式: 各観点について正しい版/誤り版の両方を受け取り、
        # サーバーの truth に応じてどちらを使うか選ぶ（内容とラベルが必ず一致）。
        if len(items) != expected_choices:
            raise ValueError(
                f"questions[{i}].choice_items は {expected_choices} 個必要（実際 {len(items)}）"
            )
        # truth が無ければ、各要素を正しい版で埋めて最初の1つだけ誤り版…では不自然なので、
        # truth が無い場合はここでランダムに1〜2個を正解に決める。
        if not (truth and len(truth) == expected_choices):
            import random as _r
            k = _r.choice([1, 2])
            pos = set(_r.sample(range(expected_choices), k))
            truth = [j in pos for j in range(expected_choices)]

        choices, reasons = [], []
        actual_truth: List[bool] = []  # 実際に使った版に合わせた正誤
        for j, it in enumerate(items):
            if not isinstance(it, dict):
                raise ValueError(f"questions[{i}].choice_items[{j}] が dict でない")
            true_text = (it.get("true_text") or "").strip()
            false_text = (it.get("false_text") or "").strip()
            true_reason = (it.get("true_reason") or "").strip()
            false_reason = (it.get("false_reason") or "").strip()
            want_true = bool(truth[j])
            # 割り当てた版を優先。欠けていれば、もう片方の版を使い正誤を実態に合わせる。
            if want_true and true_text:
                text, reason, used_true = true_text, true_reason, True
            elif (not want_true) and false_text:
                text, reason, used_true = false_text, false_reason, False
            elif true_text:  # フォールバック（正しい版のみある）
                text, reason, used_true = true_text, true_reason, True
            elif false_text:  # フォールバック（誤り版のみある）
                text, reason, used_true = false_text, false_reason, False
            else:
                raise ValueError(
                    f"questions[{i}].choice_items[{j}] に有効な記述文がない"
                )
            choices.append(text)
            reasons.append(_clean_reason(reason))
            actual_truth.append(used_true)

        # 実態の正誤で正解位置を確定。1〜2個の範囲に収まるよう調整する。
        correct_idx = [j for j, t in enumerate(actual_truth) if t]
        if len(correct_idx) == 0:
            # 正解が0個: 正しい版が使える選択肢を1つ正解に昇格
            for j, it in enumerate(items):
                tt = (it.get("true_text") or "").strip()
                if tt:
                    choices[j] = tt
                    reasons[j] = _clean_reason(it.get("true_reason") or "")
                    actual_truth[j] = True
                    break
        elif len(correct_idx) > 2:
            # 正解が3個以上: 先頭2個だけ正解に残し、他は誤り版に差し替え
            keep = set(correct_idx[:2])
            for j in correct_idx[2:]:
                ft = (items[j].get("false_text") or "").strip()
                if ft:
                    choices[j] = ft
                    reasons[j] = _clean_reason(items[j].get("false_reason") or "")
                    actual_truth[j] = False
                # 誤り版が無ければ正解のまま（稀）。keepに含める
                elif j not in keep:
                    keep.add(j)

        norm = sorted([j for j, t in enumerate(actual_truth) if t])
        if not (1 <= len(norm) <= 2):
            raise ValueError(
                f"questions[{i}] の正解は1〜2個であること（実際 {len(norm)}個）"
            )
        choice_explanations = reasons
    else:
        # 旧方式フォールバック
        question = q.get("question")
        choices = q.get("choices")
        answer_indices = q.get("answer_indices")
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
        choices = [c.strip() for c in choices]
        raw_ce = q.get("choice_explanations")
        if isinstance(raw_ce, list):
            choice_explanations = [
                (s.strip() if isinstance(s, str) else "") for s in raw_ce
            ]
            if len(choice_explanations) < expected_choices:
                choice_explanations += [""] * (expected_choices - len(choice_explanations))
            choice_explanations = choice_explanations[:expected_choices]
        else:
            choice_explanations = [""] * expected_choices

    # 設問文は単元名を用いた固定文に強制上書き（テーマ偏りを防ぐ）
    fixed_question = (
        f"「{unit_name}」について、正しいものを1つ、または2つ選んでください。"
        if unit_name
        else "正しいものを1つ、または2つ選んでください。"
    )

    return {
        "perspective_id": q.get("perspective_id", ""),
        "type": "multi",
        "question": fixed_question,
        "choices": choices,
        "answer_indices": norm,  # 0始まり、1〜2個
        "choice_explanations": choice_explanations,
        "explanation": "",
        "source_pages": q.get("source_pages", []),
    }


def _parse_and_validate(
    raw: str,
    fmt: str,
    expected_choices: int,
    unit_name: str = "",
    multi_truth: Optional[List[List[bool]]] = None,
) -> List[dict]:
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
            truth = multi_truth[i] if (multi_truth and i < len(multi_truth)) else None
            out.append(_validate_multi(i, q, expected_choices, unit_name, truth))
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
    progress_cb: Optional[Callable[[int], None]] = None,
) -> dict:
    """観点リストから問題を生成する（バッチ外殻）。

    観点数が多い（＝1回の生成が重い）場合は、RAG_TAIL_BATCH 問ずつに分割して
    並列に生成し、結果を結合して返す。progress_cb が渡された場合、各バッチ完了ごとに
    「これまでに出来た設問数（累積）」を通知する（進捗表示用）。
    """
    batch = max(1, RAG_TAIL_BATCH)
    if len(perspectives) <= batch:
        part = _generate_questions_batch(
            level, unit_id, perspectives, seed=seed,
            llm_call=llm_call, max_retries=max_retries,
        )
        if progress_cb:
            try:
                progress_cb(len(part["questions"]))
            except Exception:
                pass
        return part

    # 複数バッチを並列に生成してトータル時間を短縮する（各バッチは独立）。
    chunks = []
    for start in range(0, len(perspectives), batch):
        chunk = perspectives[start:start + batch]
        chunk_seed = None if seed is None else seed + start
        chunks.append((start, chunk, chunk_seed))

    results: dict = {}
    first_error: Optional[Exception] = None
    done_count = 0
    prog_lock = _threading.Lock()

    def _run(idx: int, chunk: List[dict], chunk_seed: Optional[int]):
        return idx, _generate_questions_batch(
            level, unit_id, chunk, seed=chunk_seed,
            llm_call=llm_call, max_retries=max_retries,
        )

    max_workers = min(RAG_GEN_CONCURRENCY, len(chunks))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_run, i, c, s) for (i, (start, c, s)) in enumerate(chunks)]
        for fut in as_completed(futures):
            try:
                idx, part = fut.result()
                results[idx] = part
                if progress_cb:
                    with prog_lock:
                        done_count += len(part["questions"])
                        try:
                            progress_cb(done_count)
                        except Exception:
                            pass
            except Exception as e:  # noqa: BLE001
                if first_error is None:
                    first_error = e

    if first_error is not None:
        # 1バッチでも失敗したら全体を失敗扱い（部分検定にしない）。
        raise first_error

    all_questions: List[dict] = []
    merged_metrics: dict = {}
    for idx in range(len(chunks)):
        part = results[idx]
        all_questions.extend(part["questions"])
        merged_metrics = merge_metrics(merged_metrics, part.get("metrics", {}))
    return {"questions": all_questions, "metrics": merged_metrics}


def _generate_questions_batch(
    level: str,
    unit_id: str,
    perspectives: List[dict],
    seed: Optional[int] = None,
    llm_call: Optional[LLMCall] = None,
    max_retries: int = 2,
) -> dict:
    """与えられた観点リストから問題を生成する（1バッチ＝1回のLLM呼び出し）。

    渡された観点ちょうどの数だけ問題を返す（多く返ってきたら先頭で切り詰める）。
    原本テキストはこの観点群から都度組み立てる。

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
    # 上級（multi）は選択肢を単元全体の多様な観点から作るため、
    # 出題観点だけでなく単元の全観点名も検索キーワードに加えて原本を広く引く。
    max_pages = 8
    if fmt == "multi":
        for p in meta.get("perspectives", []):
            if p.get("name"):
                keywords.append(p["name"])
        max_pages = 12
    source_text = rag_source.text_for_keywords(keywords, max_pages=max_pages)
    grounding = "pdf" if source_text else "summary"

    # 上級（multi）は、各設問ごとに単元の全観点から n_choices 個を
    # ランダムに選び、選択肢を観点に1対1で割り当てる（分散を確実にする）。
    multi_assignments = None
    if fmt == "multi":
        import random as _random
        pool = [p for p in meta.get("perspectives", []) if p.get("name")]
        rng = _random.Random(seed)
        multi_assignments = []
        for _ in range(want):
            if len(pool) >= n_choices:
                picked = rng.sample(pool, n_choices)
            else:
                # 観点が足りない場合は重複を許して埋める
                picked = [rng.choice(pool) for _ in range(n_choices)]
            # この設問の正解数（1 or 2）と、正解にする選択肢位置をサーバー側で確定する。
            # これによりLLMに個数を守らせる必要がなくなり、生成が安定する。
            k = rng.choice([1, 2])
            correct_positions = set(rng.sample(range(n_choices), k))
            assigned = []
            for idx, p in enumerate(picked):
                assigned.append({
                    "perspective": p,
                    "should_be_correct": idx in correct_positions,
                })
            multi_assignments.append(assigned)

    # 各設問の正誤目標（truth）を割り当てから抽出。検証時の正解確定に使う。
    multi_truth = None
    if multi_assignments:
        multi_truth = [
            [bool(a.get("should_be_correct")) for a in assigned]
            for assigned in multi_assignments
        ]

    user_prompt = _build_user_prompt(
        level=level,
        unit_name=meta.get("unit_name", unit_id),
        level_description=meta.get("level_description", ""),
        perspectives=perspectives,
        fmt=fmt,
        n_choices=n_choices,
        unit_perspectives=meta.get("perspectives", []),
        multi_assignments=multi_assignments,
    )
    # 管理画面で設定した質問／回答の追加指示を末尾に注入する。
    user_prompt = _append_prompt_extras(user_prompt)
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
    max_tokens = RAG_MAX_TOKENS_MULTI if fmt == "multi" else RAG_MAX_TOKENS

    start = time.monotonic()
    usage = {"input_tokens": None, "output_tokens": None}
    last_err: Optional[Exception] = None
    questions: List[dict] = []
    attempts_used = 0
    for attempt in range(max_retries + 1):
        attempts_used = attempt + 1
        try:
            raw, usage = call(system_blocks, user_prompt, max_tokens)
            parsed = _parse_and_validate(
                raw, fmt, n_choices, meta.get("unit_name", unit_id), multi_truth
            )
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
