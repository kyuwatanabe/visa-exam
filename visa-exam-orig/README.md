# ビザ検定（RAG出題）

社内向けビザ知識検定アプリの **RAG出題版**。原本「米国ビザ申請の手引き Ver.22.1」と
観点メタ（22ファイル・計412観点）をもとに、出題のたびに **LLMが問題を生成** する。

固定プール方式（事前作成問題のランダム出題）は別リポジトリ
[visa-examination](https://github.com/atsushibanbanji-collab/visa-examination) が担う。
本リポジトリは **RAG方式専用**。

## 出題の流れ

```
原本PDF（該当ページ） + 観点メタ（サンプリング） + 難度 + 単元
  → /api/rag/quiz/start で先頭3問（ヘッド）を生成して即返す
  → 受験中に /api/rag/quiz/continue で残り（テイル）を裏生成・追記（体感待ち短縮）
  → セッション問題プール（quiz_sessions）に正答を伏せて保持
  → /api/quiz/check（即時判定）・/api/quiz/submit（採点）でサーバ側照合
  → attempts / unit_progress に記録（10問満点を「通算3回」で単元クリア＝累計方式）
```

受験者名・難易度・単元を選んで受験する。難易度カードを押すとそのまま単元選択へ進む。

### レベル別の出題形式（難度は答えさせ方で制御）

同じ知識を、レベルごとに異なる解答形式で問う。

| レベル | 形式 | 採点 |
|---|---|---|
| 初級 | Yes/No（はい・いいえの2択） | 選択式照合 |
| 中級 | 選択式（`RAG_CHOICES` 択） | 選択式照合 |
| 上級 | 穴埋め（自由記述・1〜2箇所） | 正規化＋正解候補配列で完全一致 |

上級の採点は、正解の表記揺れ候補を配列で持ち、入力を正規化（NFKC＝全角英数→半角・
前後空白除去・小文字化）してから候補と完全一致で判定する（かな⇄カナの機械変換はせず、
表記揺れは候補配列で吸収）。空欄が複数ある場合は全空欄正解で正解（部分点なし）。

### 単元クリア（累計方式）

同一単元で **10問満点を通算3回** 取るとその単元クリア（そつぎょう）。
満点回数は通算でカウントし、**外しても減らない**（連続である必要はない）。
進捗は `unit_progress.perfect_count` で管理する。

### 出題範囲

当面、出題対象は **ビザ種別の単元のみ**（B・E・F・H-1B・J・Lビザ）。
永住権・ビザの基本など非ビザ種別の単元は単元選択から除外する（データ・観点・プロンプトは保持）。
対象は `config.VISA_TYPE_UNITS` で管理し、フロント・バックエンド双方で絞る。
ビザ種別6単元は初級・中級・上級の各レベルに用意している。

## ディレクトリ構成

```
backend/
  main.py              アプリ組み立て（観点メタ + DBを起動時ロード）
  config.py            定数・環境変数（出題形式・出題範囲・閾値など）
  db.py                永続化（SQLite/PostgreSQL両対応）。attempts / unit_progress / quiz_sessions
  models.py            Pydantic スキーマ
  routes_quiz.py       RAG出題（ヘッド/テイル）・即時判定・採点・履歴・単元一覧
  routes_admin.py      管理系（アカウント一覧＝進捗 / 個別履歴＝正答率 / パスワード再設定）
  routes_auth.py       認証（登録・ログイン・ログアウト・me・パスワード変更）
  auth.py              認証コア（PBKDF2ハッシュ・Cookieセッション）
  routes_dev.py        デモデータ生成（DEV ONLY・撤去予定。冒頭に撤去手順を記載）
  rag_perspectives.py  観点メタのロード＆サンプリング
  rag_source.py        原本PDFのページテキスト供給（2-upレイアウト対応）
  rag_generator.py     観点→プロンプト（形式分岐）→LLM生成→JSON検証→リトライ
  rag_session_store.py RAGセッション問題プールのライフサイクル・採点ヘルパ
  perspectives/        観点メタ22ファイル（初級8・中級7・上級7／計412観点）
  source/              原本PDF/txt（gitignore。実体は手動配置）
frontend/
  index.html           ログイン／新規登録＋難易度の選択（カード押下で単元選択へ直行）
  mypage.html          マイページ（進捗・履歴・パスワード変更）
  units.html           単元選択（単元別の通算満点進捗を表示）
  quiz.html            受験画面（形式に応じ選択肢/入力欄を切替）
  result.html          結果＋RAG生成メトリクス＋進捗
  admin-Kp7vQm2xRt.html 管理画面（ファイル名＝ADMIN_TOKEN）
  assets/              style.css, quiz.js, admin.js, common.js
docs/rag/              実装指示書（IMPLEMENTATION_SPEC.md 等）
TODO.md                将来対応メモ（認証のメール＋パスワード化 等）
```

## ローカル起動

Python 3.12 を使うこと（3.13/3.14 では依存ビルド不可）。

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows（macOS/Linux: source .venv/bin/activate）
pip install -r backend/requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...   # RAG生成に必須

uvicorn backend.main:app --reload --port 8000
```

- 受験画面: `http://localhost:8000/`
- 管理画面: `http://localhost:8000/admin-Kp7vQm2xRt.html`（ファイル名が ADMIN_TOKEN と一致している必要がある）

`ANTHROPIC_API_KEY` 未設定だと出題開始時に 503 を返す。

### 配線確認（スモークテスト）

APIキー不要のモックLLMで、出題〜判定〜採点〜進捗〜管理の全経路を検証できる。

```bash
.venv/Scripts/python _smoke_backend.py   # 「全通過」を確認
```

（`backend/source/` 未配置だと `grounding=pdf` 系のアサーションのみ落ちる。これは環境要因。）

### 認証（メール＋パスワード）

受験者はメールアドレス＋パスワードで自由登録し、ログインして受験する（HttpOnly Cookie セッション・30日）。
受験データ（attempts / unit_progress）は user_id で本人に紐づく。マイページ（/mypage.html）で
自分の進捗・履歴の確認とパスワード変更ができる。パスワードを忘れた場合は管理画面から
管理者が再設定する（メール送信基盤は持たない）。
旧テストモード（氏名「テストモード」/ ?test=1）は撤去済み。

### 原本PDFの配置

著作権の都合で原本PDFはリポジトリに含めない（`.gitignore` 済み）。
`backend/source/visa_guide_v22_1.pdf` に手動配置する。原本は 2-up レイアウト
（1物理ページに論理2ページ）なので、観点メタの `source_pages`（論理ページ）→
物理ページ = `論理ページ // 2` で対応付けている。

PDF未配置でも、観点メタの `summary`（原本に基づく事実要約）を根拠に生成は動作する
（`grounding=summary`）。結果画面のメトリクスで根拠（PDF/要約）を確認できる。

## 主な設定（環境変数）

| 変数 | 既定 | 用途 |
|---|---|---|
| `ANTHROPIC_API_KEY` | （空） | RAG生成に必須。未設定だと503 |
| `RAG_MODEL` | claude-haiku-4-5-20251001 | 生成モデル |
| `RAG_CHOICES` | 3 | 中級の選択肢数（3 or 4） |
| `RAG_QUESTIONS_PER_QUIZ` | 10 | 1回の出題数 |
| `RAG_HEAD_COUNT` | 3 | 開始時に先出しするヘッド問題数（残りは裏生成） |
| `RAG_SESSION_TTL_SEC` | 7200 | セッション保持秒（既定2時間） |
| `VISA_TYPE_UNITS` | b_visa,e_visa,f_visa,h1b_visa,j_visa,l_visa | 出題対象の単元（カンマ区切り） |
| `ADMIN_TOKEN` | Kp7vQm2xRt | 管理画面トークン（`admin-<token>.html` と一致必須） |
| `DEMO_SEED_ENABLED` | true | デモデータ生成ボタン／APIの有効化（DEV ONLY・撤去予定。false で無効化） |
| `DATABASE_URL` | （空） | PostgreSQL接続URL。**設定時はPostgreSQL**（本番想定・Render Postgres等）、未設定時はSQLite |
| `DATABASE_PATH` | backend/quiz.db | SQLite時のDBパス（DATABASE_URL 未設定時のみ有効） |

出題形式のレベル対応（`config.QUESTION_FORMAT_BY_LEVEL`）：初級=yesno / 中級=choice / 上級=fill_in。

## API

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/api/rag/cells` | 出題対象（ビザ種別）のセル＋原本利用可否 |
| GET | `/api/rag/units?level=&user=` | 単元一覧＋進捗（通算満点・クリア状況） |
| POST | `/api/rag/quiz/start` | RAG出題・ヘッド生成（`test=true` でテストモード） |
| POST | `/api/rag/quiz/continue` | テイル（残り問題）を生成・追記 |
| POST | `/api/quiz/check` | 1問即時判定（session_id でセッション照合） |
| POST | `/api/quiz/submit` | 採点・保存・進捗更新（session_id 必須） |
| GET | `/api/history?username=` | 個人履歴 |
| GET | `/api/{TOKEN}/admin/users` | 受験者一覧（名前＋単元別進捗・クリア数降順） |
| GET | `/api/{TOKEN}/admin/history?username=` | 個別履歴（得点は返さず正答率のみ） |
| GET/POST | `/api/dev/seed-demo` | デモデータ生成（DEV ONLY・撤去予定。デモは実アカウント demo01〜10@example.local / demo-pass-123） |
| POST | `/api/auth/register` | 自由登録（メール＋パスワード＋表示名）。成功時ログイン |
| POST | `/api/auth/login` / `/api/auth/logout` | ログイン／ログアウト |
| GET | `/api/auth/me` | ログイン中ユーザー情報（未ログインは401） |
| POST | `/api/auth/password` | 自分のパスワード変更（全セッション失効） |
| POST | `/api/{TOKEN}/admin/users/{user_id}/password` | 管理者によるパスワード再設定 |

## 管理画面

受験者ごとの単元クリア状況の把握に絞った画面。

- **受験者一覧**：受験者名＋単元別進捗（クリア済み／通算 N/3）。クリア済み単元の総数で降順、同数は名前昇順。
- **個別履歴**：受験者名クリックで表示。得点は出さず、正答率を色分け（満点=緑 / 61〜99%=黄 / 60%以下=赤）。
- サマリー・受験回数/最高点/平均点・全件履歴一覧は廃止（集計処理ごと削除）。

## 設計メモ

- **RAGの正答・解説はフロントへ返さない。** 出題時はセッションに伏せて保持し、
  `check`・`submit` がサーバ側で照合する。
- **問題IDは `sess_<uuid>#<n>`。** 採点はセッションの正答辞書を引く。
- **ヘッド/テイル分割**：開始時はヘッド（既定3問）だけ生成して即返し、残りは受験中に
  裏生成して同セッションへ追記する。prompt caching により原本入力はテイル側で実質再利用される。
- `attempts` / `unit_progress` に `source` 列があり、本リポジトリでは常に `'rag'`。
- 単元クリアは累計方式（`unit_progress.perfect_count`、通算3回満点）。
- ハルシネーション対策の2パス検証（`RAG_VERIFY_PASS`）は枠のみ。既定 off。

## 注意

- 生成問題は本番運用前に専門家レビューを推奨。
- 認証はURL難読化のみ。社外公開時はBasic認証・IP制限・SSO等を追加すること（`TODO.md` 参照）。
- 原本PDF・APIキー・DBはコミットしない（`.gitignore` 済み）。
- 本番データは独立サービスの **Render Postgres** に保存する（`render.yaml` の `databases:` 定義）。
  Webサービスの再デプロイ・再起動でデータは消えない。ローカル・スモークテストは従来どおりSQLiteで動く。
