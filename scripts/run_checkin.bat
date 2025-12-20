@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
REM AnyRouter 自动签到 - Windows定时任务启动脚本
REM 此脚本用于Windows任务计划程序调用

REM 切换到项目根目录（脚本所在目录的上级目录）
cd /d "%~dp0.."

REM 设置日志文件路径（使用绝对路径）
set "PROJECT_ROOT=%~dp0.."
set "LOG_FILE=%PROJECT_ROOT%\task_run.log"

REM 自动检测uv路径
set "UV_PATH="
for %%i in (uv.exe) do set "UV_PATH=%%~$PATH:i"
if not defined UV_PATH (
    REM 尝试常见安装路径
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV_PATH=%USERPROFILE%\.local\bin\uv.exe"
    ) else if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" (
        set "UV_PATH=%LOCALAPPDATA%\Programs\uv\uv.exe"
    )
)

REM 开始记录日志（如果不是手动运行模式，则重定向所有输出到日志文件）
if NOT "%1"=="manual" (
    call :LOG_MODE
    exit /b !errorlevel!
)

:NORMAL_MODE
echo ========================================
echo AnyRouter 自动签到脚本启动
echo 时间: %date% %time%
echo 工作目录: %CD%
echo ========================================

REM 检查uv是否存在
if not defined UV_PATH (
    echo [错误] 未找到uv命令！
    echo 安装方法: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
    pause
    exit /b 1
)
echo [信息] uv路径: %UV_PATH%

REM 检查.env文件
if not exist "%PROJECT_ROOT%\.env" (
    echo [警告] 未找到.env配置文件！
    echo 请确保配置文件存在: %PROJECT_ROOT%\.env
    pause
    exit /b 1
)
echo [信息] .env文件: %PROJECT_ROOT%\.env

REM 运行签到脚本
echo.
echo [信息] Running checkin script...
"%UV_PATH%" run checkin.py
set RUN_RESULT=%errorlevel%

REM 记录运行状态
if !RUN_RESULT! equ 0 (
    echo.
    echo [成功] 签到脚本执行完成
) else (
    echo.
    echo [失败] 签到脚本执行失败，错误码: !RUN_RESULT!
)

echo ========================================
echo 执行结束: %date% %time%
echo ========================================

REM 如果手动运行，暂停等待查看结果
if "%1"=="manual" pause
exit /b !RUN_RESULT!

:LOG_MODE
REM 定时任务模式：Python自己写日志文件，保证UTF-8编码
echo ========================================
echo AnyRouter 自动签到脚本启动 - 定时任务模式
echo 时间: %date% %time%
echo 工作目录: %CD%
echo 项目根目录: %PROJECT_ROOT%
echo ========================================
echo.

REM 检查uv是否存在
if not defined UV_PATH (
    echo [错误] 未找到uv命令！
    echo [提示] 请检查uv是否正确安装
    exit /b 1
)
echo [信息] uv路径: %UV_PATH%

REM 检查.env文件
if not exist "%PROJECT_ROOT%\.env" (
    echo [警告] 未找到.env配置文件！
    exit /b 1
)
echo [信息] .env文件: %PROJECT_ROOT%\.env
echo.

REM 运行签到脚本（Python会自己写UTF-8日志）
echo [信息] Running checkin script...
"%UV_PATH%" run checkin.py

exit /b %errorlevel%
