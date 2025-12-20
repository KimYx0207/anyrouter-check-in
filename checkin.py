#!/usr/bin/env python3
"""AnyRouter.top 自动签到脚本

重构版本：
- 使用常量替代魔法数字
- 函数职责单一，不超过 50 行
- 状态管理无副作用
- 统一错误处理
- 统一异步模型（httpx.AsyncClient）
- HTTP 重试机制（tenacity）
"""

import asyncio
import sys
from datetime import datetime

import httpx
from dotenv import load_dotenv
from tenacity import (
	retry,
	retry_if_exception_type,
	stop_after_attempt,
	wait_exponential,
)

from utils.browser import clear_waf_cookie_cache, get_cached_waf_cookies, get_waf_cookies_and_trigger_signin, perform_real_login_signin, perform_oauth_signin_with_chrome, trigger_signin_via_http
from utils.config import AccountConfig, AppConfig, ProviderConfig, load_accounts_config
from utils.constants import (
	CHROME_USER_AGENT,
	HTTP_TIMEOUT_SECONDS,
	LOG_FILE,
	MAX_CONCURRENT_ACCOUNTS,
	QUOTA_DIVISOR,
)
from utils.result import (
	SigninRecord,
	SigninResult,
	SigninStatus,
	SigninSummary,
	UserBalance,
	analyze_balance_change,
	format_time_remaining,
	generate_balance_hash,
	get_next_signin_time,
	is_in_cooldown,
	load_balance_hash,
	load_signin_history,
	save_balance_hash,
	save_signin_history,
	update_signin_history,
)

# 先加载环境变量，再导入 notify 模块
load_dotenv()
from utils.notify import notify

# ============ 日志管理 ============


class Logger:
	"""日志管理器，同时输出到控制台和文件"""

	def __init__(self, log_file: str):
		self.log_file = log_file
		self.terminal = sys.stdout

	def write(self, message: str) -> None:
		"""写入消息到控制台和文件"""
		self.terminal.write(message)
		try:
			with open(self.log_file, 'a', encoding='utf-8') as f:
				f.write(message)
		except IOError as e:
			# 日志写入失败时输出到终端（而非静默忽略）
			self.terminal.write(f'\n[日志错误] 写入日志文件失败: {e}\n')

	def flush(self) -> None:
		"""刷新输出"""
		self.terminal.flush()


def setup_logging() -> None:
	"""设置日志输出"""
	if '--manual' not in sys.argv:
		sys.stdout = Logger(LOG_FILE)
		sys.stderr = sys.stdout


# ============ Cookie 处理 ============


def parse_cookies(cookies_data: dict | str) -> dict[str, str]:
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


# ============ API 请求 ============

# 重试装饰器：网络错误时自动重试，指数退避
_http_retry = retry(
	retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
	stop=stop_after_attempt(3),
	wait=wait_exponential(multiplier=1, min=2, max=10),
	reraise=True,
)


def build_request_headers(provider: ProviderConfig, api_user: str) -> dict[str, str]:
	"""构建请求头"""
	return {
		'User-Agent': CHROME_USER_AGENT,
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
		'Accept-Encoding': 'gzip, deflate, br, zstd',
		'Referer': f'{provider.domain}/console/token',
		'Origin': provider.domain,
		'Connection': 'keep-alive',
		'Sec-Fetch-Dest': 'empty',
		'Sec-Fetch-Mode': 'cors',
		'Sec-Fetch-Site': 'same-origin',
		provider.api_user_key: api_user,
	}


@_http_retry
async def fetch_user_info(
	client: httpx.AsyncClient,
	headers: dict,
	user_info_url: str
) -> UserBalance | None:
	"""获取用户信息（异步，带重试）"""
	try:
		response = await client.get(user_info_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)

		if response.status_code == 200:
			data = response.json()
			if data.get('success'):
				user_data = data.get('data', {})
				quota = round(user_data.get('quota', 0) / QUOTA_DIVISOR, 2)
				used_quota = round(user_data.get('used_quota', 0) / QUOTA_DIVISOR, 2)
				return UserBalance(quota=quota, used_quota=used_quota)

		print(f'[错误] 获取用户信息失败: HTTP {response.status_code}')
		return None
	except (httpx.ConnectError, httpx.TimeoutException):
		raise  # 让 tenacity 处理重试
	except Exception as e:
		print(f'[错误] 获取用户信息异常: {str(e)[:50]}')
		return None


