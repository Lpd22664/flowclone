@echo off
REM Build FlowClone.exe.
REM Prereqs: python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt pyinstaller

REM Regenerate the EXE icon from the current tray glyph.
python scripts\generate_icon.py
if errorlevel 1 goto :fail

pyinstaller ^
    --onefile ^
    --noconsole ^
    --name FlowClone ^
    --icon assets\flowclone.ico ^
    --hidden-import pystray._win32 ^
    --collect-submodules pystray ^
    --collect-submodules sounddevice ^
    main.py
if errorlevel 1 goto :fail

echo.
echo Build complete. Executable at dist\FlowClone.exe
exit /b 0

:fail
echo.
echo Build FAILED.
exit /b 1
