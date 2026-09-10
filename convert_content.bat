@echo off
setlocal DisableDelayedExpansion
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -X utf8 "%~dp0tools\batch_convert.py" %*
exit /b %errorlevel%

:use_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -X utf8 "%~dp0tools\batch_convert.py" %*
exit /b %errorlevel%

:missing_python
echo Python 3 is required to batch the content directory. Install Python and try again. 1>&2
exit /b 1
