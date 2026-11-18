@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~1"
if "%ROOT%"=="" set "ROOT=E:\MP3"
cd /d "%~dp0"

echo.
echo   宁宁静听 · 批量补歌词
echo   目标目录: %ROOT%
echo   已经有 .lrc 的歌曲会自动跳过
echo.
python fetch-lyrics.py "%ROOT%" --loose
echo.
echo   完成。回到播放器重开歌词秀即可看到。
pause
