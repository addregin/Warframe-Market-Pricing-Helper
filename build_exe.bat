@echo off
REM Builds the installer "installer\Warframe Market Prices Setup.exe" (needs Inno Setup 6 or 7).
REM The program itself is still compiled on the way (the installer packs it), but the loose
REM "Warframe Market Prices.exe" is deleted afterwards, so only the installer is left.
REM Keep this file in the same folder as wfm_app.py, wfm_prices.py, wfm_icon.ico, wfm_version.txt,
REM requirements.txt and installer.iss, then double-click it.

cd /d "%~dp0"

for %%F in (wfm_app.py wfm_prices.py wfm_icon.ico wfm_version.txt requirements.txt) do (
    if not exist "%%F" (
        echo ERROR: %%F was not found in %cd%
        echo Put wfm_app.py, wfm_prices.py, wfm_icon.ico, wfm_version.txt, requirements.txt and this file in the same folder.
        echo Check that the names match exactly, with no "(1)" and no hidden .txt on the end.
        pause
        exit /b 1
    )
)

REM Close the app if it is still running, and remove old build leftovers
REM so the new icon is always applied.
taskkill /f /im "Warframe Market Prices.exe" >nul 2>&1
taskkill /f /im WarframeMarketPrices.exe >nul 2>&1
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist installer\*.exe del /q installer\*.exe >nul 2>&1
if exist "Warframe Market Prices.spec" del /q "Warframe Market Prices.spec"
if exist WarframeMarketPrices.spec del /q WarframeMarketPrices.spec

py -m pip install --upgrade pip
py -m pip install -r requirements.txt
if errorlevel 1 goto fail

REM Optional: browser-like downloader that gets item pictures past warframe.market's block.
REM If it cannot be installed the app still works (it falls back to other methods).
set EXTRA=
py -m pip install curl_cffi
py -c "import curl_cffi" >nul 2>&1
if not errorlevel 1 set EXTRA=--collect-all curl_cffi

py -m PyInstaller --noconfirm --clean --onefile --windowed --name "Warframe Market Prices" --icon "%~dp0wfm_icon.ico" --version-file "%~dp0wfm_version.txt" --add-data "%~dp0wfm_icon.ico;." --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtWebChannel %EXTRA% wfm_app.py
if errorlevel 1 goto fail

REM Optional: build an installer (shows up in Settings > Apps > Installed apps as "Warframe Market Prices").
REM Needs the free Inno Setup (version 6 or 7) from https://jrsoftware.org/isdl.php and installer.iss in this folder.
REM The lines are checked from oldest to newest, so the newest installed version wins.
set ISCC=
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 7\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 7\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
REM Not in a standard folder? Use whatever ISCC.exe is on the PATH.
if not defined ISCC for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%I"
if not exist installer.iss set ISCC=
if defined ISCC echo Building the installer with: %ISCC%
if defined ISCC "%ISCC%" installer.iss
if errorlevel 1 goto fail

REM The installer has been built and contains the program, so the loose exe is not needed any more.
REM (Single-line commands on purpose: a folder name with brackets would break a multi-line block.)
if exist "installer\Warframe Market Prices Setup.exe" if exist dist rmdir /s /q dist
if exist "installer\Warframe Market Prices Setup.exe" if exist build rmdir /s /q build

REM Windows caches icons, so an old icon can keep showing. Refresh the cache.
ie4uinit.exe -show >nul 2>&1
taskkill /f /im explorer.exe >nul 2>&1
if exist "%LocalAppData%\IconCache.db" del /a /q "%LocalAppData%\IconCache.db" >nul 2>&1
del /a /q "%LocalAppData%\Microsoft\Windows\Explorer\iconcache*" >nul 2>&1
start explorer.exe

echo.
if exist "installer\Warframe Market Prices Setup.exe" echo Done. Your installer is here: %cd%\installer\Warframe Market Prices Setup.exe
if not exist "installer\Warframe Market Prices Setup.exe" echo No installer was built - install Inno Setup 6 or 7 from https://jrsoftware.org/isdl.php and run this file again.
if not exist "installer\Warframe Market Prices Setup.exe" echo The program itself is here: %cd%\dist\Warframe Market Prices.exe
pause
exit /b 0

:fail
echo.
echo The build failed. Scroll up to read the error message.
pause
exit /b 1
