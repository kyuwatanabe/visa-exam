"""原本（米国ビザ申請の手引き Ver.22.1）のテキスト供給。

観点メタの source_pages（論理ページ番号）を、原本PDFの該当テキストへ変換する。
原本PDFは 2-up レイアウト（1物理ページに論理2ページ）なので、
論理ページ L は物理ページ index = L // SOURCE_PAGES_PER_SHEET に載っている。

原本は著作権の都合でリポジトリにコミットしない（.gitignore 済み）。実体は
backend/source/ に手動配置する。未配置でも固定プール方式は動く（RAGのみ無効化）。
"""
from __future__ import annotations

import threading
from typing import List, Optional

from backend.config import (
    SOURCE_PAGES_PER_SHEET,
    SOURCE_PDF_PATH,
    SOURCE_TXT_PATH,
)

_LOCK = threading.Lock()
_PAGE_TEXTS: Optional[List[str]] = None  # 物理ページ index → テキスト
_LOAD_ERROR: Optional[str] = None


def _load_pdf_pages() -> List[str]:
    """PDFから物理ページごとのテキストを抽出する。"""
    from pypdf import PdfReader

    reader = PdfReader(str(SOURCE_PDF_PATH))
    return [(page.extract_text() or "") for page in reader.pages]


def _ensure_loaded() -> None:
    """原本テキストを一度だけロードする（PDF優先、無ければ抽出済みtxt）。"""
    global _PAGE_TEXTS, _LOAD_ERROR
    if _PAGE_TEXTS is not None or _LOAD_ERROR is not None:
        return
    with _LOCK:
        if _PAGE_TEXTS is not None or _LOAD_ERROR is not None:
            return
        try:
            if SOURCE_PDF_PATH.exists():
                _PAGE_TEXTS = _load_pdf_pages()
            elif SOURCE_TXT_PATH.exists():
                # フォールバック: フォームフィード区切りの抽出済みテキスト
                text = SOURCE_TXT_PATH.read_text(encoding="utf-8")
                _PAGE_TEXTS = text.split("\f")
            else:
                _LOAD_ERROR = (
                    f"原本が見つかりません。{SOURCE_PDF_PATH.name} または "
                    f"{SOURCE_TXT_PATH.name} を backend/source/ に配置してください。"
                )
        except Exception as e:  # 抽出失敗は明示的に記録（無音劣化させない）
            _LOAD_ERROR = f"原本テキストの抽出に失敗しました: {e}"


def is_available() -> bool:
    """原本テキストが利用可能か。"""
    _ensure_loaded()
    return _PAGE_TEXTS is not None


def load_error() -> Optional[str]:
    """ロード失敗時の理由文。利用可能なら None。"""
    _ensure_loaded()
    return _LOAD_ERROR


def _logical_to_physical(logical_page: int) -> int:
    """論理ページ番号 → 物理ページ index（0始まり）。"""
    if SOURCE_PAGES_PER_SHEET <= 1:
        return max(0, logical_page - 1)
    return logical_page // SOURCE_PAGES_PER_SHEET


def text_for_pages(logical_pages: List[int]) -> str:
    """論理ページ番号のリストに対応する原本テキストを連結して返す。

    同一物理ページに載る論理ページは重複排除する。原本未配置なら空文字。
    """
    _ensure_loaded()
    if _PAGE_TEXTS is None:
        return ""
    physical_indices = []
    for lp in logical_pages:
        idx = _logical_to_physical(lp)
        if 0 <= idx < len(_PAGE_TEXTS) and idx not in physical_indices:
            physical_indices.append(idx)
    chunks = []
    for idx in sorted(physical_indices):
        chunks.append(f"［原本 p.{idx * SOURCE_PAGES_PER_SHEET}付近］\n{_PAGE_TEXTS[idx]}")
    return "\n\n".join(chunks)
