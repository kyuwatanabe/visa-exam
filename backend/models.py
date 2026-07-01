"""APIスキーマ定義"""
from typing import List, Optional
from pydantic import BaseModel, Field


class Answer(BaseModel):
    id: str
    choice: Optional[int] = None  # 選択式（初級Yes/No・中級）の0始まり選択
    choices: Optional[List[int]] = None  # 複数選択（上級）の0始まり選択の集合
    text_answers: Optional[List[str]] = None  # 旧穴埋め（廃止）の各空欄の入力


class SubmitRequest(BaseModel):
    level: str
    unit: Optional[str] = None  # 単元ID
    answers: List[Answer]
    # 採点はこのセッションの正答辞書を引く（RAG出題専用）。
    session_id: str


class CheckRequest(BaseModel):
    """1問だけの即時正誤判定リクエスト（履歴・進捗には一切影響しない）。"""
    id: str
    choice: Optional[int] = None        # 選択式（初級Yes/No・中級）の0始まり選択
    choices: Optional[List[int]] = None  # 複数選択（上級）の0始まり選択の集合
    text_answers: Optional[List[str]] = None  # 旧穴埋め（廃止）の各空欄の入力
    # 正答はこのセッションから引く（RAG出題専用）。
    session_id: str


class RagStartRequest(BaseModel):
    """RAG出題の開始リクエスト。観点をサンプリングしてLLMで生成する。"""
    level: str
    unit: str


class RagContinueRequest(BaseModel):
    """RAG出題のテイル（残り問題）生成リクエスト。

    開始時に発行された session_id を渡し、未消化のテイル観点から残り問題を
    生成・追記する（ヘッド／テイル分割）。
    """
    session_id: str


class ChallengeCreateRequest(BaseModel):
    """出題・採点への異議申し立て（チャレンジ）の起票リクエスト。

    受験中の解説パネルから起票する。設問スナップショットはサーバ側でセッションから
    生成するため、ここでは設問の特定情報（session_id・question_id）と申し立て理由、
    および起票時点の自分の解答（任意・スナップショットの参考用）だけを受ける。
    """
    session_id: str
    question_id: str
    reason: str = Field(..., min_length=1, max_length=1000)
    kind: Optional[str] = None                # 'grading' | 'content' | 'both'（分類・任意）
    choice: Optional[int] = None              # 起票時点の自分の解答（選択式）
    choices: Optional[List[int]] = None       # 起票時点の自分の解答（複数選択・上級）
    text_answers: Optional[List[str]] = None  # 起票時点の自分の解答（穴埋め）
