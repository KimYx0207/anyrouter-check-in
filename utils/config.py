#!/usr/bin/env python3
"""
配置管理模块
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal

# Provider 配置文件路径
PROVIDERS_FILE = Path(__file__).parent.parent / 'providers.json'


@dataclass
class ProviderConfig:
	"""Provider 配置"""

	name: str
	domain: str
	login_path: str = '/login'
	sign_in_path: str | None = '/api/user/sign_in'
	user_info_path: str = '/api/user/self'
	api_user_key: str = 'new-api-user'
	bypass_method: Literal['waf_cookies'] | None = None
	waf_cookie_names: List[str] | None = None

	def __post_init__(self):
		required_waf_cookies = set()
		if self.waf_cookie_names and isinstance(self.waf_cookie_names, List):
			for item in self.waf_cookie_names:
				name = "" if not item or not isinstance(item, str) else item.strip()
				if not name:
					print(f'[警告] 发现无效的 WAF cookie 名称: {item}')
					continue

				required_waf_cookies.add(name)

		if not required_waf_cookies:
			self.bypass_method = None

		self.waf_cookie_names = list(required_waf_cookies)

	@classmethod
	def from_dict(cls, name: str, data: dict) -> 'ProviderConfig':
		"""从字典创建 ProviderConfig

		配置格式:
		- 基础: {"domain": "https://example.com"}
		- 完整: {"domain": "https://example.com", "login_path": "/login", "api_user_key": "x-api-user", "bypass_method": "waf_cookies", ...}
		"""
		return cls(
			name=name,
			domain=data['domain'],
			login_path=data.get('login_path', '/login'),
			sign_in_path=data.get('sign_in_path', '/api/user/sign_in'),
			user_info_path=data.get('user_info_path', '/api/user/self'),
			api_user_key=data.get('api_user_key', 'new-api-user'),
			bypass_method=data.get('bypass_method'),
			waf_cookie_names = data.get('waf_cookie_names'),
		)

	def needs_waf_cookies(self) -> bool:
		"""判断是否需要获取 WAF cookies"""
		return self.bypass_method == 'waf_cookies'

	def needs_manual_check_in(self) -> bool:
		"""判断是否需要手动调用签到接口"""
		# 只有当sign_in_path不为None时才需要手动签到
		# agentrouter的sign_in_path为None，查询用户信息时自动完成签到
		return self.sign_in_path is not None


@dataclass
class AppConfig:
	"""应用配置"""

	providers: Dict[str, ProviderConfig]

	@classmethod
	def _load_providers_from_file(cls) -> Dict[str, ProviderConfig] | None:
		"""从 providers.json 文件加载配置"""
		if not PROVIDERS_FILE.exists():
			return None

		try:
			with open(PROVIDERS_FILE, 'r', encoding='utf-8') as f:
				providers_data = json.load(f)

			if not isinstance(providers_data, dict):
				print('[警告] providers.json 必须是 JSON 对象')
				return None

			providers = {}
			for name, provider_data in providers_data.items():
				try:
					providers[name] = ProviderConfig.from_dict(name, provider_data)
				except Exception as e:
					print(f'[警告] 解析 provider "{name}" 失败: {e}')

			print(f'[信息] 从 providers.json 加载了 {len(providers)} 个 provider')
			return providers

		except Exception as e:
			print(f'[警告] 读取 providers.json 失败: {e}')
			return None

	@classmethod
	def _get_default_providers(cls) -> Dict[str, ProviderConfig]:
		"""获取默认 provider 配置（硬编码后备）"""
		return {
			'anyrouter': ProviderConfig(
				name='anyrouter',
				domain='https://anyrouter.top',
				login_path='/login',
				sign_in_path='/api/user/sign_in',
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc', 'cdn_sec_tc', 'acw_sc__v2'],
			),
			'agentrouter': ProviderConfig(
				name='agentrouter',
				domain='https://agentrouter.org',
				login_path='/login',
				sign_in_path=None,
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc'],
			),
		}

	@classmethod
	def load_from_env(cls) -> 'AppConfig':
		"""从配置文件/环境变量加载配置

		优先级：
		1. providers.json 文件
		2. PROVIDERS 环境变量（JSON 格式，覆盖/扩展）
		3. 硬编码默认值（后备）
		"""
		# 1. 尝试从文件加载
		providers = cls._load_providers_from_file()

		# 2. 如果文件不存在，使用默认值
		if providers is None:
			providers = cls._get_default_providers()
			print('[信息] 使用默认 provider 配置')

		# 3. 从环境变量加载自定义 providers（覆盖/扩展）
		providers_str = os.getenv('PROVIDERS')
		if providers_str:
			try:
				providers_data = json.loads(providers_str)

				if not isinstance(providers_data, dict):
					print('[警告] PROVIDERS 必须是 JSON 对象，忽略自定义 providers')
					return cls(providers=providers)

				for name, provider_data in providers_data.items():
					try:
						providers[name] = ProviderConfig.from_dict(name, provider_data)
					except Exception as e:
						print(f'[警告] 解析 provider "{name}" 失败: {e}')

				print(f'[信息] 从 PROVIDERS 环境变量加载了 {len(providers_data)} 个自定义 provider')
			except json.JSONDecodeError as e:
				print(f'[警告] 解析 PROVIDERS 环境变量失败: {e}')
			except Exception as e:
				print(f'[警告] 加载 PROVIDERS 时出错: {e}')

		return cls(providers=providers)

	def get_provider(self, name: str) -> ProviderConfig | None:
		"""获取指定 provider 配置"""
		return self.providers.get(name)


@dataclass
class AccountConfig:
	"""账号配置"""

	cookies: dict | str
	api_user: str
	provider: str = 'anyrouter'
	name: str | None = None
	username: str | None = None  # 用于需要登录触发签到的 provider
	password: str | None = None  # 用于需要登录触发签到的 provider
	oauth_provider: str | None = None  # OAuth 提供商（github/google/linuxdo）

	@classmethod
	def from_dict(cls, data: dict, index: int) -> 'AccountConfig':
		"""从字典创建 AccountConfig"""
		provider = data.get('provider', 'anyrouter')
		name = data.get('name', f'Account {index + 1}')

		return cls(
			cookies=data['cookies'],
			api_user=data['api_user'],
			provider=provider,
			name=name if name else None,
			username=data.get('username'),
			password=data.get('password'),
			oauth_provider=data.get('oauth_provider'),
		)

	def has_login_credentials(self) -> bool:
		"""是否有登录凭据"""
		return bool(self.username and self.password)

	def has_oauth_config(self) -> bool:
		"""是否配置了 OAuth 登录"""
		return bool(self.oauth_provider)

	def get_display_name(self, index: int) -> str:
		"""获取显示名称"""
		return self.name if self.name else f'Account {index + 1}'


def load_accounts_config() -> list[AccountConfig] | None:
	"""从环境变量加载账号配置"""
	accounts_str = os.getenv('ANYROUTER_ACCOUNTS')
	if not accounts_str:
		print('错误: 未找到 ANYROUTER_ACCOUNTS 环境变量')
		return None

	try:
		# 先尝试直接解析
		try:
			accounts_data = json.loads(accounts_str)
		except json.JSONDecodeError as parse_error:
			# 如果失败，可能是因为JSON中有未转义的控制字符（比如换行符、制表符）
			# 尝试清理后再解析一次
			print(f'[警告] 初始 JSON 解析失败: {parse_error}')
			print('[信息] 正在尝试清理 JSON 字符串（这是一个临时方案，请使用单行 JSON 格式）...')

			# 简单清理：去除JSON结构外的控制字符
			# 注意：这只是一个workaround，最好的方式还是使用正确的单行JSON格式
			import re
			# 去除所有换行符、回车符和制表符（但这可能影响字符串值内部的内容）
			# 更安全的方式是只清理JSON结构外的空白字符，但这需要更复杂的解析
			cleaned_str = re.sub(r'[\n\r\t]+', '', accounts_str)
			# 去除多余的空格（JSON键值对之间的空格）
			cleaned_str = re.sub(r'\s+', ' ', cleaned_str)
			# 去除冒号和逗号后的空格
			cleaned_str = re.sub(r':\s+', ':', cleaned_str)
			cleaned_str = re.sub(r',\s+', ',', cleaned_str)

			try:
				accounts_data = json.loads(cleaned_str)
				print('[信息] 清理 JSON 字符串后解析成功')
				print('[提示] 为获得更好的性能，请在环境变量中使用单行 JSON 格式')
			except json.JSONDecodeError as second_error:
				print(f'[错误] 清理后 JSON 解析仍然失败: {second_error}')
				print('[提示] 请检查你的 JSON 格式：')
				print('[提示]   - 使用在线 JSON 验证器（https://jsonlint.com/）')
				print('[提示]   - 确保所有引号都正确转义')
				print('[提示]   - 使用单行格式: [{"cookies":{...},"api_user":"..."}]')
				raise second_error

		if not isinstance(accounts_data, list):
			print('错误: 账号配置必须使用数组格式 [{}]')
			return None

		accounts = []
		for i, account_dict in enumerate(accounts_data):
			if not isinstance(account_dict, dict):
				print(f'错误: 账号 {i + 1} 配置格式不正确')
				return None

			if 'cookies' not in account_dict or 'api_user' not in account_dict:
				print(f'错误: 账号 {i + 1} 缺少必需字段（cookies, api_user）')
				return None

			if 'name' in account_dict and not account_dict['name']:
				print(f'错误: 账号 {i + 1} name 字段不能为空')
				return None

			accounts.append(AccountConfig.from_dict(account_dict, i))

		return accounts
	except Exception as e:
		print(f'错误: 账号配置格式不正确: {e}')
		return None
