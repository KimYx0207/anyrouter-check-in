#!/usr/bin/env python3
"""签到结果和状态管理模块

职责：
1. 签到结果的数据结构
2. 签到历史的加载和保存
3. 余额变化检测
4. 状态判断逻辑

设计原则：
- 函数返回新状态，不修改输入参数
- 数据结构清晰，类型明确
"""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from utils.constants import (
	BALANCE_HASH_FILE,
	BALANCE_HASH_LENGTH,
	SIGNIN_COOLDOWN_HOURS,
	SIGNIN_HISTORY_FILE,
)

# 尝试导入数据库模块（可选依赖）
try:
	from utils.database import get_database
	HAS_DATABASE = True
except ImportError:
	HAS_DATABASE = False


def _atomic_write(file_path: str, content: str) -> None:
	"""原子性写入文件（write-to-temp + rename 模式）

	确保写入过程中崩溃不会损坏原文件。
	"""
	dir_path = os.path.dirname(file_path) or '.'
	fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
	try:
		with os.fdopen(fd, 'w', encoding='utf-8') as f:
			f.write(content)
			f.flush()
			os.fsync(f.fileno())
		os.replace(temp_path, file_path)  # 原子性替换
	except Exception:
		# 清理临时文件
		try:
			os.unlink(temp_path)
		except OSError:
			pass
		raise


class SigninStatus(Enum):
	"""签到状态枚举"""

	SUCCESS = 'success'  # 签到成功（余额增加）
	COOLDOWN = 'cooldown'  # 冷却期内
	FAILED = 'failed'  # 签到失败
	FIRST_RUN = 'first_run'  # 首次运行
	SKIPPED = 'skipped'  # 跳过（冷却期内主动跳过）
	ERROR = 'error'  # 发生错误


@dataclass
class UserBalance:
	"""用户余额信息"""

	quota: float  # 当前余额（美元）
	used_quota: float  # 已使用额度（美元）

	@property
	def display(self) -> str:
		"""格式化显示"""
		return f':money: 当前余额: ${self.quota}, 已使用: ${self.used_quota}'


@dataclass
class SigninRecord:
	"""签到记录"""

	time: datetime
	balance: float | None = None

	def to_dict(self) -> dict:
		"""转换为字典（用于 JSON 序列化）"""
		result = {'time': self.time.isoformat()}
		if self.balance is not None:
			result['balance'] = self.balance
		return result

	@classmethod
	def from_dict(cls, data: dict | str) -> 'SigninRecord | None':
		"""从字典或字符串创建记录"""
		try:
			if isinstance(data, str):
				# 旧格式：只有时间字符串
				return cls(time=datetime.fromisoformat(data), balance=None)
			elif isinstance(data, dict):
				# 新格式：包含时间和余额
				return cls(
					time=datetime.fromisoformat(data['time']),
					balance=data.get('balance')
				)
		except Exception:
			pass
		return None


@dataclass
class SigninResult:
	"""单个账号的签到结果

	不可变数据结构，用于传递签到结果，不产生副作用。
	"""

	account_key: str  # 账号唯一标识（provider_apiuser）
	account_name: str  # 账号显示名称
	status: SigninStatus  # 签到状态
	balance_before: float | None = None  # 签到前余额
	balance_after: float | None = None  # 签到后余额
	balance_diff: float | None = None  # 余额变化
	user_info: UserBalance | None = None  # 用户信息
	error: str | None = None  # 错误信息
	new_record: SigninRecord | None = None  # 需要保存的新记录
	last_signin: datetime | None = None  # 上次签到时间

	@property
	def is_success(self) -> bool:
		"""是否成功（包括首次运行）"""
		return self.status in (SigninStatus.SUCCESS, SigninStatus.FIRST_RUN, SigninStatus.COOLDOWN, SigninStatus.SKIPPED)

	@property
	def needs_notification(self) -> bool:
		"""是否需要发送通知"""
		return self.status in (SigninStatus.SUCCESS, SigninStatus.FAILED, SigninStatus.ERROR, SigninStatus.FIRST_RUN)


