#!/usr/bin/env python3
"""
公益站自动签到脚本

支持 AnyRouter、AgentRouter 等基于 NewAPI/OneAPI 的平台。
基于余额变化判断签到是否成功，并记录到数据库。
"""

import asyncio
import json
import sys
from datetime import datetime

import httpx
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from utils.config import AccountConfig, AppConfig, load_accounts_config
from utils.notify import notify
from utils.result import (
	SigninRecord,
	SigninResult,
	SigninStatus,
	analyze_balance_change,
	generate_balance_hash,
	is_in_cooldown,
	load_balance_hash,
	load_signin_history_with_db,
	save_all_signins_to_db,
	save_balance_hash,
	save_signin_history,
	update_signin_history,
)

load_dotenv()


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


async def get_waf_cookies_with_playwright(account_name: str, login_url: str, required_cookies: list[str]):
	"""使用 Playwright 获取 WAF cookies（使用可见浏览器窗口绕过Cloudflare检测）"""
	print(f'[处理中] {account_name}: 启动浏览器获取 WAF cookies...')

	async with async_playwright() as p:
		import tempfile

		with tempfile.TemporaryDirectory() as temp_dir:
			# 使用Chromium，非无头模式（Cloudflare检测）
			context = await p.chromium.launch_persistent_context(
				user_data_dir=temp_dir,
				headless=False,  # 必须是False，Cloudflare会检测无头浏览器
				user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
				viewport={'width': 1920, 'height': 1080},
				args=[
					'--disable-blink-features=AutomationControlled',
					'--disable-dev-shm-usage',
					'--disable-web-security',
					'--no-sandbox',
				],
			)

			page = await context.new_page()

			try:
				print(f'[处理中] {account_name}: 访问登录页获取 WAF cookies...')

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

				print(f'[信息] {account_name}: 获取到 {len(waf_cookies)}/{len(required_cookies)} 个 WAF cookies')

				missing_cookies = [c for c in required_cookies if c not in waf_cookies]

				if missing_cookies:
					print(f'[失败] {account_name}: 缺少 WAF cookies: {missing_cookies}')
					await context.close()
					return None

				print(f'[成功] {account_name}: 成功获取所有 WAF cookies')

				await context.close()

				return waf_cookies

			except Exception as e:
				print(f'[失败] {account_name}: 获取 WAF cookies 时发生错误: {e}')
				if context:
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
					'display': f'当前余额: ${quota}, 已用: ${used_quota}',
				}
		return {'success': False, 'error': f'获取用户信息失败: HTTP {response.status_code}'}
	except Exception as e:
		return {'success': False, 'error': f'获取用户信息失败: {str(e)[:50]}...'}


async def prepare_cookies(account_name: str, provider_config, user_cookies: dict) -> dict | None:
	"""准备请求所需的 cookies（纯HTTP模式，跳过WAF）"""
	print(f'[信息] {account_name}: 使用纯 HTTP 模式，直接使用用户 cookies')
	return user_cookies


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
			result = response.json()
			if result.get('ret') == 1 or result.get('code') == 0 or result.get('success'):
				print(f'[成功] {account_name}: 签到成功！')
				return True
			else:
				error_msg = result.get('msg', result.get('message', '未知错误'))
				print(f'[失败] {account_name}: 签到失败 - {error_msg}')
				return False
		except json.JSONDecodeError:
			# 如果不是 JSON 响应，检查是否包含成功标识
			if 'success' in response.text.lower():
				print(f'[成功] {account_name}: 签到成功！')
				return True
			else:
				print(f'[失败] {account_name}: 签到失败 - 响应格式无效')
				return False
	else:
		print(f'[失败] {account_name}: 签到失败 - HTTP {response.status_code}')
		return False


