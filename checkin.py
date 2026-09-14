#!/usr/bin/env python3
"""
公益站自动签到脚本

支持 AnyRouter、AgentRouter 等基于 NewAPI/OneAPI 的平台。
基于余额变化判断签到是否成功，并记录到数据库。
"""

import asyncio
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cloakbrowser import launch_async
from dotenv import load_dotenv

from utils.browser import (
	BrowserLoginResult,
	has_session_cookie,
	is_logged_in,
	launch_login_context,
	load_browser_login_settings,
	login_with_email_form,
	navigate_login_page,
	prepare_browser_page,
	save_login_screenshot,
	verify_browser_login,
	wait_for_waf_ready,
)
from utils.config import AccountConfig, AppConfig, load_accounts_config
from utils.debug import debug_print, is_debug_enabled
from utils.notify import notify
from utils.proxy import get_playwright_proxy, get_proxy_server
from utils.result import (
	SigninRecord,
	SigninResult,
	SigninStatus,
	analyze_balance_change,
	format_notification_line,
	generate_balance_hash,
	is_in_cooldown,
	load_balance_hash,
	load_signin_history_with_db,
	save_all_signins_to_db,
	save_balance_hash,
	save_signin_history,
	update_signin_history,
)

load_dotenv(Path(__file__).with_name('.env'))


@dataclass(frozen=True)
class CheckInAttempt:
	"""签到接口调用结果。"""

	success: bool
	error: str | None = None

	def __bool__(self) -> bool:
		return self.success


def parse_cookies(cookies_data):
	"""解析 cookies 数据"""
	if isinstance(cookies_data, dict):
		return cookies_data

	if isinstance(cookies_data, str):
		cookies_dict = {}
		for cookie in cookies_data.split(';'):
			if '=' in cookie:
				key, value = cookie.strip().split('=', 1)
				cookies_dict[key] = value
		return cookies_dict
	return {}


async def get_waf_cookies_with_browser(
	account_name: str,
	login_url: str,
	required_cookies: list[str],
	*,
	use_proxy: bool = False,
):
	"""使用浏览器获取 WAF cookies"""
	print(f'[PROCESSING] {account_name}: Starting browser to get WAF cookies...')

	launch_kwargs: dict = {'headless': True}
	proxy = get_playwright_proxy(use_proxy=use_proxy)
	if proxy:
		launch_kwargs['proxy'] = proxy
	browser = await launch_async(**launch_kwargs)

	try:
		page = await browser.new_page()
		await prepare_browser_page(page)
		print(f'[PROCESSING] {account_name}: Access login page to get initial cookies...')

		await page.goto(login_url, wait_until='domcontentloaded')
		await wait_for_waf_ready(page)

		cookies = await page.context.cookies()

		waf_cookies = {}
		for cookie in cookies:
			cookie_name = cookie.get('name')
			cookie_value = cookie.get('value')
			if cookie_name in required_cookies and cookie_value is not None:
				waf_cookies[cookie_name] = cookie_value

		print(f'[INFO] {account_name}: Got {len(waf_cookies)} WAF cookies')

		missing_cookies = [c for c in required_cookies if c not in waf_cookies]

		if missing_cookies:
			print(f'[FAILED] {account_name}: Missing WAF cookies: {missing_cookies}')
			await browser.close()
			return None

		print(f'[SUCCESS] {account_name}: Successfully got all WAF cookies')
		await browser.close()
		return waf_cookies

	except Exception as e:
		print(f'[FAILED] {account_name}: Error occurred while getting WAF cookies: {e}')
		await browser.close()
		return None


