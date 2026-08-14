@echo off
REM ============================================================================
REM   Photographer Copilot - One-Click Launcher
REM   Pure ASCII, no BOM, no chcp (causes flash close in Win11 Chinese)
REM ============================================================================

cd /d "%~dp0"

echo.
echo ============================================
echo   Photographer Copilot
echo ============================================
echo.
echo Working dir: %CD%
echo.

python "%~dp0start.py" %*

if errorlevel 1 (
    echo.
    echo *** start.py exited with code %errorlevel% ***
    pause
)

exit /b %errorlevel%