async def check_in_account(
	account: AccountConfig,
	account_index: int,
	app_config: AppConfig,
	signin_history: dict[str, SigninRecord]
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
	account_key = f'{account.provider}_{account.api_user}'

	print(f'\n[处理中] 开始处理 {account_name}')

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

	user_cookies = parse_cookies(account.cookies)
	if not user_cookies:
		print(f'[失败] {account_name}: 配置格式无效')
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			error='配置格式无效',
		)

	all_cookies = await prepare_cookies(account_name, provider_config, user_cookies)
	if not all_cookies:
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			error='无法获取 WAF cookies',
		)

	client = httpx.Client(timeout=30.0)

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
		balance_before = user_info_before.get('quota') if user_info_before and user_info_before.get('success') else last_balance

		if user_info_before and user_info_before.get('success'):
			print(f'[签到前] {user_info_before["display"]}')
		elif user_info_before:
			print(f'[警告] {user_info_before.get("error", "未知错误")}')

		# 执行签到
		if provider_config.needs_manual_check_in():
			api_success = execute_check_in(client, account_name, provider_config, headers)
		else:
			print(f'[信息] {account_name}: 签到已自动完成（通过用户信息请求触发）')
			api_success = True

		# 签到后获取余额
		user_info_after = get_user_info(client, headers, user_info_url)
		balance_after = user_info_after.get('quota') if user_info_after and user_info_after.get('success') else None

		if user_info_after and user_info_after.get('success'):
			print(f'[签到后] {user_info_after["display"]}')

		# 基于余额变化分析签到结果
		if balance_after is not None:
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
			status = SigninStatus.SUCCESS if api_success else SigninStatus.FAILED
			balance_diff = None
			if api_success:
				print(f'[成功] {account_name}: API 返回签到成功（无法验证余额）')
			else:
				print(f'[失败] {account_name}: 签到失败')

		# 构建用户信息
		from utils.result import UserBalance
		user_balance = None
		if user_info_after and user_info_after.get('success'):
			user_balance = UserBalance(
				quota=user_info_after['quota'],
				used_quota=user_info_after['used_quota']
			)

		# 创建签到记录（用于更新历史）
		new_record = SigninRecord(time=datetime.now(), balance=balance_after)

		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=status,
			balance_before=balance_before or last_balance,
			balance_after=balance_after,
			balance_diff=balance_diff,
			user_info=user_balance,
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
			results.append(SigninResult(
				account_key=account_key,
				account_name=account_name,
				status=SigninStatus.ERROR,
				error=str(e)[:100],
			))

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
	current_balances = {r.account_key: r.balance_after for r in results if r.balance_after is not None}
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
	need_notify = failed_count > 0 or balance_changed or any(
		r.status in (SigninStatus.SUCCESS, SigninStatus.SKIPPED) for r in results
	)

	if need_notify:
		# 构建通知内容
		notification_lines = []

		for result in results:
			if result.status == SigninStatus.SKIPPED:
				from utils.result import format_time_remaining, get_next_signin_time, get_today_total_gain

				last_signin_time = (
					result.last_signin.strftime('%Y-%m-%d %H:%M:%S')
					if result.last_signin else '未知'
				)
				remaining = format_time_remaining(get_next_signin_time(result.last_signin))
				balance_value = result.balance_after if result.balance_after is not None else result.balance_before
				balance = f'{balance_value}' if balance_value is not None else '未知'
				# 获取今日累计收益和首次签到时间
				from utils.result import get_current_cycle_first_signin_time
				today_gain_value = get_today_total_gain(result.account_key)
				first_signin_time = get_current_cycle_first_signin_time(result.account_key)
				today_gain = f'{today_gain_value}'

				# 构建通知文本
				gain_text = f'(+${today_gain}'
				if first_signin_time:
					time_str = first_signin_time.strftime('%Y/%m/%d %H:%M')
					gain_text += f'，签到成功时间 {time_str}'
				gain_text += ')'

				line = (
					f'[冷却中] {result.account_name} | 上次签到: {last_signin_time} | '
					f'剩余: {remaining} | 余额: ${balance}{gain_text}'
				)
				notification_lines.append(line)
				continue

			status_icon = {
				SigninStatus.SUCCESS: '[成功]',
				SigninStatus.FIRST_RUN: '[首次]',
				SigninStatus.COOLDOWN: '[冷却]',
				SigninStatus.FAILED: '[失败]',
				SigninStatus.ERROR: '[错误]',
			}.get(result.status, '[未知]')

			line = f'{status_icon} {result.account_name}'

			if result.user_info:
				line += f'\n   余额: ${result.user_info.quota}'
				# 获取今日累计收益和首次签到时间
				from utils.result import get_current_cycle_first_signin_time, get_today_total_gain

				today_gain_value = get_today_total_gain(result.account_key)
				first_signin_time = get_current_cycle_first_signin_time(result.account_key)

				if today_gain_value > 0:
					gain_text = f' (+${today_gain_value}'
					# 如果有首次签到时间，添加到括号中
					if first_signin_time:
						time_str = first_signin_time.strftime('%Y/%m/%d %H:%M')
						gain_text += f'，签到成功时间 {time_str}'
					gain_text += ')'
					line += gain_text
				elif result.balance_diff is not None and result.balance_diff != 0:
					# 如果没有今日累计记录，显示单次变化
					sign = '+' if result.balance_diff > 0 else ''
					line += f' ({sign}${result.balance_diff})'

			if result.error:
				line += f'\n   错误: {result.error[:50]}'

			notification_lines.append(line)

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