async def login_with_credentials(
	account_name: str,
	provider_config,
	provider_name: str,
	email: str,
	password: str,
) -> BrowserLoginResult | None:
	"""使用邮箱密码通过浏览器登录，返回 cookies 与拦截到的 api user id。"""
	print(f'[PROCESSING] {account_name}: Logging in with email/password...')

	login_url = f'{provider_config.domain}{provider_config.login_path}'
	settings = load_browser_login_settings(
		account_name,
		provider_name,
		persist_profile=provider_config.persist_profile,
	)
	timeout_ms = settings.wait_timeout_ms

	debug_print(
		f'[INFO] {account_name}: Browser profile={settings.profile_dir}, '
		f'persist={settings.persist_profile}, headless={settings.headless}, '
		f'humanize={settings.humanize}, timeout={timeout_ms}ms'
	)

	print(
		f'[INFO] {account_name}: Provider proxy={"enabled" if provider_config.use_proxy else "disabled"} '
		f'({provider_name})'
	)

	try:
		context = await launch_login_context(settings, use_proxy=provider_config.use_proxy)
	except Exception as e:
		print(f'[FAILED] {account_name}: Browser launch failed: {e}')
		return None

	page = None
	try:
		page = await context.new_page()
		await prepare_browser_page(page)
		await navigate_login_page(
			page,
			login_url,
			timeout_ms,
			provider=provider_name,
			account_name=account_name,
		)

		if not await is_logged_in(page):
			if await has_session_cookie(page):
				print(f'[WARN] {account_name}: Stale session cookie on login page, forcing email login')
			await save_login_screenshot(page, provider_name, account_name, 'before-email-login')
			await login_with_email_form(
				page,
				email,
				password,
				timeout_ms,
				provider=provider_name,
				account_name=account_name,
			)
		else:
			print(f'[INFO] {account_name}: Browser profile already logged in')

		console_url = f'{provider_config.domain}/console'
		user_profile = await verify_browser_login(page, console_url, timeout_ms)
		if not user_profile:
			cookies = await context.cookies()
			cookie_names = [c.get('name') for c in cookies if c.get('name')]
			print(f'[FAILED] {account_name}: Login failed - /api/user/self not verified')
			debug_print(f'[INFO] {account_name}: Current URL: {page.url}')
			debug_print(f'[INFO] {account_name}: Got cookies: {cookie_names}')
			await save_login_screenshot(page, provider_name, account_name, 'not-authenticated')
			await context.close()
			return None

		cookies = await context.cookies()
		all_cookies: dict[str, str] = {}
		for cookie in cookies:
			cookie_name, cookie_value = cookie.get('name'), cookie.get('value')
			if cookie_name and cookie_value:
				all_cookies[cookie_name] = cookie_value
		api_user = str(user_profile['id']) if user_profile.get('id') is not None else None

		success_msg = f'[SUCCESS] {account_name}: Login successful, got {len(all_cookies)} cookies'
		if is_debug_enabled() and api_user:
			success_msg += f', api_user={api_user}'
		print(success_msg)
		await context.close()
		return BrowserLoginResult(cookies=all_cookies, api_user=api_user)

	except Exception as e:
		print(f'[FAILED] {account_name}: Error during login: {e}')
		if page is not None:
			await save_login_screenshot(page, provider_name, account_name, 'login-error')
		await context.close()
		return None


def get_user_info(client, headers, user_info_url: str):
	"""获取用户信息"""
	try:
		response = client.get(user_info_url, headers=headers, timeout=30)

		if response.status_code == 200 and response.headers.get('content-type', '').startswith('application/json'):
			return parse_user_info_payload(response.json(), response.status_code)
		return {'success': False, 'error': format_http_error(response.status_code)}
	except Exception as e:
		return {'success': False, 'error': f'获取用户信息失败: {str(e)[:50]}...'}


def format_http_error(status_code: int) -> str:
	"""保留状态码，并为登录失效给出可执行的处理说明。"""
	if status_code == 401:
		return 'HTTP 401：登录凭据失效或不匹配，请重新登录站点并更新 session 和 api_user'
	return f'HTTP {status_code}'


def parse_user_info_payload(data: dict, status_code: int = 200):
	"""解析用户信息响应。"""
	if status_code == 200 and data.get('success'):
		user_data = data.get('data', {})
		quota = round(user_data.get('quota', 0) / 500000, 2)
		used_quota = round(user_data.get('used_quota', 0) / 500000, 2)
		return {
			'success': True,
			'quota': quota,
			'used_quota': used_quota,
			'display': f'当前余额: ${quota}, 已用: ${used_quota}',
		}
	return {
		'success': False,
		'error': format_http_error(status_code) if status_code != 200 else data.get('message', '获取用户信息失败'),
	}