@_http_retry
async def execute_manual_signin(
	client: httpx.AsyncClient,
	account_name: str,
	provider: ProviderConfig,
	headers: dict
) -> bool:
	"""执行手动签到请求（异步，带重试）"""
	print(f'[网络] {account_name}: 正在执行签到')

	checkin_headers = headers.copy()
	checkin_headers.update({
		'Content-Type': 'application/json',
		'X-Requested-With': 'XMLHttpRequest'
	})

	sign_in_url = f'{provider.domain}{provider.sign_in_path}'

	try:
		response = await client.post(sign_in_url, headers=checkin_headers, timeout=HTTP_TIMEOUT_SECONDS)

		if response.status_code != 200:
			print(f'[失败] {account_name}: 签到失败 - HTTP {response.status_code}')
			return False

		result = response.json()
		print(f'[调试] {account_name}: 签到API响应: {result}')

		# 检查各种成功标识
		if result.get('ret') == 1 or result.get('code') == 0 or result.get('success'):
			msg = result.get('msg', result.get('message', ''))
			if '已签到' in msg or '已经签到' in msg:
				print(f'[信息] {account_name}: {msg}')
			else:
				print(f'[成功] {account_name}: 签到成功！{msg}')
			return True

		error_msg = result.get('msg', result.get('message', '未知错误'))
		print(f'[失败] {account_name}: 签到失败 - {error_msg}')
		return False

	except (httpx.ConnectError, httpx.TimeoutException):
		raise  # 让 tenacity 处理重试
	except Exception as e:
		print(f'[失败] {account_name}: 签到请求异常 - {str(e)[:50]}')
		return False


# ============ 签到核心逻辑 ============


async def prepare_cookies_with_waf(
	account_name: str,
	provider: ProviderConfig,
	user_cookies: dict[str, str],
	api_user: str
) -> dict[str, str] | None:
	"""准备请求所需的 cookies（包含 WAF cookies）

	优化策略：
	- 对于手动签到的 provider（通过 API 签到），使用缓存的 WAF cookies
	- 对于自动签到的 provider（访问首页触发），每次都启动浏览器
	"""
	if not provider.needs_waf_cookies():
		print(f'[信息] {account_name}: 无需绕过 WAF，直接使用用户 cookies')
		return user_cookies

	# 获取用户 session
	user_session = user_cookies.get('session', '')
	if not user_session:
		print(f'[失败] {account_name}: cookies 中未找到 session')
		return None

	login_url = f'{provider.domain}{provider.login_path}'

	# 如果是手动签到（通过 API），可以使用缓存的 WAF cookies
	if provider.needs_manual_check_in():
		waf_cookies = await get_cached_waf_cookies(
			domain=provider.domain,
			login_url=login_url,
			required_cookies=provider.waf_cookie_names or [],
			log_fn=print
		)
		if waf_cookies:
			return {**waf_cookies, **user_cookies}
		print(f'[失败] {account_name}: 无法获取 WAF cookies')
		return None

	# 自动签到需要每个账号都启动浏览器访问首页
	result = await get_waf_cookies_and_trigger_signin(
		account_name=account_name,
		domain=provider.domain,
		login_url=login_url,
		required_cookies=provider.waf_cookie_names or [],
		user_session=user_session,
		api_user=api_user,
		api_user_key=provider.api_user_key,
		log_fn=print
	)

	if not result.success:
		print(f'[失败] {account_name}: 无法获取 WAF cookies - {result.error}')
		return None

	# 合并 WAF cookies 和用户 cookies
	return {**result.waf_cookies, **user_cookies}


def create_signin_result(
	account_key: str,
	account_name: str,
	current_balance: float | None,
	last_balance: float | None,
	last_signin: datetime | None,
	user_info: UserBalance | None,
	api_success: bool = True
) -> SigninResult:
	"""创建签到结果（无副作用）"""
	if not api_success:
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			user_info=user_info,
			error='API 请求失败'
		)

	if current_balance is None:
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			user_info=user_info,
			error='无法获取余额信息'
		)

	# 分析余额变化
	status, diff = analyze_balance_change(current_balance, last_balance, last_signin)

	# 创建新的签到记录
	new_record = None
	if status in (SigninStatus.SUCCESS, SigninStatus.FIRST_RUN):
		new_record = SigninRecord(time=datetime.now(), balance=current_balance)

	return SigninResult(
		account_key=account_key,
		account_name=account_name,
		status=status,
		balance_before=last_balance,
		balance_after=current_balance,
		balance_diff=diff,
		user_info=user_info,
		new_record=new_record
	)


