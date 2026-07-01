"""アプリ全体の設定値・定数。

環境変数依存の設定とマジックナンバーをここへ集約する。RAG出題専用。
"""
import os
from pathlib import Path

# --- パス ---
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
PERSPECTIVES_DIR = Path(__file__).parent / "perspectives"
SOURCE_DIR = Path(__file__).parent / "source"

# --- 難易度レベル ---
ALLOWED_LEVELS = ("beginner", "intermediate", "advanced")

# --- 管理者トークン（URL難読化のみ。環境変数で差し替え可能）---
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "Kp7vQm2xRt")

# --- 出題・採点のルール ---
UNIT_CLEAR_REQUIRED_STREAK = 3      # 単元クリアに必要な通算満点回数（累計方式。外しても減らない）

# 出題対象の単元（当面、ビザ種別の単元のみに限定する）。
# 永住権(green_card)・ビザの基本(basics)など、ビザ種別でない単元は単元選択画面から除外する。
# データ・観点・プロンプトは保持しており、ここに unit_id を足せば即座に復帰できる（単一の真実源）。
# 環境変数 VISA_TYPE_UNITS にカンマ区切りで指定すると上書き可能。
VISA_TYPE_UNITS = frozenset(
    u.strip()
    for u in os.environ.get(
        "VISA_TYPE_UNITS",
        "b_visa,e_visa,f_visa,h1b_visa,j_visa,l_visa",
    ).split(",")
    if u.strip()
)

# --- RAG出題方式の設定 ---
# ANTHROPIC_API_KEY 未設定なら RAG 出題は 503 を返す。
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RAG_MODEL = os.environ.get("RAG_MODEL", "claude-sonnet-4-6")
RAG_CHOICES = int(os.environ.get("RAG_CHOICES", "3"))            # 1問あたりの選択肢数（3 or 4）

# レベル別の出題形式（項目5）。
#   yesno   … Yes/No 2択（はい/いいえ固定）。選択式採点を流用する。
#   choice  … 選択式（級別の選択肢数）。選択式採点を流用する。
QUESTION_FORMAT_BY_LEVEL = {
    "beginner": "yesno",
    "intermediate": "choice",
    "advanced": "multi",
}
# レベル別の選択肢数。yesno（初級）は固定2択なので対象外。
#   中級 … 3択（比較的わかりやすい問題）
#   上級 … 5択（紛らわしい問題）
CHOICES_BY_LEVEL = {
    "intermediate": 3,
    "advanced": 5,
}
# レベル別の難易度指示（プロンプトに添える）。誤答の紛らわしさを級で変える。
DIFFICULTY_BY_LEVEL = {
    "intermediate": (
        "比較的わかりやすい問題にする。誤答は原本に照らして明確に誤っている内容にし、"
        "引っかけや紛らわしい表現は避ける。基本的な理解を問うやさしめの難度。"
    ),
    "advanced": (
        "難しい問題にする。誤答は『一見もっともらしいが原本に照らすと誤り』という"
        "紛らわしいものにし、正答と誤答の差を細部に置く。深い理解がないと選べない難度。"
    ),
}
# Yes/No 出題で用いる固定の2択（はい=index0 / いいえ=index1）。
YESNO_CHOICES = ("はい", "いいえ")
RAG_QUESTIONS_PER_QUIZ = int(os.environ.get("RAG_QUESTIONS_PER_QUIZ", "10"))
# 出題開始の体感待ちを縮めるためのヘッド／テイル分割。
# 開始時はまず先頭 RAG_HEAD_COUNT 問だけを生成して即返し（=最初の描画を速く）、
# 残りはユーザーが解いている間に /api/rag/quiz/continue で生成・追記する。
# 1 にすると、開始時の待ちが「1問分」だけになり体感が最短になる。
RAG_HEAD_COUNT = int(os.environ.get("RAG_HEAD_COUNT", "1"))
RAG_SESSION_TTL_SEC = int(os.environ.get("RAG_SESSION_TTL_SEC", "7200"))  # セッション保持（既定2時間）

# --- DEV ONLY（撤去予定）: 管理画面確認用デモデータ生成ボタン -----------------
# ホーム画面右上の「ダミーデータ生成」ボタンと /api/dev/seed-demo を有効化する。
# 構築段階専用。運用移行時は false にするか、routes_dev.py ごと撤去する。
# 撤去箇所: この定数 / backend/routes_dev.py / main.py の include / index.html のボタン
DEMO_SEED_ENABLED = os.environ.get("DEMO_SEED_ENABLED", "true").lower() == "true"
# ------------------------------------------------------------------------------
RAG_MAX_TOKENS = int(os.environ.get("RAG_MAX_TOKENS", "4000"))
# 上級（複数選択）は各選択肢に正しい版・誤り版の両方を生成するため出力が大きい。
# 途中で JSON が切れないよう、上級のヘッド／テイル生成では大きめの上限を使う。
RAG_MAX_TOKENS_MULTI = int(os.environ.get("RAG_MAX_TOKENS_MULTI", "12000"))
# 原本PDFは2-up（1物理ページに論理2ページ）。論理ページ→物理ページの除数。
SOURCE_PDF_PATH = SOURCE_DIR / "visa_guide_v22_1.pdf"
SOURCE_TXT_PATH = SOURCE_DIR / "visa_guide_v22_1.txt"
SOURCE_PAGES_PER_SHEET = int(os.environ.get("SOURCE_PAGES_PER_SHEET", "2"))

# --- チャレンジ（異議申し立て）ステータスの表示ラベル（内部コード→日本語）---
# routes_quiz / routes_admin が参照する単一の真実源（frontend/common.js にも同義の表あり）。
CHALLENGE_STATUS_LABELS = {
    "open": "未処理",
    "accepted": "処理済",
    "closed": "クローズ",
    "rejected": "却下",
}

# 受験者（マイページ）向けの表示ラベル。処理済／クローズは管理用の区別なので
# 利用者には「容認」に集約し、未決定は「確認中」とする。
CHALLENGE_USER_STATUS_LABELS = {
    "open": "確認中",
    "accepted": "容認",
    "closed": "容認",
    "rejected": "却下",
}