async def prepare_cookies(account_name: str, provider_config, user_cookies: dict) -> dict | None:
	"""准备请求所需的 cookies（可能包含 WAF cookies）"""
	waf_cookies = {}

	if provider_config.needs_waf_cookies():
		login_url = f'{provider_config.domain}{provider_config.login_path}'
		waf_cookies = await get_waf_cookies_with_browser(
			account_name, login_url, provider_config.waf_cookie_names, use_proxy=provider_config.use_proxy
		)
		if not waf_cookies:
			print(f'[失败] {account_name}: 无法获取 WAF cookies')
			return None
		# 已保存的防护 Cookie 属于旧浏览器会话，不能替代本次获取的值。
		waf_cookie_names = set(provider_config.waf_cookie_names or [])
		user_cookies = {key: value for key, value in user_cookies.items() if key not in waf_cookie_names}
	else:
		print(f'[信息] {account_name}: 服务商 {provider_config.name} 无需绕过 WAF，直接使用用户 cookies')

	return {**user_cookies, **waf_cookies}


async def browser_fetch_json(page, url: str, method: str, api_user_key: str, api_user: str):
	"""在浏览器上下文里请求 API，保留 WAF JS 校验产生的浏览器状态。"""
	return await page.evaluate(
		"""async ({url, method, apiUserKey, apiUser}) => {
			const response = await fetch(url, {
				method,
				credentials: 'include',
				headers: {
					'Accept': 'application/json, text/plain, */*',
					'Content-Type': 'application/json',
					'X-Requested-With': 'XMLHttpRequest',
					[apiUserKey]: apiUser
				}
			});
			return {
				status: response.status,
				contentType: response.headers.get('content-type') || '',
				text: await response.text()
			};
		}""",
		{
			'url': url,
			'method': method,
			'apiUserKey': api_user_key,
			'apiUser': api_user,
		},
	)


def parse_json_response(response: dict):
	"""解析浏览器 fetch 的 JSON 响应。"""
	if not response.get('contentType', '').startswith('application/json'):
		return None
	try:
		return json.loads(response.get('text') or '')
	except json.JSONDecodeError:
		return None


def parse_browser_user_info_response(response: dict) -> dict:
	"""区分登录错误与站点防护返回的 HTML，避免掩盖真实失败原因。"""
	status = response.get('status', 0)
	payload = parse_json_response(response)
	if isinstance(payload, dict):
		return parse_user_info_payload(payload, status)
	if status != 200:
		return {'success': False, 'error': format_http_error(status)}
	content_type = str(response.get('contentType') or '未知')[:100]
	return {
		'success': False,
		'error': f'HTTP {status}，用户信息响应不是有效 JSON（{content_type}），请检查站点验证或网络',
	}


def parse_browser_check_in_response(response: dict, account_name: str) -> CheckInAttempt:
	"""解析浏览器上下文里的签到响应。"""
	status = response.get('status')
	if status and status != 200:
		error = format_http_error(status)
		print(f'[失败] {account_name}: 签到失败 - {error}')
		return CheckInAttempt(False, error)

	result = parse_json_response(response)
	if result:
		return parse_check_in_result(result, account_name)

	print(f'[失败] {account_name}: 签到失败 - 响应格式无效')
	return CheckInAttempt(False, '响应格式无效')


async def complete_visible_slider(page, account_name: str) -> bool:
	"""在站点显示阿里云滑块时完成一次交互，结果仍由后续 API 验证。"""
	for frame in page.frames:
		track = frame.locator('.nc_scale').first
		handle = track.locator('.btn_slide').first
		if not await handle.is_visible():
			continue
		track_box = await track.bounding_box()
		handle_box = await handle.bounding_box()
		if not track_box or not handle_box or track_box['width'] <= handle_box['width']:
			continue
		start_x = handle_box['x'] + handle_box['width'] / 2
		y = handle_box['y'] + handle_box['height'] / 2
		end_x = track_box['x'] + track_box['width'] - handle_box['width'] / 2
		print(f'[验证] {account_name}: 页面要求滑块验证，完成一次交互')
		await page.mouse.move(start_x, y)
		await page.mouse.down()
		try:
			await page.mouse.move(end_x, y, steps=20)
		finally:
			await page.mouse.up()
		return True
	return False


