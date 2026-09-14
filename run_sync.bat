@echo off
REM Wrapper so Task Scheduler has one stable target to point at.
REM Assumes a venv folder sits next to this file -- see README setup steps.
cd /d "%~dp0"
if not exist logs mkdir logs

REM Only stderr goes to this file (not stdout) -- routine output already
REM goes to logs\trackstreet_sync.log via Python's own logging. This file
REM exists only to catch crashes so early that Python's logging never
REM starts (a broken venv, a missing dependency, etc). Safe to delete
REM anytime; it isn't rotated, but should stay tiny in normal operation.
echo ---- %date% %time% ---- >>logs\bat_output.log
call venv\Scripts\activate.bat 2>>logs\bat_output.log
python trackstreet_sync.py 2>>logs\bat_output.log
