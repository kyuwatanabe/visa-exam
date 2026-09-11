@echo off
setlocal EnableDelayedExpansion
rem ============================================================
rem  push_to_github.bat
rem  git pull -> git add -A -> git commit -> git push を順に実行
rem  このファイルをダブルクリックすると GitHub にアップロードされます
rem  ※ .git が無いフォルダでは、初回だけ GitHub との接続設定を行います
rem ============================================================

cd /d "%~dp0"
for %%I in ("%CD%") do set "REPO=%%~nxI"
set "REMOTE_URL=https://github.com/kyuwatanabe/!REPO!.git"

echo ============================================================
echo  GitHub へアップロードします
echo  フォルダ: %CD%
echo  接続先  : !REMOTE_URL!
echo ============================================================
echo.

where git >nul 2>&1
if errorlevel 1 goto NOGITCMD

if not exist ".git" goto SETUP
rem .git はあるが初回設定が途中で止まっている場合は、設定をやり直す
git rev-parse -q --verify HEAD >nul 2>&1
if errorlevel 1 goto SETUP
goto MAIN

rem ============================================================
rem  初回設定: このフォルダを GitHub のリポジトリと接続する
rem ============================================================
:SETUP
echo このフォルダはまだ GitHub と接続されていません。
echo 初回設定として、!REMOTE_URL! と接続します。
echo フォルダ内のファイルは消えたり書き換わったりしません。
echo.
set "ANS="
set /p "ANS=接続設定を行いますか？ [Y/N] : "
if /i not "!ANS!"=="Y" goto CANCEL

echo.
echo [初回設定 1/3] Git の管理を開始しています...
git init -q
if errorlevel 1 goto FAIL
git remote remove origin >nul 2>&1
git remote add origin "!REMOTE_URL!"
if errorlevel 1 goto FAIL

echo [初回設定 2/3] GitHub から履歴を取得しています...
echo  ※ GitHub のサインイン画面が出たらログインしてください
git fetch origin
if errorlevel 1 goto SETUPFAIL

set "BR="
for /f "tokens=2" %%r in ('git ls-remote --symref origin HEAD ^| findstr /b "ref:"') do set "BR=%%r"
if defined BR set "BR=!BR:refs/heads/=!"
if not defined BR set "BR=main"

echo [初回設定 3/3] ブランチ !BR! に合わせています...
git symbolic-ref HEAD refs/heads/!BR!
git reset -q origin/!BR!
if errorlevel 1 goto SETUPFAIL
git branch -q --set-upstream-to=origin/!BR! !BR!

rem コミット用の名前が未設定なら、このフォルダ専用に設定する
git config user.email >nul 2>&1
if errorlevel 1 git config user.email "kyu.watanabe@green-f.biz"
git config user.name >nul 2>&1
if errorlevel 1 git config user.name "Kyu Watanabe"

echo.
echo 初回設定が完了しました。続けて GitHub との差分を確認します。
echo.

rem ============================================================
rem  通常の処理
rem ============================================================
:MAIN
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set "BRANCH=%%b"
echo  ブランチ: !BRANCH!
echo.

rem ---- 1. git pull ----
echo [1/4] GitHub から最新を取得しています... [git pull]
git pull --no-rebase --no-edit
if errorlevel 1 goto PULLFAIL
echo.

rem ---- 2. git add ----
echo [2/4] 変更をまとめています... [git add -A]
git add -A
if errorlevel 1 goto FAIL

git diff --cached --quiet
if not errorlevel 1 goto NOCHANGE

echo.
echo  --- アップロードされる変更 ---
echo   M=変更  A=追加  D=削除
git status --short
echo  ------------------------------
echo.
set "ANS="
set /p "ANS=この内容でアップロードしますか？ [Y/N] : "
if /i not "!ANS!"=="Y" goto UNSTAGE

rem ---- 3. git commit ----
set "MSG="
set /p "MSG=コミットメッセージを入力して Enter [空欄なら日時] : "
if not defined MSG set "MSG=update %DATE% %TIME:~0,5%"
set "MSG=!MSG:"=!"

echo.
echo [3/4] 記録しています... [git commit]
git commit -q -m "!MSG!"
if errorlevel 1 goto FAIL
echo.
goto PUSH

:NOCHANGE
echo.
echo [3/4] 新しい変更はありません。コミットは省略します。
echo.

:PUSH
rem ---- 4. git push ----
echo [4/4] GitHub へアップロードしています... [git push]
git push
if errorlevel 1 goto FAIL
echo.
echo ============================================================
echo  完了しました。GitHub へのアップロードが終わりました。
echo ============================================================
echo.
pause
exit /b 0

:UNSTAGE
git reset -q
echo.
echo 中止しました。何もアップロードしていません。
echo.
pause
exit /b 1

:CANCEL
echo.
echo 中止しました。
echo.
pause
exit /b 1

:NOGITCMD
echo [エラー] git コマンドが見つかりません。Git for Windows をインストールしてください。
echo.
pause
exit /b 1

:SETUPFAIL
echo.
echo [エラー] GitHub との接続設定に失敗しました。
echo  「Could not resolve host」と出た場合は、インターネットに接続できていません。
echo  ブラウザで github.com が開けるか確認してから、もう一度実行してください。
echo  それ以外の場合は、サインインできなかったか、リポジトリ名 !REPO! が違う可能性があります。
echo  もう一度実行すれば、初回設定を最初からやり直します。
echo  上のメッセージを Claude に貼り付けて相談してください。
echo.
pause
exit /b 1

:PULLFAIL
echo.
echo [エラー] git pull に失敗しました。
echo  GitHub 側とローカルで同じファイルが別々に変更されている可能性があります。
echo  処理を中止しました。上のメッセージを Claude に貼り付けて相談してください。
echo.
pause
exit /b 1

:FAIL
echo.
echo [エラー] 途中で失敗しました。上のメッセージを確認してください。
echo  認証エラーの場合は、GitHub へのサインイン画面が出たらログインしてください。
echo.
pause
exit /b 1