async def check_in_with_browser(account: AccountConfig, account_name: str, provider_config):
	"""复用上游浏览器启动逻辑，在同一个上下文中完成 WAF 校验和 API 请求。"""
	api_user = account.api_user
	if not api_user:
		return CheckInAttempt(False, 'Cookie 登录缺少 api_user'), None, None
	print(f'[处理中] {account_name}: 启动浏览器执行签到请求...')
	settings = load_browser_login_settings(account_name, account.provider, persist_profile=False)
	context = None
	try:
		context = await launch_login_context(settings, use_proxy=provider_config.use_proxy)
		page = await context.new_page()
		await prepare_browser_page(page)
		await page.goto(
			f'{provider_config.domain}{provider_config.login_path}',
			wait_until='domcontentloaded',
			timeout=settings.wait_timeout_ms,
		)
		await wait_for_waf_ready(page, timeout_ms=settings.wait_timeout_ms)
		if account.provider == 'agentrouter' and await complete_visible_slider(page, account_name):
			await wait_for_waf_ready(page, timeout_ms=settings.wait_timeout_ms)

		domain = urlparse(provider_config.domain).hostname
		waf_cookie_names = set(provider_config.waf_cookie_names or [])
		await context.add_cookies(
			[
				{
					'name': key,
					'value': str(value),
					'domain': domain,
					'path': '/',
					'httpOnly': True,
					'secure': provider_config.domain.startswith('https://'),
					'sameSite': 'Lax',
				}
				for key, value in parse_cookies(account.cookies).items()
				if key not in waf_cookie_names
			]
		)

		if provider_config.name == 'anyrouter':
			await page.goto(
				f'{provider_config.domain}/console/token',
				wait_until='domcontentloaded',
				timeout=settings.wait_timeout_ms,
			)
			await wait_for_waf_ready(page, timeout_ms=settings.wait_timeout_ms)

		user_info_url = f'{provider_config.domain}{provider_config.user_info_path}'
		before_response = await browser_fetch_json(page, user_info_url, 'GET', provider_config.api_user_key, api_user)
		user_info_before = parse_browser_user_info_response(before_response)
		if not user_info_before.get('success'):
			error = user_info_before.get('error', '获取用户信息失败')
			print(f'[失败] {account_name}: {error}')
			if is_debug_enabled():
				debug_print(f'[诊断] {account_name}: 当前页面标题 {await page.title()}')
				await save_login_screenshot(page, account.provider, account_name, 'user-info-failed')
			return CheckInAttempt(False, error), user_info_before, None
		print(f'[签到前] {user_info_before["display"]}')

		if provider_config.needs_manual_check_in():
			print(f'[网络] {account_name}: 在浏览器上下文执行签到请求')
			sign_in_url = f'{provider_config.domain}{provider_config.sign_in_path}'
			sign_in_response = await browser_fetch_json(
				page, sign_in_url, 'POST', provider_config.api_user_key, api_user
			)
			print(f'[响应] {account_name}: 响应状态码 {sign_in_response["status"]}')
			api_attempt = parse_browser_check_in_response(sign_in_response, account_name)
		else:
			api_attempt = CheckInAttempt(
				bool(user_info_before and user_info_before.get('success')),
				None if user_info_before.get('success') else user_info_before.get('error', '获取用户信息失败'),
			)
			if api_attempt.success:
				print(f'[信息] {account_name}: 签到已自动完成（通过用户信息请求触发）')

		after_response = await browser_fetch_json(page, user_info_url, 'GET', provider_config.api_user_key, api_user)
		user_info_after = parse_browser_user_info_response(after_response)
		if user_info_after.get('success'):
			print(f'[签到后] {user_info_after["display"]}')
		return api_attempt, user_info_before, user_info_after
	except Exception as e:
		print(f'[失败] {account_name}: 浏览器签到过程中发生错误: {e}')
		return CheckInAttempt(False, str(e)[:100]), None, None
	finally:
		if context is not None:
			await context.close()


def parse_check_in_result(result: dict | None, account_name: str) -> CheckInAttempt:
	"""解析签到接口响应。"""
	if not result:
		print(f'[失败] {account_name}: 签到失败 - 响应格式无效')
		return CheckInAttempt(False, '响应格式无效')

	if result.get('ret') == 1 or result.get('code') == 0 or result.get('success'):
		print(f'[成功] {account_name}: 签到成功！')
		return CheckInAttempt(True)

	error_msg = result.get('msg', result.get('message', '未知错误'))
	already_checked_keywords = ['已经签到', '已签到', '重复签到', 'already checked', 'already signed']
	if any(keyword in str(error_msg).lower() for keyword in already_checked_keywords):
		print(f'[成功] {account_name}: 今日已签到')
		return CheckInAttempt(True)

	print(f'[失败] {account_name}: 签到失败 - {error_msg}')
	return CheckInAttempt(False, str(error_msg))


