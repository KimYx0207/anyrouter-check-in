#!/usr/bin/env python3
"""敏感数据脱敏模块

提供对 cookies、密码等敏感数据的脱敏处理，用于日志输出和 API 响应。
数据库中存储原始数据，脱敏仅在显示层应用。
"""


def mask_session(session: str | None) -> str:
	"""脱敏 session cookie

	Args:
	    session: session cookie 值

	Returns:
	    脱敏后的字符串，如 'abc...xyz'
	"""
	if not session:
		return ''
	if len(session) <= 6:
		return '***'
	return f'{session[:3]}...{session[-3:]}'


def mask_password(password: str | None) -> str:
	"""脱敏密码

	Args:
	    password: 密码字符串

	Returns:
	    脱敏后的字符串 '***'
	"""
	return '***' if password else ''


def mask_cookies(cookies: dict | str | None) -> dict | str:
	"""脱敏 cookies 字典

	Args:
	    cookies: cookies 字典或字符串

	Returns:
	    脱敏后的 cookies
	"""
	if not cookies:
		return {}

	if isinstance(cookies, str):
		# 字符串格式的 cookies，简单脱敏
		if 'session=' in cookies:
			return '***session=***'
		return cookies

	# 字典格式
	return {
		k: mask_session(v) if k == 'session' else v
		for k, v in cookies.items()
	}


def mask_api_user(api_user: str | None) -> str:
	"""脱敏 API 用户标识（可选，当前不脱敏）

	Args:
	    api_user: API 用户标识

	Returns:
	    原始值（API user 通常是数字 ID，不需要脱敏）
	"""
	return api_user or ''


def mask_account_for_log(
	name: str | None,
	provider: str,
	api_user: str,
	cookies: dict | str | None = None
) -> str:
	"""生成用于日志的脱敏账号信息

	Args:
	    name: 账号名称
	    provider: provider 名称
	    api_user: API 用户标识
	    cookies: cookies（可选）

	Returns:
	    格式化的脱敏字符串
	"""
	display_name = name or f'Account ({api_user})'
	if cookies:
		masked_cookies = mask_cookies(cookies)
		if isinstance(masked_cookies, dict) and 'session' in masked_cookies:
			return f'{display_name} [{provider}] session={masked_cookies["session"]}'
	return f'{display_name} [{provider}]'
