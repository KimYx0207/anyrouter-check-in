@echo off
chcp 65001 >nul
REM AnyRouter 自动签到 - Windows定时任务启动脚本
REM 此脚本用于Windows任务计划程序调用

REM 切换到项目根目录（脚本所在目录的上级目录）
cd /d "%~dp0.."

REM 设置日志文件路径
set "LOG_FILE=%~dp0..\task_run.log"

REM 开始记录日志（如果不是手动运行模式，则重定向所有输出到日志文件）
if NOT "%1"=="manual" (
    call :LOG_MODE
    exit /b %errorlevel%
)

:NORMAL_MODE
echo ========================================
echo AnyRouter 自动签到脚本启动
echo 时间: %date% %time%
echo ========================================

REM 检查uv是否安装
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到uv命令，请先安装uv！
    echo 安装方法: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
    pause
    exit /b 1
)

REM 运行签到脚本
echo.
echo [信息] 正在运行签到脚本...
uv run checkin.py
set RUN_RESULT=%errorlevel%

REM 记录运行状态
if %RUN_RESULT% equ 0 (
    echo.
    echo [成功] 签到脚本执行完成
) else (
    echo.
    echo [失败] 签到脚本执行失败，错误码: %RUN_RESULT%
)

echo ========================================
echo 执行结束: %date% %time%
echo ========================================

REM 如果手动运行，暂停等待查看结果
if "%1"=="manual" pause
exit /b %RUN_RESULT%

:LOG_MODE
REM 所有输出重定向到日志文件
(
    echo ========================================
    echo AnyRouter 自动签到脚本启动
    echo 时间: %date% %time%
    echo 工作目录: %CD%
    echo ========================================
    echo.

    REM 检查uv是否安装
    where uv 2>&1
    if %errorlevel% neq 0 (
        echo [错误] 未找到uv命令，请先安装uv！
        echo 安装方法: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
        exit /b 1
    )
    echo [信息] uv命令检查通过
    echo.

    REM 运行签到脚本
    echo [信息] 正在运行签到脚本...
    uv run checkin.py 2>&1
    set RUN_RESULT=%errorlevel%

    echo.
    if %RUN_RESULT% equ 0 (
        echo [成功] 签到脚本执行完成
    ) else (
        echo [失败] 签到脚本执行失败，错误码: %RUN_RESULT%
    )

    echo ========================================
    echo 执行结束: %date% %time%
    echo ========================================
) > "%LOG_FILE%" 2>&1
exit /b %RUN_RESULT%