def execute_check_in(client, account_name: str, provider_config, headers: dict):
	"""执行签到请求"""
	print(f'[网络] {account_name}: 执行签到请求')

	checkin_headers = headers.copy()
	checkin_headers.update({'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

	sign_in_url = f'{provider_config.domain}{provider_config.sign_in_path}'
	response = client.post(sign_in_url, headers=checkin_headers, timeout=30)

	print(f'[响应] {account_name}: 响应状态码 {response.status_code}')

	if response.status_code == 200:
		try:
			return parse_check_in_result(response.json(), account_name)
		except json.JSONDecodeError:
			# 如果不是 JSON 响应，检查是否包含成功标识
			if 'success' in response.text.lower():
				print(f'[成功] {account_name}: 签到成功！')
				return CheckInAttempt(True)
			else:
				print(f'[失败] {account_name}: 签到失败 - 响应格式无效')
				return CheckInAttempt(False, '响应格式无效')
	else:
		error = format_http_error(response.status_code)
		print(f'[失败] {account_name}: 签到失败 - {error}')
		return CheckInAttempt(False, error)


async def check_in_account(
	account: AccountConfig, account_index: int, app_config: AppConfig, signin_history: dict[str, SigninRecord]
) -> SigninResult:
	"""为单个账号执行签到操作，基于余额变化判断结果

	Args:
	    account: 账号配置
	    account_index: 账号索引
	    app_config: 应用配置
	    signin_history: 签到历史记录

	Returns:
	    SigninResult: 签到结果
	"""
	account_name = account.get_display_name(account_index)
	account_key = f'{account.provider}_{account.api_user or account_index + 1}'

	print(f'\n[处理中] 开始处理 {account_name}')

	provider_config = app_config.get_provider(account.provider)
	if not provider_config:
		print(f'[失败] {account_name}: 服务商 "{account.provider}" 未在配置中找到')
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			error=f'服务商 "{account.provider}" 未找到',
		)

	print(f'[信息] {account_name}: 使用服务商 "{account.provider}" ({provider_config.domain})')

	if account.email and account.password:
		login_result = await login_with_credentials(
			account_name, provider_config, account.provider, account.email, account.password
		)
		if not login_result or not (login_result.api_user or account.api_user):
			return SigninResult(
				account_key=account_key,
				account_name=account_name,
				status=SigninStatus.ERROR,
				error='邮箱密码登录失败，未使用旧 Cookie 继续签到',
			)
		account = replace(
			account,
			cookies=login_result.cookies,
			api_user=login_result.api_user or account.api_user,
		)
		account_key = f'{account.provider}_{account.api_user}'

	# 获取上次签到记录
	last_record = signin_history.get(account_key)
	last_signin_time = last_record.time if last_record else None
	last_balance = last_record.balance if last_record else None

	# 检查冷却期
	if is_in_cooldown(last_signin_time):
		from utils.result import format_time_remaining, get_next_signin_time

		next_time = get_next_signin_time(last_signin_time)
		remaining = format_time_remaining(next_time)
		print(f'[跳过] {account_name}: 冷却期内，剩余 {remaining}')
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.SKIPPED,
			balance_before=last_balance,
			balance_after=last_balance,
			last_signin=last_signin_time,
		)

	user_cookies = parse_cookies(account.cookies)
	if not user_cookies:
		print(f'[失败] {account_name}: 配置格式无效')
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			error='配置格式无效',
		)

	if provider_config.needs_waf_cookies():
		api_attempt, user_info_before, user_info_after = await check_in_with_browser(
			account, account_name, provider_config
		)
		balance_before = (
			user_info_before.get('quota') if user_info_before and user_info_before.get('success') else last_balance
		)
		balance_after = user_info_after.get('quota') if user_info_after and user_info_after.get('success') else None

		if not api_attempt.success:
			status, balance_diff = SigninStatus.FAILED, None
		elif balance_after is not None:
			status, balance_diff = analyze_balance_change(balance_after, balance_before, last_signin_time)
		else:
			status = SigninStatus.SUCCESS if api_attempt.success else SigninStatus.FAILED
			balance_diff = None

		from utils.result import UserBalance

		user_balance = None
		if user_info_after and user_info_after.get('success'):
			user_balance = UserBalance(quota=user_info_after['quota'], used_quota=user_info_after['used_quota'])

		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=status,
			balance_before=balance_before if balance_before is not None else last_balance,
			balance_after=balance_after,
			balance_diff=balance_diff,
			user_info=user_balance,
			error=api_attempt.error if status == SigninStatus.FAILED else None,
			last_signin=last_signin_time,
			new_record=SigninRecord(time=datetime.now(), balance=balance_after) if api_attempt.success else None,
		)

	all_cookies = await prepare_cookies(account_name, provider_config, user_cookies)
	if not all_cookies:
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			error='无法获取 WAF cookies',
		)

	client = httpx.Client(timeout=30.0, proxy=get_proxy_server(use_proxy=provider_config.use_proxy), trust_env=False)

	try:
		client.cookies.update(all_cookies)

		headers = {
			'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
			'Accept': 'application/json, text/plain, */*',
			'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
			'Accept-Encoding': 'gzip, deflate, br, zstd',
			'Referer': provider_config.domain,
			'Origin': provider_config.domain,
			'Connection': 'keep-alive',
			'Sec-Fetch-Dest': 'empty',
			'Sec-Fetch-Mode': 'cors',
			'Sec-Fetch-Site': 'same-origin',
			provider_config.api_user_key: account.api_user,
		}

		user_info_url = f'{provider_config.domain}{provider_config.user_info_path}'

		# 签到前获取余额
		user_info_before = get_user_info(client, headers, user_info_url)
		balance_before = (
			user_info_before.get('quota') if user_info_before and user_info_before.get('success') else last_balance
		)

		if user_info_before and user_info_before.get('success'):
			print(f'[签到前] {user_info_before["display"]}')
		elif user_info_before:
			print(f'[警告] {user_info_before.get("error", "未知错误")}')

		# 执行签到
		if provider_config.needs_manual_check_in():
			api_attempt = execute_check_in(client, account_name, provider_config, headers)
		else:
			api_attempt = CheckInAttempt(
				bool(user_info_before and user_info_before.get('success')),
				None if user_info_before.get('success') else user_info_before.get('error', '获取用户信息失败'),
			)
			if api_attempt.success:
				print(f'[信息] {account_name}: 签到已自动完成（通过用户信息请求触发）')

		# 签到后获取余额
		user_info_after = get_user_info(client, headers, user_info_url)
		balance_after = user_info_after.get('quota') if user_info_after and user_info_after.get('success') else None

		if user_info_after and user_info_after.get('success'):
			print(f'[签到后] {user_info_after["display"]}')

		# 基于余额变化分析签到结果
		if not api_attempt.success:
			status, balance_diff = SigninStatus.FAILED, None
		elif balance_after is not None:
			status, balance_diff = analyze_balance_change(balance_after, balance_before, last_signin_time)

			if status == SigninStatus.SUCCESS:
				print(f'[成功] {account_name}: 签到成功！余额增加 ${balance_diff}')
			elif status == SigninStatus.FIRST_RUN:
				print(f'[首次] {account_name}: 首次运行，当前余额 ${balance_after}')
			elif status == SigninStatus.COOLDOWN:
				if balance_diff and balance_diff < 0:
					print(f'[信息] {account_name}: 余额减少 ${abs(balance_diff)}（正常消耗），今日已签到')
				else:
					print(f'[信息] {account_name}: 余额无变化，今日已签到')
		else:
			# 无法获取余额，使用 API 返回结果判断
			status = SigninStatus.SUCCESS if api_attempt.success else SigninStatus.FAILED
			balance_diff = None
			if api_attempt.success:
				print(f'[成功] {account_name}: API 返回签到成功（无法验证余额）')
			else:
				print(f'[失败] {account_name}: 签到失败 - {api_attempt.error or "未知错误"}')

		# 构建用户信息
		from utils.result import UserBalance

		user_balance = None
		if user_info_after and user_info_after.get('success'):
			user_balance = UserBalance(quota=user_info_after['quota'], used_quota=user_info_after['used_quota'])

		# 创建签到记录（用于更新历史）
		new_record = SigninRecord(time=datetime.now(), balance=balance_after) if api_attempt.success else None

		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=status,
			balance_before=balance_before if balance_before is not None else last_balance,
			balance_after=balance_after,
			balance_diff=balance_diff,
			user_info=user_balance,
			error=api_attempt.error if status == SigninStatus.FAILED else None,
			last_signin=last_signin_time,
			new_record=new_record,
		)

	except Exception as e:
		print(f'[失败] {account_name}: 签到过程中发生错误 - {str(e)[:50]}...')
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			error=str(e)[:100],
		)
	finally:
		client.close()


