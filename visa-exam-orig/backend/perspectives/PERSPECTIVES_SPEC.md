# 観点メタJSON 作成指示書

## 目的
ビザ検定アプリのRAG出題（方式A・観点リスト方式）のための「観点メタ」を作る。
原本PDF「米国ビザ申請の手引き Ver.22.1」（Greenfield発行・176ページ）に**書いてあることだけ**を
観点化する。原本にない知識は足さない（幻覚抑制のため）。

## 対象セル（全10）
初級8単元 + 中級basics + 上級basics。
- beginner: basics / b_visa / e_visa / l_visa / h1b_visa / f_visa / j_visa / green_card
- intermediate: basics のみ
- advanced: basics のみ
他単元の中・上級は将来拡張。今は作らない。

## 進捗
- [x] beginner_basics.json （20観点 bb01〜bb20 完成）
- [x] beginner_b_visa.json
- [x] beginner_e_visa.json
- [x] beginner_l_visa.json
- [x] beginner_h1b_visa.json
- [x] beginner_f_visa.json
- [x] beginner_j_visa.json
- [x] beginner_green_card.json
- [x] intermediate_basics.json
- [x] advanced_basics.json

## ファイル名規則
`{level}_{unit_id}.json`（例: beginner_b_visa.json）
作業ディレクトリ: /home/claude/perspectives/

## ID命名規則
単元略号2文字 + 連番2桁。
- bb=beginner basics / bv=beginner b_visa / be=beginner e_visa / bl=beginner l_visa
- bh=beginner h1b_visa / bf=beginner f_visa / bj=beginner j_visa / bg=beginner green_card
- ib=intermediate basics / ab=advanced basics
例: bv01, be01, ib01

## JSONフォーマット（厳守・beginner_basics.jsonと同一）
```json
{
  "level": "beginner",
  "unit_id": "b_visa",
  "unit_name": "Bビザ・商用",
  "level_description": "（その難度で何を問うかの説明。下記の難度定義を参照）",
  "perspectives": [
    {
      "id": "bv01",
      "name": "観点の見出し（簡潔に）",
      "summary": "原本に基づく事実の要約。1〜2文。",
      "source_pages": [21]
    }
  ]
}
```
- `summary` は「事実の要約」スタイル（問いかけ文ではなく断定文）。
- `source_pages` は原本の該当ページ番号（複数可）。
- フィールドの追加・削除はしない。

## 観点の粒度
1単元あたり **16〜20観点**。10問出題に対し1.5〜2倍の観点を用意し、出題の被りを防ぐ。

## 難度定義（level_descriptionとsummaryの温度感）
- **初級**: 用語の定義、制度の概要、主体の区別。原本を一度読めば把握できる基本事実。条文番号や細かな例外は問わない。
  - 例: 「ビザは入国前に在外公館で発行される入国許可証」レベル。
- **中級**: 概念の分離、複数制度の比較、付与主体の違いなど一段深い理解。
  - 例: 「ビザとステータスの違い（発給主体・概念の分離）」レベル。
- **上級**: 条文番号（INA / FAM / CFR）、発動タイミング、例外の例外など実務の踏み込み。
  - 例: 「INA212条(a)(9)(B)の3年・10年バーの発動タイミング」レベル。

## 各単元の原本ページ対応（目次より）
- b_visa（Bビザ・商用）: II章 pp.21-26（Bビザとは/有効期間/industrial worker/B-1 in lieu of H-1B）
- e_visa（Eビザ・投資貿易）: III章1 pp.27-46（Eビザとは/種類/申請条件/E-1・E-2カンパニー/essential skill/TDY/家族）
- l_visa（Lビザ・企業内転勤）: III章2 pp.46-56（Lビザとは/種類/申請条件/specialized knowledge/Blanket L/家族）
- h1b_visa（H-1B・専門職）: III章3 pp.57-62（H-1Bとは/specialty occupation/申請枠65000/抽選/延長/家族）
- f_visa（Fビザ・就学）: IV章 pp.69-73（Fビザとは/I-20/SEVIS/CPT/OPT/Grace Period/Cap-Gap）
- j_visa（Jビザ・研修交流）: V章 pp.74-82（H-3との違い/Jビザとは/DS-2019/業務研修条件/Two-Year Rule/家族）
- green_card（永住権）: IX章 pp.96-111（移民ビザ/EB-1〜EB-5/PERM/I-140/ステータス変更/家族ベース/DV/市民権）
- intermediate basics / advanced basics: I章 pp.7-20 を中心に、より深い論点で。
  - 中級例: ビザとステータスの概念分離、有効期限と滞在期限が逆転する状況、Automatic Revalidationの除外条件。
  - 上級例: INA条文番号、不法滞在バーの発動タイミング、VWP除外国・二重国籍者の扱いなど。

## 作業手順（トークン節約のため1ファイルずつ）
1. 対象単元の原本該当ページを確認（必要なら context の原本テキストを参照）。
2. 観点を16〜20個抽出し、上記フォーマットでJSON生成。
3. create_file で /home/claude/perspectives/ に保存。
4. INSTRUCTIONS.md の進捗チェックボックスを更新。
5. 次のファイルへ。

## 文体（Atsushi向け応答時）
翻訳調・常体・ウィット。「ワオ」は使わない。君呼び。簡潔。表は使わない。一度に一問。