@dataclass
class SigninSummary:
	"""签到汇总结果"""

	total: int = 0
	success: int = 0
	cooldown: int = 0
	failed: int = 0
	results: list[SigninResult] = field(default_factory=list)
	balance_changed: bool = False
	is_first_run: bool = False

	def add_result(self, result: SigninResult) -> None:
		"""添加签到结果"""
		self.results.append(result)
		self.total += 1

		if result.status == SigninStatus.SUCCESS or result.status == SigninStatus.FIRST_RUN:
			self.success += 1
		elif result.status == SigninStatus.COOLDOWN or result.status == SigninStatus.SKIPPED:
			self.cooldown += 1
		elif result.status in (SigninStatus.FAILED, SigninStatus.ERROR):
			self.failed += 1

	@property
	def needs_notification(self) -> bool:
		"""是否需要发送通知"""
		return self.failed > 0 or self.success > 0 or self.balance_changed or self.is_first_run


# ============ 签到历史管理 ============


def load_signin_history() -> dict[str, SigninRecord]:
	"""加载签到历史

	Returns:
	    账号key到签到记录的映射
	"""
	try:
		if os.path.exists(SIGNIN_HISTORY_FILE):
			with open(SIGNIN_HISTORY_FILE, 'r', encoding='utf-8') as f:
				raw_data = json.load(f)

			history = {}
			for key, value in raw_data.items():
				record = SigninRecord.from_dict(value)
				if record:
					history[key] = record
			return history
	except Exception as e:
		print(f'[警告] 加载签到历史失败: {e}')
	return {}


def save_signin_history(history: dict[str, SigninRecord]) -> bool:
	"""保存签到历史

	Args:
	    history: 账号key到签到记录的映射

	Returns:
	    是否保存成功
	"""
	try:
		data = {key: record.to_dict() for key, record in history.items()}
		content = json.dumps(data, ensure_ascii=False, indent=2)
		_atomic_write(SIGNIN_HISTORY_FILE, content)
		return True
	except Exception as e:
		print(f'[警告] 保存签到历史失败: {e}')
		return False


def update_signin_history(
	history: dict[str, SigninRecord],
	results: list[SigninResult]
) -> dict[str, SigninRecord]:
	"""根据签到结果更新历史（返回新字典，不修改原字典）

	Args:
	    history: 原始签到历史
	    results: 签到结果列表

	Returns:
	    更新后的签到历史（新字典）
	"""
	new_history = dict(history)  # 创建副本

	for result in results:
		if result.new_record:
			new_history[result.account_key] = result.new_record

	return new_history


# ============ 余额 Hash 管理 ============


def load_balance_hash() -> str | None:
	"""加载余额 hash"""
	try:
		if os.path.exists(BALANCE_HASH_FILE):
			with open(BALANCE_HASH_FILE, 'r', encoding='utf-8') as f:
				return f.read().strip()
	except Exception as e:
		print(f'[警告] 加载余额 hash 失败: {e}')
	return None


def save_balance_hash(balance_hash: str) -> bool:
	"""保存余额 hash"""
	try:
		_atomic_write(BALANCE_HASH_FILE, balance_hash)
		return True
	except Exception as e:
		print(f'[警告] 保存余额 hash 失败: {e}')
		return False


def generate_balance_hash(balances: dict[str, float]) -> str:
	"""生成余额数据的 hash

	Args:
	    balances: 账号key到余额的映射

	Returns:
	    16位 hash 字符串
	"""
	if not balances:
		return ''
	balance_json = json.dumps(balances, sort_keys=True, separators=(',', ':'))
	return hashlib.sha256(balance_json.encode('utf-8')).hexdigest()[:BALANCE_HASH_LENGTH]


# ============ 冷却期检查 ============


def get_next_signin_time(last_signin: datetime | None) -> datetime | None:
	"""计算下次可签到时间"""
	if last_signin:
		return last_signin + timedelta(hours=SIGNIN_COOLDOWN_HOURS)
	return None


def is_in_cooldown(last_signin: datetime | None) -> bool:
	"""检查是否在冷却期内"""
	if not last_signin:
		return False
	next_signin = get_next_signin_time(last_signin)
	return next_signin is not None and datetime.now() < next_signin


def format_time_remaining(next_signin_time: datetime | None) -> str:
	"""格式化剩余时间"""
	if not next_signin_time:
		return '可以签到'

	now = datetime.now()
	if now >= next_signin_time:
		return '可以签到'

	remaining = next_signin_time - now
	hours = remaining.seconds // 3600
	minutes = (remaining.seconds % 3600) // 60

	if remaining.days > 0:
		return f'{remaining.days}天{hours}小时{minutes}分钟'
	elif hours > 0:
		return f'{hours}小时{minutes}分钟'
	else:
		return f'{minutes}分钟'


