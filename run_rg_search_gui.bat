@echo off
REM 使用專案根目錄啟動 Python GUI，避免把核心邏輯寫死在本機路徑。
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -m rg_search_gui
    exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m rg_search_gui
    exit /b %errorlevel%
)

echo [ERROR] 找不到 Python 啟動器。
echo 請先安裝 Python 3.10+，並確認 py 或 python 已加入 PATH。
pause
endlocal
