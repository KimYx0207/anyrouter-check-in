#!/usr/bin/env python3
"""数据库管理模块

使用 SQLite 存储账号配置、Provider 配置和签到历史记录。
支持从环境变量和 JSON 文件迁移数据。
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

from utils.constants import DATA_DIR, DATABASE_FILE

# 数据库 Schema 版本
SCHEMA_VERSION = 1

# 建表 SQL
SCHEMA_SQL = """
-- Provider 配置表
CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    login_path TEXT DEFAULT '/login',
    sign_in_path TEXT,
    user_info_path TEXT DEFAULT '/api/user/self',
    api_user_key TEXT DEFAULT 'new-api-user',
    bypass_method TEXT,
    waf_cookie_names TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 账号表
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    provider_id INTEGER NOT NULL,
    api_user TEXT NOT NULL,
    cookies TEXT NOT NULL,
    username TEXT,
    password TEXT,
    oauth_provider TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (provider_id) REFERENCES providers(id),
    UNIQUE (provider_id, api_user)
);

-- 签到记录表
CREATE TABLE IF NOT EXISTS signin_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    signin_time TIMESTAMP NOT NULL,
    balance_before REAL,
    balance_after REAL,
    balance_diff REAL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

-- 元数据表（存储 schema 版本等）
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_signin_records_account ON signin_records(account_id);
CREATE INDEX IF NOT EXISTS idx_signin_records_time ON signin_records(signin_time DESC);
CREATE INDEX IF NOT EXISTS idx_accounts_provider ON accounts(provider_id);
CREATE INDEX IF NOT EXISTS idx_accounts_active ON accounts(is_active);
"""


@dataclass
class ProviderRow:
	"""Provider 数据库行"""

	id: int
	name: str
	domain: str
	login_path: str
	sign_in_path: str | None
	user_info_path: str
	api_user_key: str
	bypass_method: str | None
	waf_cookie_names: List[str] | None


@dataclass
class AccountRow:
	"""Account 数据库行"""

	id: int
	name: str | None
	provider_id: int
	provider_name: str  # JOIN 得到
	api_user: str
	cookies: dict
	username: str | None
	password: str | None
	oauth_provider: str | None
	is_active: bool


@dataclass
class SigninRecordRow:
	"""SigninRecord 数据库行"""

	id: int
	account_id: int
	signin_time: datetime
	balance_before: float | None
	balance_after: float | None
	balance_diff: float | None
	status: str
	error_message: str | None


class Database:
	"""SQLite 数据库管理类"""

	def __init__(self, db_path: str = DATABASE_FILE):
		self.db_path = db_path
		self._conn: sqlite3.Connection | None = None

	def _ensure_data_dir(self) -> None:
		"""确保 data 目录存在"""
		Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

	def connect(self) -> sqlite3.Connection:
		"""获取数据库连接"""
		if self._conn is None:
			self._ensure_data_dir()
			self._conn = sqlite3.connect(self.db_path)
			self._conn.row_factory = sqlite3.Row
			# 启用外键约束
			self._conn.execute('PRAGMA foreign_keys = ON')
		return self._conn

	def close(self) -> None:
		"""关闭数据库连接"""
		if self._conn:
			self._conn.close()
			self._conn = None

	def init_schema(self) -> None:
		"""初始化数据库 schema"""
		conn = self.connect()
		conn.executescript(SCHEMA_SQL)
		# 记录 schema 版本
		conn.execute(
			'INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)',
			('schema_version', str(SCHEMA_VERSION))
		)
		conn.commit()

	def get_schema_version(self) -> int | None:
		"""获取当前 schema 版本"""
		conn = self.connect()
		try:
			cursor = conn.execute(
				'SELECT value FROM metadata WHERE key = ?',
				('schema_version',)
			)
			row = cursor.fetchone()
			return int(row['value']) if row else None
		except sqlite3.OperationalError:
			return None

	# ============ Provider CRUD ============

	def get_all_providers(self) -> List[ProviderRow]:
		"""获取所有 Provider"""
		conn = self.connect()
		cursor = conn.execute('SELECT * FROM providers ORDER BY name')
		return [self._row_to_provider(row) for row in cursor.fetchall()]

	def get_provider_by_name(self, name: str) -> ProviderRow | None:
		"""按名称获取 Provider"""
		conn = self.connect()
		cursor = conn.execute('SELECT * FROM providers WHERE name = ?', (name,))
		row = cursor.fetchone()
		return self._row_to_provider(row) if row else None

	def get_provider_by_id(self, provider_id: int) -> ProviderRow | None:
		"""按 ID 获取 Provider"""
		conn = self.connect()
		cursor = conn.execute('SELECT * FROM providers WHERE id = ?', (provider_id,))
		row = cursor.fetchone()
		return self._row_to_provider(row) if row else None

	def upsert_provider(
		self,
		name: str,
		domain: str,
		login_path: str = '/login',
		sign_in_path: str | None = '/api/user/sign_in',
		user_info_path: str = '/api/user/self',
		api_user_key: str = 'new-api-user',
		bypass_method: str | None = None,
		waf_cookie_names: List[str] | None = None
	) -> int:
		"""创建或更新 Provider"""
		conn = self.connect()
		waf_names_json = json.dumps(waf_cookie_names) if waf_cookie_names else None

		cursor = conn.execute('''
			INSERT INTO providers (name, domain, login_path, sign_in_path, user_info_path,
			                       api_user_key, bypass_method, waf_cookie_names)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?)
			ON CONFLICT(name) DO UPDATE SET
				domain = excluded.domain,
				login_path = excluded.login_path,
				sign_in_path = excluded.sign_in_path,
				user_info_path = excluded.user_info_path,
				api_user_key = excluded.api_user_key,
				bypass_method = excluded.bypass_method,
				waf_cookie_names = excluded.waf_cookie_names,
				updated_at = CURRENT_TIMESTAMP
		''', (name, domain, login_path, sign_in_path, user_info_path,
		      api_user_key, bypass_method, waf_names_json))
		conn.commit()
		return cursor.lastrowid or self.get_provider_by_name(name).id

	def _row_to_provider(self, row: sqlite3.Row) -> ProviderRow:
		"""将数据库行转换为 ProviderRow"""
		waf_names = None
		if row['waf_cookie_names']:
			try:
				waf_names = json.loads(row['waf_cookie_names'])
			except json.JSONDecodeError:
				waf_names = None

		return ProviderRow(
			id=row['id'],
			name=row['name'],
			domain=row['domain'],
			login_path=row['login_path'],
			sign_in_path=row['sign_in_path'],
			user_info_path=row['user_info_path'],
			api_user_key=row['api_user_key'],
			bypass_method=row['bypass_method'],
			waf_cookie_names=waf_names
		)

	# ============ Account CRUD ============

	def get_all_accounts(self, active_only: bool = True) -> List[AccountRow]:
		"""获取所有账号"""
		conn = self.connect()
		sql = '''
			SELECT a.*, p.name as provider_name
			FROM accounts a
			JOIN providers p ON a.provider_id = p.id
		'''
		if active_only:
			sql += ' WHERE a.is_active = 1'
		sql += ' ORDER BY a.id'

		cursor = conn.execute(sql)
		return [self._row_to_account(row) for row in cursor.fetchall()]

	def get_account_by_id(self, account_id: int) -> AccountRow | None:
		"""按 ID 获取账号"""
		conn = self.connect()
		cursor = conn.execute('''
			SELECT a.*, p.name as provider_name
			FROM accounts a
			JOIN providers p ON a.provider_id = p.id
			WHERE a.id = ?
		''', (account_id,))
		row = cursor.fetchone()
		return self._row_to_account(row) if row else None

	def get_account_by_key(self, provider_name: str, api_user: str) -> AccountRow | None:
		"""按 provider + api_user 获取账号"""
		conn = self.connect()
		cursor = conn.execute('''
			SELECT a.*, p.name as provider_name
			FROM accounts a
			JOIN providers p ON a.provider_id = p.id
			WHERE p.name = ? AND a.api_user = ?
		''', (provider_name, api_user))
		row = cursor.fetchone()
		return self._row_to_account(row) if row else None

	def create_account(
		self,
		provider_name: str,
		api_user: str,
		cookies: dict | str,
		name: str | None = None,
		username: str | None = None,
		password: str | None = None,
		oauth_provider: str | None = None
	) -> int:
		"""创建账号"""
		# 获取 provider ID
		provider = self.get_provider_by_name(provider_name)
		if not provider:
			raise ValueError(f'Provider not found: {provider_name}')

		conn = self.connect()
		cookies_json = json.dumps(cookies) if isinstance(cookies, dict) else cookies

		cursor = conn.execute('''
			INSERT INTO accounts (name, provider_id, api_user, cookies, username, password, oauth_provider)
			VALUES (?, ?, ?, ?, ?, ?, ?)
		''', (name, provider.id, api_user, cookies_json, username, password, oauth_provider))
		conn.commit()
		return cursor.lastrowid

	def update_account(
		self,
		account_id: int,
		cookies: dict | str | None = None,
		name: str | None = None,
		username: str | None = None,
		password: str | None = None,
		oauth_provider: str | None = None,
		is_active: bool | None = None
	) -> bool:
		"""更新账号"""
		conn = self.connect()
		updates = []
		params = []

		if cookies is not None:
			cookies_json = json.dumps(cookies) if isinstance(cookies, dict) else cookies
			updates.append('cookies = ?')
			params.append(cookies_json)
		if name is not None:
			updates.append('name = ?')
			params.append(name)
		if username is not None:
			updates.append('username = ?')
			params.append(username)
		if password is not None:
			updates.append('password = ?')
			params.append(password)
		if oauth_provider is not None:
			updates.append('oauth_provider = ?')
			params.append(oauth_provider)
		if is_active is not None:
			updates.append('is_active = ?')
			params.append(1 if is_active else 0)

		if not updates:
			return False

		updates.append('updated_at = CURRENT_TIMESTAMP')
		params.append(account_id)

		sql = f'UPDATE accounts SET {", ".join(updates)} WHERE id = ?'
		cursor = conn.execute(sql, params)
		conn.commit()
		return cursor.rowcount > 0

	def delete_account(self, account_id: int) -> bool:
		"""删除账号（同时删除签到记录）"""
		conn = self.connect()
		conn.execute('DELETE FROM signin_records WHERE account_id = ?', (account_id,))
		cursor = conn.execute('DELETE FROM accounts WHERE id = ?', (account_id,))
		conn.commit()
		return cursor.rowcount > 0

	def _row_to_account(self, row: sqlite3.Row) -> AccountRow:
		"""将数据库行转换为 AccountRow"""
		cookies = {}
		if row['cookies']:
			try:
				cookies = json.loads(row['cookies'])
			except json.JSONDecodeError:
				cookies = {'raw': row['cookies']}

		return AccountRow(
			id=row['id'],
			name=row['name'],
			provider_id=row['provider_id'],
			provider_name=row['provider_name'],
			api_user=row['api_user'],
			cookies=cookies,
			username=row['username'],
			password=row['password'],
			oauth_provider=row['oauth_provider'],
			is_active=bool(row['is_active'])
		)

	# ============ SigninRecord CRUD ============

	def add_signin_record(
		self,
		account_id: int,
		signin_time: datetime,
		status: str,
		balance_before: float | None = None,
		balance_after: float | None = None,
		balance_diff: float | None = None,
		error_message: str | None = None
	) -> int:
		"""添加签到记录"""
		conn = self.connect()
		cursor = conn.execute('''
			INSERT INTO signin_records (account_id, signin_time, balance_before, balance_after,
			                           balance_diff, status, error_message)
			VALUES (?, ?, ?, ?, ?, ?, ?)
		''', (account_id, signin_time.isoformat(), balance_before, balance_after,
		      balance_diff, status, error_message))
		conn.commit()
		return cursor.lastrowid

	def get_signin_history(self, account_id: int, limit: int = 30) -> List[SigninRecordRow]:
		"""获取账号的签到历史"""
		conn = self.connect()
		cursor = conn.execute('''
			SELECT * FROM signin_records
			WHERE account_id = ?
			ORDER BY signin_time DESC
			LIMIT ?
		''', (account_id, limit))
		return [self._row_to_signin_record(row) for row in cursor.fetchall()]

	def get_last_signin(self, account_id: int) -> SigninRecordRow | None:
		"""获取账号最后一次签到记录"""
		conn = self.connect()
		cursor = conn.execute('''
			SELECT * FROM signin_records
			WHERE account_id = ?
			ORDER BY signin_time DESC
			LIMIT 1
		''', (account_id,))
		row = cursor.fetchone()
		return self._row_to_signin_record(row) if row else None

	def get_all_last_signins(self) -> dict[int, SigninRecordRow]:
		"""获取所有账号的最后一次签到记录"""
		conn = self.connect()
		# 使用窗口函数获取每个账号的最新记录
		cursor = conn.execute('''
			SELECT * FROM signin_records
			WHERE id IN (
				SELECT MAX(id) FROM signin_records GROUP BY account_id
			)
		''')
		return {row['account_id']: self._row_to_signin_record(row) for row in cursor.fetchall()}

	def _row_to_signin_record(self, row: sqlite3.Row) -> SigninRecordRow:
		"""将数据库行转换为 SigninRecordRow"""
		signin_time = row['signin_time']
		if isinstance(signin_time, str):
			signin_time = datetime.fromisoformat(signin_time)

		return SigninRecordRow(
			id=row['id'],
			account_id=row['account_id'],
			signin_time=signin_time,
			balance_before=row['balance_before'],
			balance_after=row['balance_after'],
			balance_diff=row['balance_diff'],
			status=row['status'],
			error_message=row['error_message']
		)


# ============ 迁移函数 ============


def migrate_providers_from_json(db: Database, providers_file: str) -> int:
	"""从 providers.json 迁移 Provider 配置"""
	if not os.path.exists(providers_file):
		return 0

	try:
		with open(providers_file, 'r', encoding='utf-8') as f:
			providers_data = json.load(f)

		count = 0
		for name, data in providers_data.items():
			db.upsert_provider(
				name=name,
				domain=data['domain'],
				login_path=data.get('login_path', '/login'),
				sign_in_path=data.get('sign_in_path', '/api/user/sign_in'),
				user_info_path=data.get('user_info_path', '/api/user/self'),
				api_user_key=data.get('api_user_key', 'new-api-user'),
				bypass_method=data.get('bypass_method'),
				waf_cookie_names=data.get('waf_cookie_names')
			)
			count += 1

		return count
	except Exception as e:
		print(f'[警告] 迁移 providers.json 失败: {e}')
		return 0


def migrate_accounts_from_env(db: Database) -> int:
	"""从环境变量迁移账号配置"""
	accounts_str = os.getenv('ANYROUTER_ACCOUNTS')
	if not accounts_str:
		return 0

	try:
		accounts_data = json.loads(accounts_str)
		if not isinstance(accounts_data, list):
			return 0

		count = 0
		for i, account in enumerate(accounts_data):
			provider_name = account.get('provider', 'anyrouter')

			# 确保 provider 存在
			if not db.get_provider_by_name(provider_name):
				print(f'[警告] Provider 不存在: {provider_name}，跳过账号 {i + 1}')
				continue

			# 检查账号是否已存在
			existing = db.get_account_by_key(provider_name, account['api_user'])
			if existing:
				# 更新现有账号
				db.update_account(
					account_id=existing.id,
					cookies=account['cookies'],
					name=account.get('name'),
					username=account.get('username'),
					password=account.get('password'),
					oauth_provider=account.get('oauth_provider')
				)
			else:
				# 创建新账号
				db.create_account(
					provider_name=provider_name,
					api_user=account['api_user'],
					cookies=account['cookies'],
					name=account.get('name'),
					username=account.get('username'),
					password=account.get('password'),
					oauth_provider=account.get('oauth_provider')
				)
			count += 1

		return count
	except Exception as e:
		print(f'[警告] 迁移账号配置失败: {e}')
		return 0


def migrate_signin_history_from_json(db: Database, history_file: str) -> int:
	"""从 signin_history.json 迁移签到历史"""
	if not os.path.exists(history_file):
		return 0

	try:
		with open(history_file, 'r', encoding='utf-8') as f:
			history_data = json.load(f)

		count = 0
		for key, record in history_data.items():
			# key 格式: provider_apiuser
			parts = key.split('_', 1)
			if len(parts) != 2:
				continue

			provider_name, api_user = parts
			account = db.get_account_by_key(provider_name, api_user)
			if not account:
				continue

			# 解析时间和余额
			if isinstance(record, str):
				signin_time = datetime.fromisoformat(record)
				balance = None
			elif isinstance(record, dict):
				signin_time = datetime.fromisoformat(record['time'])
				balance = record.get('balance')
			else:
				continue

			# 检查是否已存在该记录
			last_signin = db.get_last_signin(account.id)
			if last_signin and abs((last_signin.signin_time - signin_time).total_seconds()) < 60:
				# 已存在相近的记录，跳过
				continue

			db.add_signin_record(
				account_id=account.id,
				signin_time=signin_time,
				status='success',  # 历史记录默认为成功
				balance_after=balance
			)
			count += 1

		return count
	except Exception as e:
		print(f'[警告] 迁移签到历史失败: {e}')
		return 0


def init_database(
	db_path: str = DATABASE_FILE,
	providers_file: str = 'providers.json',
	history_file: str = 'data/signin_history.json'
) -> Database:
	"""初始化数据库并迁移数据

	Args:
	    db_path: 数据库文件路径
	    providers_file: providers.json 文件路径
	    history_file: signin_history.json 文件路径

	Returns:
	    初始化后的 Database 实例
	"""
	db = Database(db_path)

	# 检查是否需要初始化
	version = db.get_schema_version()
	if version is None:
		print('[数据库] 初始化数据库...')
		db.init_schema()

		# 迁移数据
		providers_count = migrate_providers_from_json(db, providers_file)
		if providers_count > 0:
			print(f'[迁移] 从 providers.json 导入了 {providers_count} 个 Provider')

		accounts_count = migrate_accounts_from_env(db)
		if accounts_count > 0:
			print(f'[迁移] 从环境变量导入了 {accounts_count} 个账号')

		history_count = migrate_signin_history_from_json(db, history_file)
		if history_count > 0:
			print(f'[迁移] 从 signin_history.json 导入了 {history_count} 条签到记录')

		print('[数据库] 初始化完成')
	else:
		print(f'[数据库] 使用现有数据库 (schema v{version})')

	return db


# 全局数据库实例
_db: Database | None = None


def get_database() -> Database:
	"""获取全局数据库实例"""
	global _db
	if _db is None:
		_db = init_database()
	return _db


def close_database() -> None:
	"""关闭全局数据库连接"""
	global _db
	if _db:
		_db.close()
		_db = None
