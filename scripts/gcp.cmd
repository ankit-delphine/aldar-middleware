@echo off
REM Quick Git Commit and Push - Creates new branch from protected branches
REM Usage: gcp "your commit message"

if "%~1"=="" (
    echo Usage: gcp "your commit message"
    exit /b 1
)

powershell -ExecutionPolicy Bypass -File "%~dp0git_commit_push.ps1" -Message "%~1"
