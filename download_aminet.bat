@echo off
setlocal DisableDelayedExpansion
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -X utf8 "%~dp0tools\download_aminet.py" --recover-truncated-manifest %*
set "AMINET_EXIT_CODE=%errorlevel%"
pause
exit /b %AMINET_EXIT_CODE%

:use_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -X utf8 "%~dp0tools\download_aminet.py" --recover-truncated-manifest %*
set "AMINET_EXIT_CODE=%errorlevel%"
pause
exit /b %AMINET_EXIT_CODE%

:missing_python
echo Python 3 is required. Install Python and try again. 1>&2
pause
exit /b 1