async def main():
	"""主函数"""
	print('[系统] 公益站多账号自动签到脚本启动')
	print(f'[时间] 执行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

	app_config = AppConfig.load_from_env()
	print(f'[信息] 已加载 {len(app_config.providers)} 个服务商配置')

	accounts = load_accounts_config()
	if not accounts:
		print('[失败] 无法加载账号配置，程序退出')
		sys.exit(1)

	print(f'[信息] 找到 {len(accounts)} 个账号配置')

	# 加载签到历史（优先数据库，后备 JSON）
	signin_history = load_signin_history_with_db()
	print(f'[信息] 已加载 {len(signin_history)} 条签到历史')

	# 加载余额 hash（用于检测变化）
	last_balance_hash = load_balance_hash()

	# 执行签到
	results: list[SigninResult] = []
	for i, account in enumerate(accounts):
		try:
			result = await check_in_account(account, i, app_config, signin_history)
			results.append(result)
		except Exception as e:
			account_name = account.get_display_name(i)
			account_key = f'{account.provider}_{account.api_user}'
			print(f'[失败] {account_name} 处理异常: {e}')
			results.append(
				SigninResult(
					account_key=account_key,
					account_name=account_name,
					status=SigninStatus.ERROR,
					error=str(e)[:100],
				)
			)

	# 统计结果 - 四类状态互斥
	success_count = sum(1 for r in results if r.is_success)  # SUCCESS + FIRST_RUN
	failed_count = sum(1 for r in results if r.status in (SigninStatus.FAILED, SigninStatus.ERROR))
	cooldown_count = sum(1 for r in results if r.status in (SigninStatus.SKIPPED, SigninStatus.COOLDOWN))
	total_count = len(results)

	print(f'\n[统计] 签到完成: 成功 {success_count}, 失败 {failed_count}, 冷却 {cooldown_count}, 总计 {total_count}')

	# 更新签到历史
	new_history = update_signin_history(signin_history, results)
	save_signin_history(new_history)

	# 保存签到记录到数据库
	saved_count = save_all_signins_to_db(results)
	if saved_count > 0:
		print(f'[数据库] 已保存 {saved_count} 条签到记录')

	# 检查余额变化
	current_balances: dict[str, float | dict[str, float | None]] = {
		r.account_key: {
			'quota': r.balance_after,
			'used': r.user_info.used_quota if r.user_info else None,
		}
		for r in results
		if r.balance_after is not None
	}
	current_balance_hash = generate_balance_hash(current_balances) if current_balances else None

	balance_changed = False
	is_first_run = False
	if current_balance_hash:
		if last_balance_hash is None:
			balance_changed = True
			is_first_run = True
			print('[通知] 检测到首次运行，将发送当前余额通知')
		elif current_balance_hash != last_balance_hash:
			balance_changed = True
			print('[通知] 检测到余额变化，将发送通知')
		else:
			print('[信息] 未检测到余额变化')

		# 保存余额 hash
		save_balance_hash(current_balance_hash)

	# 判断是否需要发送通知
	need_notify = (
		failed_count > 0
		or balance_changed
		or any(r.status in (SigninStatus.SUCCESS, SigninStatus.SKIPPED) for r in results)
	)

	if need_notify:
		# 构建通知内容
		notification_lines = []

		for result in results:
			notification_lines.append(format_notification_line(result))

		# 统计摘要（使用统一的计数变量）
		summary = [
			'',
			(
				f'[统计] 签到结果: 总计: {total_count} | 成功: {success_count} | '
				f'冷却: {cooldown_count} | 失败: {failed_count}'
			),
		]

		time_info = f'[时间] 执行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'

		notify_content = '\n'.join([time_info, '', *notification_lines, *summary])

		print('\n' + notify_content)
		notify.push_message('公益站签到提醒', notify_content, msg_type='text')
		print('\n[通知] 已发送签到通知')
	else:
		print('[信息] 无需发送通知（全部跳过且余额无变化）')

	# 设置退出码
	sys.exit(0 if failed_count == 0 else 1)


def run_main():
	"""运行主函数的包装函数"""
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print('\n[警告] 程序被用户中断')
		sys.exit(1)
	except Exception as e:
		print(f'\n[失败] 程序执行过程中发生错误: {e}')
		sys.exit(1)


if __name__ == '__main__':
	run_main()
