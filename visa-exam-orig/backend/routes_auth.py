"""認証エンドポイント。

  POST /api/auth/register  自由登録（メール＋パスワード＋表示名）。成功時そのままログイン。
  POST /api/auth/login     ログイン（HttpOnly Cookie にセッション発行）
  POST /api/auth/logout    ログアウト（セッション破棄）
  GET  /api/auth/me        ログイン中ユーザー情報（未ログインは401）
  POST /api/auth/password  自分のパスワード変更（現在のパスワード必須）

パスワードを忘れた場合の再設定は管理者が行う（routes_admin 側。メール送信基盤は持たない）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend import auth, db

router = APIRouter()

_PASSWORD_MIN = 8


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=_PASSWORD_MIN, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=50)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=_PASSWORD_MIN, max_length=128)


def _validate_email(email: str) -> str:
    e = email.strip().lower()
    # 簡易検証（@ と . の存在・空白なし）。厳密なRFC検証はしない。
    if " " in e or "@" not in e or "." not in e.split("@")[-1]:
        raise HTTPException(400, "メールアドレスの形式が正しくありません。")
    return e


@router.post("/api/auth/register")
def register(req: RegisterRequest, response: Response):
    email = _validate_email(req.email)
    display_name = req.display_name.strip()
    if not display_name:
        raise HTTPException(400, "表示名が必要です。")
    user_id = db.create_user(email, auth.hash_password(req.password), display_name)
    if user_id is None:
        raise HTTPException(409, "このメールアドレスは既に登録されています。")
    auth.issue_session(response, user_id)
    return {"id": user_id, "email": email, "display_name": display_name}


@router.post("/api/auth/login")
def login(req: LoginRequest, response: Response):
    email = _validate_email(req.email)
    user = db.get_user_by_email(email)
    # 不在とパスワード不一致でメッセージを変えない（アカウント有無の探りを防ぐ）
    if user is None or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "メールアドレスまたはパスワードが正しくありません。")
    auth.issue_session(response, user["id"])
    return {"id": user["id"], "email": user["email"], "display_name": user["display_name"]}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.destroy_session(request, response)
    return {"ok": True}


@router.get("/api/auth/me")
def me(user: dict = Depends(auth.get_current_user)):
    return {"id": user["id"], "email": user["email"], "display_name": user["display_name"]}


class EmailChangeRequest(BaseModel):
    new_email: str = Field(..., min_length=3, max_length=254)
    current_password: str = Field(..., min_length=1, max_length=128)


@router.post("/api/auth/email")
def change_email(req: EmailChangeRequest, user: dict = Depends(auth.get_current_user)):
    """メールアドレスを変更する（現在のパスワード必須）。

    進捗・履歴は username（＝メール）でも一意管理しているため、変更時に該当行も
    付け替える（db.change_user_email 内）。セッションは user_id 基準のため維持される。
    """
    new_email = _validate_email(req.new_email)
    full = db.get_user_by_email(user["email"])
    if full is None or not auth.verify_password(req.current_password, full["password_hash"]):
        raise HTTPException(401, "現在のパスワードが正しくありません。")
    res = db.change_user_email(user["id"], new_email)
    if not res["ok"]:
        if res.get("error") == "duplicate":
            raise HTTPException(409, "このメールアドレスは既に使われています。")
        raise HTTPException(404, "アカウントが見つかりません。")
    return {"ok": True, "email": new_email}


@router.post("/api/auth/password")
def change_password(req: PasswordChangeRequest, user: dict = Depends(auth.get_current_user)):
    full = db.get_user_by_email(user["email"])
    if full is None or not auth.verify_password(req.current_password, full["password_hash"]):
        raise HTTPException(401, "現在のパスワードが正しくありません。")
    db.update_user_password(user["id"], auth.hash_password(req.new_password))
    # update_user_password が全セッションを失効させるため、再ログインが必要になる
    return {"ok": True, "relogin_required": True}