async def process_single_account(
	account: AccountConfig,
	index: int,
	app_config: AppConfig,
	signin_history: dict[str, SigninRecord]
) -> SigninResult:
	"""处理单个账号的签到（无副作用）"""
	account_name = account.get_display_name(index)
	account_key = f'{account.provider}_{account.api_user}'

	print(f'\n[处理中] 开始处理 {account_name}')

	# 获取历史记录
	last_record = signin_history.get(account_key)
	last_signin = last_record.time if last_record else None
	last_balance = last_record.balance if last_record else None

	# 检查冷却期
	if last_signin:
		if is_in_cooldown(last_signin):
			next_signin = get_next_signin_time(last_signin)
			remaining = format_time_remaining(next_signin)
			print(f'[已跳过] {account_name} | 上次签到时间: {last_signin.strftime("%Y-%m-%d %H:%M:%S")} | 剩余冷却: {remaining}')
			return SigninResult(
				account_key=account_key,
				account_name=account_name,
				status=SigninStatus.SKIPPED,
				balance_before=last_balance
			)
		print(f'[可签到] {account_name}: 冷却期已过')
	else:
		print(f'[首次] {account_name}: 首次签到')

	# 获取 provider 配置
	provider = app_config.get_provider(account.provider)
	if not provider:
		print(f'[失败] {account_name}: 配置中未找到 Provider "{account.provider}"')
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			error=f'未知 Provider: {account.provider}'
		)

	print(f'[信息] {account_name}: 使用 provider "{account.provider}" ({provider.domain})')

	# 解析 cookies
	user_cookies = parse_cookies(account.cookies)
	if not user_cookies:
		print(f'[失败] {account_name}: cookies 配置格式无效')
		return SigninResult(
			account_key=account_key,
			account_name=account_name,
			status=SigninStatus.ERROR,
			error='cookies 格式无效'
		)

	# 判断签到方式
	needs_oauth_login = not provider.needs_manual_check_in() and account.has_oauth_config()
	needs_real_login = not provider.needs_manual_check_in() and account.has_login_credentials()

	if needs_oauth_login:
		# 使用 HTTP 请求触发签到（优先，适用于 GitHub Actions）
		print(f'[信息] {account_name}: 使用 HTTP 请求触发签到（OAuth 账号）')
		login_url = f'{provider.domain}{provider.login_path}'

		result = await trigger_signin_via_http(
			account_name=account_name,
			domain=provider.domain,
			login_url=login_url,
			user_cookies=user_cookies,
			api_user=account.api_user,
			api_user_key=provider.api_user_key,
			log_fn=print
		)

		if result.success:
			# HTTP 签到成功
			all_cookies = user_cookies
		else:
			# HTTP 失败，尝试浏览器方式（仅本地开发）
			print(f'[回退] {account_name}: HTTP 签到失败，尝试浏览器方式...')
			result = await perform_oauth_signin_with_chrome(
				account_name=account_name,
				domain=provider.domain,
				login_url=login_url,
				oauth_provider=account.oauth_provider,
				log_fn=print
			)

			if not result.success:
				return SigninResult(
					account_key=account_key,
					account_name=account_name,
					status=SigninStatus.ERROR,
					error=result.error or 'OAuth 登录失败'
				)

			all_cookies = user_cookies

	elif needs_real_login:
		# 使用真正的登录流程触发签到（用户名密码）
		print(f'[信息] {account_name}: 使用用户名密码登录触发签到')
		login_url = f'{provider.domain}{provider.login_path}'

		result = await perform_real_login_signin(
			account_name=account_name,
			domain=provider.domain,
			login_url=login_url,
			username=account.username,
			password=account.password,
			required_cookies=provider.waf_cookie_names or [],
			log_fn=print
		)

		if not result.success:
			return SigninResult(
				account_key=account_key,
				account_name=account_name,
				status=SigninStatus.ERROR,
				error=result.error or '登录失败'
			)

		# 登录成功后，使用获取的 cookies 查询余额
		all_cookies = {**result.waf_cookies, **user_cookies}
	else:
		# 准备 cookies（获取 WAF cookies）
		all_cookies = await prepare_cookies_with_waf(account_name, provider, user_cookies, account.api_user)
		if not all_cookies:
			return SigninResult(
				account_key=account_key,
				account_name=account_name,
				status=SigninStatus.ERROR,
				error='无法获取 WAF cookies'
			)

	# 使用异步 HTTP 客户端（context manager 自动关闭）
	async with httpx.AsyncClient(http2=True, timeout=HTTP_TIMEOUT_SECONDS) as client:
		client.cookies.update(all_cookies)
		headers = build_request_headers(provider, account.api_user)
		user_info_url = f'{provider.domain}{provider.user_info_path}'

		# 查询当前余额
		print(f'[信息] {account_name}: 查询当前余额')
		user_info = await fetch_user_info(client, headers, user_info_url)

		if user_info:
			print(f'[当前] {user_info.display}')
			current_balance = user_info.quota
		else:
			current_balance = None

		# 执行签到
		api_success = True
		if provider.needs_manual_check_in():
			api_success = await execute_manual_signin(client, account_name, provider, headers)

			# 签到后再次查询余额
			if api_success:
				print(f'[信息] {account_name}: 查询签到后余额')
				user_info_after = await fetch_user_info(client, headers, user_info_url)
				if user_info_after:
					print(f'[签到后] {user_info_after.display}')
					current_balance = user_info_after.quota
					user_info = user_info_after
		elif needs_oauth_login:
			# OAuth 登录已经在上面完成，签到已触发
			print(f'[信息] {account_name}: 签到已通过 HTTP/浏览器触发完成')
		elif needs_real_login:
			# 真正登录已经在上面完成，签到已触发
			print(f'[信息] {account_name}: 签到已通过用户名密码登录触发完成')
		else:
			# 没有登录凭据，通过浏览器模拟登录触发签到（已在 prepare_cookies_with_waf 中完成）
			print(f'[信息] {account_name}: 签到已通过浏览器模拟登录触发')

		# 创建签到结果
		result = create_signin_result(
			account_key=account_key,
			account_name=account_name,
			current_balance=current_balance,
			last_balance=last_balance,
			last_signin=last_signin,
			user_info=user_info,
			api_success=api_success
		)

		# 输出结果
		now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		if result.status == SigninStatus.SUCCESS:
			print(f'[签到成功] {account_name} | 余额: ${result.balance_before} → ${result.balance_after} (+${result.balance_diff}) | 时间: {now_str}')
		elif result.status == SigninStatus.FIRST_RUN:
			print(f'[首次运行] {account_name} | 当前余额: ${current_balance} | 时间: {now_str}')
		elif result.status == SigninStatus.COOLDOWN:
			print(f'[已签到] {account_name}: 余额未变化（今日已签到）')
		elif result.status == SigninStatus.FAILED:
			print(f'[签到失败] {account_name}: 余额未变化或减少')

		return result


