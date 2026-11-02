@echo off
REM 啟動終端機版（TUI）。用法：run_rg_search_tui.bat [資料夾 ...] [-q 關鍵字] [-g *.log]
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -c "import textual" >nul 2>nul || py -m pip install "textual>=0.80"
    py -m rg_search_gui.tui_main %*
    exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -c "import textual" >nul 2>nul || python -m pip install "textual>=0.80"
    python -m rg_search_gui.tui_main %*
    exit /b %errorlevel%
)

echo [ERROR] 找不到 Python 啟動器。
echo 請先安裝 Python 3.10+，並確認 py 或 python 已加入 PATH。
pause
endlocal
