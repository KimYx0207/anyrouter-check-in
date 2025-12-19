#!/usr/bin/env python3
"""
AnyRouter.top 自动签到脚本
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime

import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from utils.config import AccountConfig, AppConfig, load_accounts_config

# 先加载环境变量，再导入 notify 模块
load_dotenv()
from utils.notify import notify

BALANCE_HASH_FILE = 'balance_hash.txt'


def load_balance_hash():
	"""加载余额hash"""
	try:
		if os.path.exists(BALANCE_HASH_FILE):
			with open(BALANCE_HASH_FILE, 'r', encoding='utf-8') as f:
				return f.read().strip()
	except Exception:
		pass
	return None


def save_balance_hash(balance_hash):
	"""保存余额hash"""
	try:
		with open(BALANCE_HASH_FILE, 'w', encoding='utf-8') as f:
			f.write(balance_hash)
	except Exception as e:
		print(f'Warning: Failed to save balance hash: {e}')


def generate_balance_hash(balances):
	"""生成余额数据的hash"""
	# 将包含 quota 和 used 的结构转换为简单的 quota 值用于 hash 计算
	simple_balances = {k: v['quota'] for k, v in balances.items()} if balances else {}
	balance_json = json.dumps(simple_balances, sort_keys=True, separators=(',', ':'))
	return hashlib.sha256(balance_json.encode('utf-8')).hexdigest()[:16]


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


async def get_waf_cookies_and_trigger_login(account_name: str, domain: str, login_url: str, required_cookies: list[str], user_session: str, api_user: str, api_user_key: str):
	"""使用 Playwright 获取 WAF cookies 并触发真实登录以完成签到"""
	print(f'[浏览器] {account_name}: 正在启动浏览器并模拟登录...')

	async with async_playwright() as p:
		import tempfile

		with tempfile.TemporaryDirectory() as temp_dir:
			context = await p.chromium.launch_persistent_context(
				user_data_dir=temp_dir,
				headless=False,
				user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
				viewport={'width': 1920, 'height': 1080},
				args=[
					'--disable-blink-features=AutomationControlled',
					'--disable-dev-shm-usage',
					'--disable-web-security',
					'--disable-features=VizDisplayCompositor',
					'--no-sandbox',
				],
			)

			# 隐藏webdriver标识
			await context.add_init_script("""
				Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
				window.navigator.chrome = {runtime: {}};
				Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
				Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
			""")

			page = await context.new_page()

			try:
				# 第一步：访问登录页面获取WAF cookies
				await page.goto(login_url, wait_until='networkidle')

				try:
					await page.wait_for_function('document.readyState === "complete"', timeout=5000)
				except Exception:
					await page.wait_for_timeout(3000)

				cookies = await page.context.cookies()

				waf_cookies = {}
				for cookie in cookies:
					cookie_name = cookie.get('name')
					cookie_value = cookie.get('value')
					if cookie_name in required_cookies and cookie_value is not None:
						waf_cookies[cookie_name] = cookie_value

				missing_cookies = [c for c in required_cookies if c not in waf_cookies]

				if missing_cookies:
					print(f'[失败] {account_name}: 缺少 WAF cookies: {missing_cookies}')
					await context.close()
					return None

				print(f'[成功] {account_name}: 已获取 {len(waf_cookies)} 个 WAF cookies')

				# 第二步：设置用户session cookie并访问关键页面触发签到
				from urllib.parse import urlparse
				parsed_domain = urlparse(domain)
				cookie_domain = parsed_domain.netloc

				# 添加用户session cookie
				await context.add_cookies([{
					'name': 'session',
					'value': user_session,
					'domain': cookie_domain,
					'path': '/',
					'httpOnly': True,
					'secure': True,
					'sameSite': 'Lax'
				}])

				# 监听网络请求
				api_calls = []
				def log_request(request):
					if '/api/' in request.url:
						api_calls.append(f"{request.method} {request.url}")
						print(f'[API请求] {request.method} {request.url}')

				page.on('request', log_request)

				# 第一步：清除所有cookies（模拟退出登录）
				print(f'[退出] {account_name}: 清除所有cookies（模拟退出）...')
				await context.clear_cookies()
				await page.wait_for_timeout(1000)

				# 第二步：重新设置session cookie（模拟重新登录）
				print(f'[登录] {account_name}: 重新设置session（模拟重新登录）...')
				await context.add_cookies([{
					'name': 'session',
					'value': user_session,
					'domain': cookie_domain,
					'path': '/',
					'httpOnly': True,
					'secure': True,
					'sameSite': 'Lax'
				}])

				# 第三步：访问首页（重新登录后，触发签到！）
				print(f'[签到] {account_name}: 访问首页触发签到...')
				await page.goto(domain, wait_until='networkidle')
				print(f'[等待] {account_name}: 在首页停留，等待签到逻辑执行（15秒）...')
				await page.wait_for_timeout(15000)

				if api_calls:
					print(f'[信息] {account_name}: 捕获到 {len(api_calls)} 个API调用')
					for call in api_calls:
						if 'user/self' in call:
							print(f'[关键] {account_name}: 检测到 /api/user/self 调用！')
				else:
					print(f'[警告] {account_name}: 未捕获到任何API调用！')

				print(f'[成功] {account_name}: 登出重登流程完成')

				await context.close()

				return waf_cookies

			except Exception as e:
				print(f'[失败] {account_name}: 浏览器操作失败: {e}')
				await context.close()
				return None


def get_user_info(client, headers, user_info_url: str):
	"""获取用户信息"""
	try:
		response = client.get(user_info_url, headers=headers, timeout=30)

		if response.status_code == 200:
			data = response.json()
			if data.get('success'):
				user_data = data.get('data', {})
				quota = round(user_data.get('quota', 0) / 500000, 2)
				used_quota = round(user_data.get('used_quota', 0) / 500000, 2)
				return {
					'success': True,
					'quota': quota,
					'used_quota': used_quota,
					'display': f':money: 当前余额: ${quota}, 已使用: ${used_quota}',
				}
		return {'success': False, 'error': f'获取用户信息失败: HTTP {response.status_code}'}
	except Exception as e:
		return {'success': False, 'error': f'获取用户信息失败: {str(e)[:50]}...'}


async def prepare_cookies(account_name: str, provider_config, user_cookies: dict, api_user: str) -> dict | None:
	"""准备请求所需的 cookies（可能包含 WAF cookies），并触发真实登录完成签到"""
	waf_cookies = {}

	if provider_config.needs_waf_cookies():
		# 获取用户session（从user_cookies中提取）
		user_session = user_cookies.get('session', '')
		if not user_session:
			print(f'[失败] {account_name}: cookies中未找到session')
			return None

		login_url = f'{provider_config.domain}{provider_config.login_path}'
		# 新函数：同时获取WAF cookies并触发真实登录
		waf_cookies = await get_waf_cookies_and_trigger_login(
			account_name,
			provider_config.domain,
			login_url,
			provider_config.waf_cookie_names,
			user_session,
			api_user,
			provider_config.api_user_key
		)
		if not waf_cookies:
			print(f'[失败] {account_name}: 无法获取 WAF cookies 或触发登录')
			return None
	else:
		print(f'[信息] {account_name}: 无需绕过 WAF，直接使用用户 cookies')

	return {**waf_cookies, **user_cookies}


def execute_check_in(client, account_name: str, provider_config, headers: dict):
	"""执行签到请求（仅用于配置了sign_in_path的自定义provider）"""
	print(f'[网络] {account_name}: 正在执行签到')

	checkin_headers = headers.copy()
	checkin_headers.update({'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

	sign_in_url = f'{provider_config.domain}{provider_config.sign_in_path}'
	response = client.post(sign_in_url, headers=checkin_headers, timeout=30)

	if response.status_code == 200:
		try:
			result = response.json()
			if result.get('ret') == 1 or result.get('code') == 0 or result.get('success'):
				msg = result.get('msg', result.get('message', ''))
				if '已签到' in msg or '已经签到' in msg:
					print(f'[信息] {account_name}: {msg}')
				else:
					print(f'[成功] {account_name}: 签到成功！{msg}')
				return True
			else:
				error_msg = result.get('msg', result.get('message', '未知错误'))
				print(f'[失败] {account_name}: 签到失败 - {error_msg}')
				return False
		except json.JSONDecodeError:
			if 'success' in response.text.lower():
				print(f'[成功] {account_name}: 签到成功！')
				return True
			else:
				print(f'[失败] {account_name}: 签到失败 - 响应格式无效')
				return False
	else:
		print(f'[失败] {account_name}: 签到失败 - HTTP {response.status_code}')
		return False


async def check_in_account(account: AccountConfig, account_index: int, app_config: AppConfig):
	"""为单个账号执行签到操作"""
	account_name = account.get_display_name(account_index)
	print(f'\n[处理中] 开始处理 {account_name}')

	provider_config = app_config.get_provider(account.provider)
	if not provider_config:
		print(f'[失败] {account_name}: 配置中未找到 Provider "{account.provider}"')
		return False, None

	print(f'[信息] {account_name}: 使用 provider "{account.provider}" ({provider_config.domain})')

	user_cookies = parse_cookies(account.cookies)
	if not user_cookies:
		print(f'[失败] {account_name}: 配置格式无效')
		return False, None

	all_cookies = await prepare_cookies(account_name, provider_config, user_cookies, account.api_user)
	if not all_cookies:
		return False, None

	client = httpx.Client(http2=True, timeout=30.0)

	try:
		client.cookies.update(all_cookies)

		headers = {
			'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
			'Accept': 'application/json, text/plain, */*',
			'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
			'Accept-Encoding': 'gzip, deflate, br, zstd',
			'Referer': f'{provider_config.domain}/console/token',  # 修改为OAuth回调页面
			'Origin': provider_config.domain,
			'Connection': 'keep-alive',
			'Sec-Fetch-Dest': 'empty',
			'Sec-Fetch-Mode': 'cors',
			'Sec-Fetch-Site': 'same-origin',
			provider_config.api_user_key: account.api_user,
		}

		user_info_url = f'{provider_config.domain}{provider_config.user_info_path}'

		# 查询签到前余额
		print(f'[信息] {account_name}: 查询签到前余额')
		user_info_before = get_user_info(client, headers, user_info_url)
		if user_info_before and user_info_before.get('success'):
			print(f'[签到前] {user_info_before["display"]}')
			balance_before = user_info_before['quota']
		elif user_info_before:
			print(user_info_before.get('error', '未知错误'))
			balance_before = None
		else:
			balance_before = None

		# 执行签到
		if provider_config.needs_manual_check_in():
			success = execute_check_in(client, account_name, provider_config, headers)

			# 签到后再次查询余额，查看是否增加
			if success:
				print(f'[信息] {account_name}: 查询签到后余额')
				user_info_after = get_user_info(client, headers, user_info_url)
				if user_info_after and user_info_after.get('success'):
					print(f'[签到后] {user_info_after["display"]}')
					balance_after = user_info_after['quota']

					# 对比前后余额
					if balance_before is not None and balance_after is not None:
						diff = round(balance_after - balance_before, 2)
						if diff > 0:
							print(f'[成功] {account_name}: 余额增加了 ${diff}')
						elif diff == 0:
							print(f'[警告] {account_name}: 余额未变化，可能今日已签到')
						else:
							print(f'[异常] {account_name}: 余额减少了 ${abs(diff)}')

					return success, user_info_after
				else:
					# 签到后查询失败，返回签到前的信息
					return success, user_info_before
			else:
				return success, user_info_before
		else:
			print(f'[信息] {account_name}: 签到已自动完成（通过用户信息请求触发）')
			return True, user_info_before

	except Exception as e:
		print(f'[失败] {account_name}: 签到过程中发生错误 - {str(e)[:50]}...')
		return False, None
	finally:
		client.close()


async def main():
	"""主函数"""
	print('[系统] AnyRouter.top 多账号自动签到脚本已启动 (使用 Playwright)')
	print(f'[时间] 执行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

	app_config = AppConfig.load_from_env()
	print(f'[信息] 已加载 {len(app_config.providers)} 个 provider 配置')

	accounts = load_accounts_config()
	if not accounts:
		print('[失败] 无法加载账号配置，程序退出')
		sys.exit(1)

	print(f'[信息] 找到 {len(accounts)} 个账号配置')

	last_balance_hash = load_balance_hash()

	success_count = 0
	total_count = len(accounts)
	notification_content = []
	current_balances = {}
	need_notify = False  # 是否需要发送通知
	balance_changed = False  # 余额是否有变化

	for i, account in enumerate(accounts):
		account_key = f'account_{i + 1}'
		try:
			success, user_info = await check_in_account(account, i, app_config)
			if success:
				success_count += 1

			should_notify_this_account = False

			if not success:
				should_notify_this_account = True
				need_notify = True
				account_name = account.get_display_name(i)
				print(f'[通知] {account_name} 失败，将发送通知')

			if user_info and user_info.get('success'):
				current_quota = user_info['quota']
				current_used = user_info['used_quota']
				current_balances[account_key] = {'quota': current_quota, 'used': current_used}

			if should_notify_this_account:
				account_name = account.get_display_name(i)
				status = '[成功]' if success else '[失败]'
				account_result = f'{status} {account_name}'
				if user_info and user_info.get('success'):
					account_result += f'\n{user_info["display"]}'
				elif user_info:
					account_result += f'\n{user_info.get("error", "未知错误")}'
				notification_content.append(account_result)

		except Exception as e:
			account_name = account.get_display_name(i)
			print(f'[失败] {account_name} 处理异常: {e}')
			need_notify = True  # 异常也需要通知
			notification_content.append(f'[失败] {account_name} 异常: {str(e)[:50]}...')

	# 检查余额变化
	current_balance_hash = generate_balance_hash(current_balances) if current_balances else None
	if current_balance_hash:
		if last_balance_hash is None:
			# 首次运行
			balance_changed = True
			need_notify = True
			print('[通知] 检测到首次运行，将发送当前余额通知')
		elif current_balance_hash != last_balance_hash:
			# 余额有变化
			balance_changed = True
			need_notify = True
			print('[通知] 检测到余额变化，将发送通知')
		else:
			print('[信息] 未检测到余额变化')

	# 为有余额变化的情况添加所有成功账号到通知内容
	if balance_changed:
		for i, account in enumerate(accounts):
			account_key = f'account_{i + 1}'
			if account_key in current_balances:
				account_name = account.get_display_name(i)
				# 只添加成功获取余额的账号，且避免重复添加
				account_result = f'[余额] {account_name}'
				account_result += f'\n:money: 当前余额: ${current_balances[account_key]["quota"]}, 已使用: ${current_balances[account_key]["used"]}'
				# 检查是否已经在通知内容中（避免重复）
				if not any(account_name in item for item in notification_content):
					notification_content.append(account_result)

	# 保存当前余额hash
	if current_balance_hash:
		save_balance_hash(current_balance_hash)

	if need_notify and notification_content:
		# 构建通知内容
		summary = [
			'[统计] 签到结果统计:',
			f'[成功] 成功: {success_count}/{total_count}',
			f'[失败] 失败: {total_count - success_count}/{total_count}',
		]

		if success_count == total_count:
			summary.append('[成功] 所有账号签到成功！')
		elif success_count > 0:
			summary.append('[警告] 部分账号签到成功')
		else:
			summary.append('[错误] 所有账号签到失败')

		time_info = f'[时间] 执行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'

		notify_content = '\n\n'.join([time_info, '\n'.join(notification_content), '\n'.join(summary)])

		print(notify_content)
		notify.push_message('AnyRouter 签到提醒', notify_content, msg_type='text')
		print('[通知] 由于失败或余额变化，已发送通知')
	else:
		print('[信息] 所有账号成功且无余额变化，跳过通知')

	# 设置退出码
	sys.exit(0 if success_count > 0 else 1)


def run_main():
	"""运行主函数的包装函数"""
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print('\n[警告] 程序被用户中断')
		sys.exit(1)
	except Exception as e:
		print(f'\n[失败] 程序执行时发生错误: {e}')
		sys.exit(1)


if __name__ == '__main__':
	run_main()
