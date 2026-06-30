"""認証コア。

- パスワード: PBKDF2-HMAC-SHA256（60万回・ソルト16バイト）。標準ライブラリのみで依存追加なし。
- セッション: ランダムトークン（HttpOnly Cookie）。DBにはトークンのSHA-256のみ保存し、
  DB読取だけではセッションを乗っ取れないようにする。
- get_current_user: Cookie からログイン中ユーザーを解決する FastAPI 依存関数。
  未ログインは 401 を返し、フロントはトップ（ログイン画面）へ誘導する。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Response

from backend import db

SESSION_COOKIE = "session"
SESSION_TTL_SEC = 30 * 24 * 3600  # 30日

_PBKDF2_ITERATIONS = 600_000  # OWASP推奨水準（2023以降）


# ----------------------------------------------------------------------
# パスワードハッシュ
# ----------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ----------------------------------------------------------------------
# セッション
# ----------------------------------------------------------------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(response: Response, user_id: int) -> None:
    """新規セッションを発行し、HttpOnly Cookie をレスポンスへ載せる。

    Cookie には max_age/expires を付けず**セッションCookie**とする。ブラウザ／タブを
    閉じると失効するため、共有端末で次の利用者が前の人のアカウントへ自動ログインして
    しまう取り違えを防ぐ。DB側のセッションTTL（SESSION_TTL_SEC）はサーバ側の上限として残す。
    """
    token = secrets.token_urlsafe(32)
    db.create_auth_session(_token_hash(token), user_id, ttl_sec=SESSION_TTL_SEC)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        path="/",
    )


def destroy_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_auth_session(_token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_optional_user(request: Request) -> Optional[dict]:
    """ログイン中なら user dict（id/email/display_name）、未ログインなら None。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return db.get_user_by_session(_token_hash(token))


def get_current_user(request: Request) -> dict:
    """ログイン必須の依存関数。未ログインは 401。"""
    user = get_optional_user(request)
    if user is None:
        raise HTTPException(401, "ログインが必要です。")
    return user
