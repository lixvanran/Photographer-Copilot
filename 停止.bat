@echo off
REM ============================================================================
REM   Photographer Copilot - Stop
REM ============================================================================

cd /d "%~dp0"

echo.
echo ============================================
echo   Stopping Photographer Copilot
echo ============================================
echo.

python "%~dp0stop.py"

pause

exit /b %errorlevel%
