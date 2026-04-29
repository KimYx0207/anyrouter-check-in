#!/usr/bin/env python3
"""通知格式测试"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import utils.result as result_module
from utils.result import SigninRecord, SigninResult, SigninStatus, format_notification_line


def test_success_without_balance_still_shows_success_time(monkeypatch):
	monkeypatch.setattr(result_module, 'get_today_total_gain', lambda account_key: 0.0)
	monkeypatch.setattr(result_module, 'get_current_cycle_first_signin_time', lambda account_key: None)

	signin_time = datetime(2026, 4, 29, 12, 0, 59)
	result = SigninResult(
		account_key='anyrouter_79429',
		account_name='AnyRouter主账号',
		status=SigninStatus.SUCCESS,
		new_record=SigninRecord(time=signin_time),
	)

	line = format_notification_line(result)

	assert line == '[成功] AnyRouter主账号 | 签到成功: 2026-04-29 12:00:59 | 余额: $未知'


def test_cooldown_uses_detailed_line(monkeypatch):
	monkeypatch.setattr(result_module, 'get_today_total_gain', lambda account_key: 0.0)
	monkeypatch.setattr(result_module, 'get_current_cycle_first_signin_time', lambda account_key: None)

	signin_time = datetime.now() - timedelta(minutes=5)
	result = SigninResult(
		account_key='anyrouter_15543',
		account_name='AnyRouter备用账号',
		status=SigninStatus.COOLDOWN,
		balance_before=1641.2,
		balance_after=1641.2,
		new_record=SigninRecord(time=signin_time, balance=1641.2),
	)

	line = format_notification_line(result)

	assert line.startswith('[冷却中] AnyRouter备用账号 | 上次签到: ')
	assert '剩余: ' in line
	assert '余额: $1641.2' in line


def test_stale_gain_time_is_not_included(monkeypatch):
	monkeypatch.setattr(result_module, 'get_today_total_gain', lambda account_key: 0.0)
	monkeypatch.setattr(result_module, 'get_current_cycle_first_signin_time', lambda account_key: None)

	signin_time = datetime(2026, 4, 29, 12, 1, 14)
	result = SigninResult(
		account_key='anyrouter_15543',
		account_name='AnyRouter备用账号',
		status=SigninStatus.SKIPPED,
		balance_before=1641.2,
		last_signin=signin_time,
	)

	line = format_notification_line(result)

	assert '2026/04/18' not in line
	assert '签到成功时间' not in line
