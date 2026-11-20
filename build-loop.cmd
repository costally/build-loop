@echo off
setlocal
set BUILD_LOOP_EXPERIMENT_ENABLED=1
python "%~dp0launch.py" %*
exit /b %errorlevel%