# ============ 余额变化检测 ============


def analyze_balance_change(
	current_balance: float,
	last_balance: float | None,
	last_signin: datetime | None
) -> tuple[SigninStatus, float | None]:
	"""分析余额变化，判断签到状态

	Args:
	    current_balance: 当前余额
	    last_balance: 上次记录的余额
	    last_signin: 上次签到时间

	Returns:
	    (签到状态, 余额变化值)
	"""
	if last_balance is None:
		# 首次运行
		return SigninStatus.FIRST_RUN, None

	diff = round(current_balance - last_balance, 2)

	if diff > 0:
		# 余额增加 = 签到成功
		return SigninStatus.SUCCESS, diff
	elif diff == 0:
		# 余额没变
		if is_in_cooldown(last_signin):
			return SigninStatus.COOLDOWN, 0.0
		else:
			# 不在冷却期但余额没增加 = 签到失败
			return SigninStatus.FAILED, 0.0
	else:
		# 余额减少 = 异常
		return SigninStatus.FAILED, diff


# ============ 数据库集成 ============


def save_signin_to_db(
	account_id: int,
	result: SigninResult
) -> bool:
	"""保存签到结果到数据库

	Args:
	    account_id: 数据库中的账号 ID
	    result: 签到结果

	Returns:
	    是否保存成功
	"""
	if not HAS_DATABASE:
		return False

	try:
		db = get_database()
		signin_time = result.new_record.time if result.new_record else datetime.now()

		db.add_signin_record(
			account_id=account_id,
			signin_time=signin_time,
			status=result.status.value,
			balance_before=result.balance_before,
			balance_after=result.balance_after,
			balance_diff=result.balance_diff,
			error_message=result.error
		)
		return True
	except Exception as e:
		print(f'[警告] 保存签到记录到数据库失败: {e}')
		return False


def save_all_signins_to_db(results: list[SigninResult]) -> int:
	"""批量保存签到结果到数据库

	Args:
	    results: 签到结果列表

	Returns:
	    成功保存的记录数
	"""
	if not HAS_DATABASE:
		return 0

	try:
		db = get_database()
		saved = 0

		for result in results:
			# 解析 account_key 获取 provider 和 api_user
			parts = result.account_key.split('_', 1)
			if len(parts) != 2:
				continue

			provider_name, api_user = parts

			# 查找账号 ID
			account = db.get_account_by_key(provider_name, api_user)
			if not account:
				print(f'[警告] 数据库中未找到账号: {result.account_key}')
				continue

			signin_time = result.new_record.time if result.new_record else datetime.now()

			db.add_signin_record(
				account_id=account.id,
				signin_time=signin_time,
				status=result.status.value,
				balance_before=result.balance_before,
				balance_after=result.balance_after,
				balance_diff=result.balance_diff,
				error_message=result.error
			)
			saved += 1

		return saved
	except Exception as e:
		print(f'[警告] 批量保存签到记录失败: {e}')
		return 0


def load_signin_history_from_db() -> dict[str, SigninRecord] | None:
	"""从数据库加载签到历史

	Returns:
	    账号key到签到记录的映射，数据库不可用时返回 None
	"""
	if not HAS_DATABASE:
		return None

	try:
		db = get_database()
		accounts = db.get_all_accounts(active_only=False)
		last_signins = db.get_all_last_signins()

		history = {}
		for account in accounts:
			account_key = f'{account.provider_name}_{account.api_user}'
			last_signin = last_signins.get(account.id)

			if last_signin and last_signin.status == 'success':
				history[account_key] = SigninRecord(
					time=last_signin.signin_time,
					balance=last_signin.balance_after
				)

		return history if history else None
	except Exception as e:
		print(f'[警告] 从数据库加载签到历史失败: {e}')
		return None


def load_signin_history_with_db() -> dict[str, SigninRecord]:
	"""加载签到历史（优先数据库，后备 JSON 文件）

	Returns:
	    账号key到签到记录的映射
	"""
	# 优先从数据库加载
	history = load_signin_history_from_db()
	if history:
		return history

	# 后备：从 JSON 文件加载
	return load_signin_history()
