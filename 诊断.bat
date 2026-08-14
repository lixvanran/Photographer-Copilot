@echo off
REM ============================================================================
REM   Photographer Copilot - Diagnostic
REM   Generates diagnose.txt for troubleshooting
REM ============================================================================

cd /d "%~dp0"

echo.
echo ============================================
echo   Running Diagnostic
echo ============================================
echo.

python "%~dp0diagnose.py"

echo.
echo Report saved to diagnose.txt
echo.
pause

exit /b %errorlevel%