# ============ 通知生成 ============


def build_notification_content(summary: SigninSummary) -> str:
	"""构建通知内容"""
	lines = []

	# 时间信息
	lines.append(f'[时间] 执行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
	lines.append('')

	# 账号详情
	for result in summary.results:
		if result.needs_notification or summary.balance_changed:
			status_map = {
				SigninStatus.SUCCESS: '[签到成功]',
				SigninStatus.FIRST_RUN: '[首次运行]',
				SigninStatus.COOLDOWN: '[冷却期内]',
				SigninStatus.SKIPPED: '[已跳过]',
				SigninStatus.FAILED: '[签到失败]',
				SigninStatus.ERROR: '[发生错误]',
			}
			status_text = status_map.get(result.status, '[未知状态]')

			account_line = f'{status_text} {result.account_name}'
			if result.user_info:
				account_line += f'\n{result.user_info.display}'
			if result.error:
				account_line += f'\n错误: {result.error}'
			lines.append(account_line)

	lines.append('')

	# 统计信息
	lines.append('[统计] 签到结果统计:')
	lines.append(f'[执行] 总计: {summary.total} 个账号')
	lines.append(f'[签到成功] 本次签到成功: {summary.success} 个账号')
	lines.append(f'[冷却期内] 24小时内已签到: {summary.cooldown} 个账号')
	lines.append(f'[失败] 签到失败: {summary.failed} 个账号')

	if summary.success > 0:
		lines.append(f'[恭喜] 本次有 {summary.success} 个账号成功签到！')
	if summary.cooldown > 0:
		lines.append(f'[提示] 有 {summary.cooldown} 个账号在24小时冷却期内')
	if summary.failed > 0:
		lines.append(f'[警告] 有 {summary.failed} 个账号签到失败')

	return '\n'.join(lines)


# ============ 主程序 ============


async def run_checkin() -> SigninSummary:
	"""执行签到流程（核心逻辑）

	使用信号量控制并发，避免同时启动过多浏览器实例。
	"""
	print('[系统] AnyRouter.top 多账号自动签到脚本已启动')
	print(f'[时间] 执行时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

	# 加载配置
	app_config = AppConfig.load_from_env()
	print(f'[信息] 已加载 {len(app_config.providers)} 个 provider 配置')

	accounts = load_accounts_config()
	if not accounts:
		print('[失败] 无法加载账号配置，程序退出')
		sys.exit(1)

	print(f'[信息] 找到 {len(accounts)} 个账号配置')

	# 加载历史记录
	signin_history = load_signin_history()
	print(f'[信息] 已加载签到历史（{len(signin_history)} 条记录）')

	last_balance_hash = load_balance_hash()

	# 清除 WAF Cookie 缓存（每次运行重新获取）
	clear_waf_cookie_cache()

	# 创建信号量控制并发
	semaphore = asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)

	async def process_with_semaphore(account: AccountConfig, index: int) -> SigninResult:
		"""带信号量的账号处理"""
		async with semaphore:
			try:
				return await process_single_account(account, index, app_config, signin_history)
			except Exception as e:
				print(f'[失败] {account.get_display_name(index)} 处理异常: {e}')
				return SigninResult(
					account_key=f'{account.provider}_{account.api_user}',
					account_name=account.get_display_name(index),
					status=SigninStatus.ERROR,
					error=str(e)[:100]
				)

	# 并行处理所有账号
	print(f'[信息] 开始并行处理（最大并发: {MAX_CONCURRENT_ACCOUNTS}）')
	tasks = [process_with_semaphore(account, i) for i, account in enumerate(accounts)]
	results = await asyncio.gather(*tasks)

	# 汇总结果
	summary = SigninSummary()
	current_balances: dict[str, float] = {}

	for i, result in enumerate(results):
		summary.add_result(result)
		if result.user_info:
			current_balances[f'account_{i + 1}'] = result.user_info.quota

	# 检查余额变化
	current_balance_hash = generate_balance_hash(current_balances) if current_balances else ''

	if current_balance_hash:
		if last_balance_hash is None:
			summary.is_first_run = True
			print('[通知] 检测到首次运行，将发送当前余额通知')
		elif current_balance_hash != last_balance_hash:
			summary.balance_changed = True
			print('[通知] 检测到余额变化，将发送通知')
		else:
			print('[信息] 未检测到余额变化')

	# 更新签到历史（返回新字典）
	new_history = update_signin_history(signin_history, summary.results)
	save_signin_history(new_history)
	print(f'[信息] 已保存签到历史（{len(new_history)} 条记录）')

	# 保存余额 hash
	if current_balance_hash:
		save_balance_hash(current_balance_hash)

	return summary


async def main() -> None:
	"""主函数"""
	# 设置日志
	setup_logging()

	# 如果是定时任务模式，添加日志分隔符
	if '--manual' not in sys.argv:
		print('\n' + '=' * 60)
		print('新一轮签到开始')
		print('=' * 60)

	# 执行签到
	summary = await run_checkin()

	# 发送通知
	if summary.needs_notification:
		content = build_notification_content(summary)
		print(content)
		notify.push_message('AnyRouter 签到提醒', content, msg_type='text')
		print('[通知] 已发送通知')
	else:
		print('[信息] 所有账号成功且无余额变化，跳过通知')

	# 设置退出码
	sys.exit(0 if summary.failed == 0 else 1)


def run_main() -> None:
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
