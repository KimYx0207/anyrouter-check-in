#!/usr/bin/env python3
"""数据库收益周期测试"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.database import Database


def _create_test_account(db: Database) -> int:
	db.init_schema()
	db.upsert_provider('test', 'https://example.com')
	return db.create_account('test', '123', {'session': 'x'}, name='Test')


def test_current_gain_window_ignores_stale_positive_record(tmp_path):
	db = Database(str(tmp_path / 'checkin.db'))
	account_id = _create_test_account(db)
	try:
		stale_time = datetime.now() - timedelta(days=10)
		recent_time = datetime.now() - timedelta(hours=1)
		db.add_signin_record(
			account_id=account_id,
			signin_time=stale_time,
			status='success',
			balance_before=100.0,
			balance_after=125.0,
			balance_diff=25.0,
		)
		db.add_signin_record(
			account_id=account_id,
			signin_time=recent_time,
			status='skipped',
			balance_before=125.0,
			balance_after=125.0,
		)

		assert db.get_today_total_gain(account_id) == 0.0
		assert db.get_current_cycle_first_signin_time(account_id) is None
	finally:
		db.close()


def test_current_gain_window_uses_recent_positive_record(tmp_path):
	db = Database(str(tmp_path / 'checkin.db'))
	account_id = _create_test_account(db)
	try:
		recent_time = datetime.now() - timedelta(hours=1)
		db.add_signin_record(
			account_id=account_id,
			signin_time=recent_time,
			status='success',
			balance_before=100.0,
			balance_after=125.0,
			balance_diff=25.0,
		)

		assert db.get_today_total_gain(account_id) == 25.0
		assert db.get_current_cycle_first_signin_time(account_id) == recent_time
	finally:
		db.close()
