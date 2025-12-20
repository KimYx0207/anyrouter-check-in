#!/usr/bin/env python3
"""浏览器自动化模块 - 封装 Playwright 操作

职责：
1. WAF Cookie 获取（带缓存）
2. 模拟登录流程
3. 触发自动签到
4. OAuth 自动登录（使用 Chrome 已登录状态）
5. HTTP 签到（无浏览器依赖，适用于 GitHub Actions）
"""

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx
from playwright.async_api import BrowserContext, Page, async_playwright

from utils.constants import (
	BROWSER_ARGS,
	CHROME_USER_AGENT,
	COOKIE_SET_WAIT_MS,
	HTTP_TIMEOUT_SECONDS,
	PAGE_LOAD_WAIT_MS,
	SIGNIN_TRIGGER_WAIT_MS,
)


@dataclass
class BrowserResult:
	"""浏览器操作结果"""

	success: bool
	waf_cookies: dict[str, str]
	api_calls: list[str]
	error: str | None = None


# WAF Cookie 缓存（按域名缓存）
_waf_cookie_cache: dict[str, dict[str, str]] = {}
_cache_lock = asyncio.Lock()


# Stealth 脚本：隐藏自动化特征
STEALTH_SCRIPT = """
// 隐藏 webdriver 标识
Object.defineProperty(navigator, 'webdriver', {
	get: () => undefined,
	configurable: true
});

// 模拟 Chrome 运行时
window.navigator.chrome = {
	runtime: {},
	loadTimes: function() {},
	csi: function() {},
	app: {}
};

// 模拟插件
Object.defineProperty(navigator, 'plugins', {
	get: () => {
		const plugins = [
			{name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
			{name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
			{name: 'Native Client', filename: 'internal-nacl-plugin'}
		];
		plugins.item = (index) => plugins[index];
		plugins.namedItem = (name) => plugins.find(p => p.name === name);
		plugins.refresh = () => {};
		return plugins;
	},
	configurable: true
});

// 模拟语言
Object.defineProperty(navigator, 'languages', {
	get: () => ['zh-CN', 'zh', 'en-US', 'en'],
	configurable: true
});

// 隐藏自动化权限查询
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
	parameters.name === 'notifications' ?
		Promise.resolve({ state: Notification.permission }) :
		originalQuery(parameters)
);

// 模拟正常的 WebGL 渲染器
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
	if (parameter === 37445) return 'Intel Inc.';
	if (parameter === 37446) return 'Intel Iris OpenGL Engine';
	return getParameter.apply(this, arguments);
};
"""


async def _create_stealth_context(playwright) -> tuple[BrowserContext, str]:
	"""创建带有 stealth 配置的浏览器上下文

	Returns:
	    (browser_context, temp_dir_path)
	"""
	temp_dir = tempfile.mkdtemp()

	context = await playwright.chromium.launch_persistent_context(
		user_data_dir=temp_dir,
		headless=True,  # 使用 headless 模式，配合 stealth 脚本
		user_agent=CHROME_USER_AGENT,
		viewport={'width': 1920, 'height': 1080},
		args=BROWSER_ARGS,
		ignore_https_errors=True,
		java_script_enabled=True,
		bypass_csp=True,
	)

	# 注入 stealth 脚本
	await context.add_init_script(STEALTH_SCRIPT)

	return context, temp_dir


async def _get_waf_cookies(page: Page, required_cookies: list[str]) -> dict[str, str]:
	"""从页面获取 WAF cookies"""
	cookies = await page.context.cookies()
	waf_cookies = {}

	for cookie in cookies:
		cookie_name = cookie.get('name')
		cookie_value = cookie.get('value')
		if cookie_name in required_cookies and cookie_value is not None:
			waf_cookies[cookie_name] = cookie_value

	return waf_cookies


async def _set_session_cookie(context: BrowserContext, domain: str, session_value: str) -> None:
	"""设置用户 session cookie"""
	parsed = urlparse(domain)
	cookie_domain = parsed.netloc

	await context.add_cookies([{
		'name': 'session',
		'value': session_value,
		'domain': cookie_domain,
		'path': '/',
		'httpOnly': True,
		'secure': True,
		'sameSite': 'Lax'
	}])


async def _wait_for_page_load(page: Page) -> None:
	"""等待页面加载完成"""
	try:
		await page.wait_for_function('document.readyState === "complete"', timeout=PAGE_LOAD_WAIT_MS)
	except Exception:
		await page.wait_for_timeout(PAGE_LOAD_WAIT_MS)


def _create_api_logger(api_calls: list[str], log_fn: Callable[[str], None] | None = None):
	"""创建 API 请求记录器"""

	def log_request(request):
		if '/api/' in request.url:
			call_info = f'{request.method} {request.url}'
			api_calls.append(call_info)
			if log_fn:
				log_fn(f'[API请求] {call_info}')

	return log_request


async def _get_waf_cookies_from_browser(
	domain: str,
	login_url: str,
	required_cookies: list[str],
	log_fn: Callable[[str], None] | None = None
) -> dict[str, str]:
	"""从浏览器获取 WAF cookies（内部函数）"""
	async with async_playwright() as p:
		context = None
		temp_dir = None
		try:
			context, temp_dir = await _create_stealth_context(p)
			page = await context.new_page()

			await page.goto(login_url, wait_until='networkidle')
			await _wait_for_page_load(page)

			waf_cookies = await _get_waf_cookies(page, required_cookies)
			return waf_cookies

		finally:
			if context:
				await context.close()
			if temp_dir:
				try:
					shutil.rmtree(temp_dir, ignore_errors=True)
				except Exception:
					pass


async def get_cached_waf_cookies(
	domain: str,
	login_url: str,
	required_cookies: list[str],
	log_fn: Callable[[str], None] | None = None
) -> dict[str, str] | None:
	"""获取 WAF cookies（带缓存，同一域名只获取一次）"""
	async with _cache_lock:
		# 检查缓存
		if domain in _waf_cookie_cache:
			cached = _waf_cookie_cache[domain]
			# 验证缓存是否包含所有需要的 cookies
			if all(c in cached for c in required_cookies):
				if log_fn:
					log_fn(f'[缓存] 使用已缓存的 WAF cookies ({domain})')
				return cached

		# 缓存未命中，从浏览器获取
		if log_fn:
			log_fn(f'[浏览器] 正在获取 WAF cookies ({domain})...')

		waf_cookies = await _get_waf_cookies_from_browser(domain, login_url, required_cookies, log_fn)

		if waf_cookies and all(c in waf_cookies for c in required_cookies):
			_waf_cookie_cache[domain] = waf_cookies
			if log_fn:
				log_fn(f'[缓存] WAF cookies 已缓存 ({domain})')
			return waf_cookies

		return None


def clear_waf_cookie_cache() -> None:
	"""清除 WAF Cookie 缓存"""
	_waf_cookie_cache.clear()


async def get_waf_cookies_and_trigger_signin(
	account_name: str,
	domain: str,
	login_url: str,
	required_cookies: list[str],
	user_session: str,
	api_user: str,
	api_user_key: str = 'new-api-user',
	log_fn: Callable[[str], None] | None = None
) -> BrowserResult:
	"""使用 Playwright 获取 WAF cookies 并触发签到

	Args:
	    account_name: 账号名称（用于日志）
	    domain: 目标域名
	    login_url: 登录页面 URL
	    required_cookies: 需要获取的 WAF cookie 名称列表
	    user_session: 用户 session cookie 值
	    api_user: API 用户标识
	    api_user_key: API 用户请求头名称
	    log_fn: 日志输出函数

	Returns:
	    BrowserResult: 包含成功状态、WAF cookies 和 API 调用记录
	"""

	def log(msg: str) -> None:
		if log_fn:
			log_fn(msg)
		else:
			print(msg)

	log(f'[浏览器] {account_name}: 正在启动浏览器并模拟登录...')

	async with async_playwright() as p:
		context = None
		temp_dir = None
		try:
			context, temp_dir = await _create_stealth_context(p)
			page = await context.new_page()

			# 第一步：访问登录页面获取 WAF cookies
			log(f'[浏览器] {account_name}: 访问登录页面获取 WAF cookies...')
			await page.goto(login_url, wait_until='networkidle')
			await _wait_for_page_load(page)

			waf_cookies = await _get_waf_cookies(page, required_cookies)
			missing_cookies = [c for c in required_cookies if c not in waf_cookies]

			if missing_cookies:
				log(f'[失败] {account_name}: 缺少 WAF cookies: {missing_cookies}')
				return BrowserResult(
					success=False,
					waf_cookies={},
					api_calls=[],
					error=f'缺少 WAF cookies: {missing_cookies}'
				)

			log(f'[成功] {account_name}: 已获取 {len(waf_cookies)} 个 WAF cookies')

			# 第二步：设置 API 请求监听
			api_calls: list[str] = []
			page.on('request', _create_api_logger(api_calls, log))

			# 第三步：模拟退出登录
			log(f'[退出] {account_name}: 清除所有 cookies（模拟退出）...')
			await context.clear_cookies()
			await page.wait_for_timeout(COOKIE_SET_WAIT_MS)

			# 第四步：重新设置 session（模拟重新登录）
			log(f'[登录] {account_name}: 重新设置 session（模拟重新登录）...')
			await _set_session_cookie(context, domain, user_session)

			# 第五步：访问首页触发签到（AgentRouter 在首页登录成功时触发签到）
			home_url = f'{domain}/'
			log(f'[签到] {account_name}: 访问首页触发签到 ({home_url})...')
			await page.goto(home_url, wait_until='networkidle')

			# 第六步：主动调用 /api/user/self 触发签到（AgentRouter 登录时自动签到）
			log(f'[签到] {account_name}: 主动调用 /api/user/self 触发签到...')
			try:
				# 注入 api_user 和 api_user_key 到 JavaScript
				user_self_result = await page.evaluate(f'''
					async () => {{
						try {{
							const response = await fetch('/api/user/self', {{
								method: 'GET',
								credentials: 'include',
								headers: {{
									'Accept': 'application/json',
									'Content-Type': 'application/json',
									'{api_user_key}': '{api_user}'
								}}
							}});
							const data = await response.json();
							return {{ success: response.ok, status: response.status, data: data }};
						}} catch (e) {{
							return {{ success: false, error: e.message }};
						}}
					}}
				''')
				if user_self_result.get('success'):
					log(f'[成功] {account_name}: /api/user/self 调用成功')
					api_calls.append(f'GET {domain}/api/user/self (browser)')
				else:
					log(f'[警告] {account_name}: /api/user/self 调用失败: {user_self_result}')
			except Exception as e:
				log(f'[警告] {account_name}: 浏览器内 API 调用失败: {str(e)[:50]}')

			log(f'[等待] {account_name}: 等待签到逻辑执行（{SIGNIN_TRIGGER_WAIT_MS // 1000}秒）...')
			await page.wait_for_timeout(SIGNIN_TRIGGER_WAIT_MS)

			# 输出 API 调用统计
			if api_calls:
				log(f'[信息] {account_name}: 捕获到 {len(api_calls)} 个 API 调用')
				for call in api_calls:
					if 'user/self' in call:
						log(f'[关键] {account_name}: 检测到 /api/user/self 调用')
			else:
				log(f'[警告] {account_name}: 未捕获到任何 API 调用')

			log(f'[成功] {account_name}: 登出重登流程完成')

			return BrowserResult(
				success=True,
				waf_cookies=waf_cookies,
				api_calls=api_calls
			)

		except Exception as e:
			error_msg = str(e)[:100]
			log(f'[失败] {account_name}: 浏览器操作失败: {error_msg}')
			return BrowserResult(
				success=False,
				waf_cookies={},
				api_calls=[],
				error=error_msg
			)

		finally:
			if context:
				await context.close()
			if temp_dir:
				try:
					shutil.rmtree(temp_dir, ignore_errors=True)
				except Exception:
					pass  # 清理失败不影响主流程


async def perform_real_login_signin(
	account_name: str,
	domain: str,
	login_url: str,
	username: str,
	password: str,
	required_cookies: list[str],
	log_fn: Callable[[str], None] | None = None
) -> BrowserResult:
	"""执行真正的登录流程触发签到

	适用于签到在登录时自动触发的 provider（如 AgentRouter）。

	Args:
	    account_name: 账号名称（用于日志）
	    domain: 目标域名
	    login_url: 登录页面 URL
	    username: 登录用户名
	    password: 登录密码
	    required_cookies: 需要获取的 WAF cookie 名称列表
	    log_fn: 日志输出函数

	Returns:
	    BrowserResult: 包含成功状态、WAF cookies 和 API 调用记录
	"""

	def log(msg: str) -> None:
		if log_fn:
			log_fn(msg)
		else:
			print(msg)

	log(f'[浏览器] {account_name}: 正在启动浏览器执行真正登录...')

	async with async_playwright() as p:
		context = None
		temp_dir = None
		try:
			context, temp_dir = await _create_stealth_context(p)
			page = await context.new_page()

			# 设置 API 请求监听
			api_calls: list[str] = []
			page.on('request', _create_api_logger(api_calls, log))

			# 第一步：访问登录页面
			log(f'[浏览器] {account_name}: 访问登录页面...')
			await page.goto(login_url, wait_until='networkidle')
			await _wait_for_page_load(page)

			# 获取 WAF cookies
			waf_cookies = await _get_waf_cookies(page, required_cookies)
			if waf_cookies:
				log(f'[成功] {account_name}: 已获取 {len(waf_cookies)} 个 WAF cookies')

			# 第二步：填写登录表单
			log(f'[登录] {account_name}: 填写登录表单...')

			# 等待登录表单加载
			try:
				await page.wait_for_selector('input[name="username"], input[type="text"]', timeout=10000)
			except Exception:
				log(f'[警告] {account_name}: 找不到用户名输入框，尝试继续...')

			# 尝试多种选择器找到输入框
			username_selectors = [
				'input[name="username"]',
				'input[type="text"]:first-of-type',
				'input[placeholder*="用户名"]',
				'input[placeholder*="username"]',
				'input[placeholder*="邮箱"]',
				'input[placeholder*="email"]',
			]
			password_selectors = [
				'input[name="password"]',
				'input[type="password"]',
				'input[placeholder*="密码"]',
				'input[placeholder*="password"]',
			]

			# 填写用户名
			username_filled = False
			for selector in username_selectors:
				try:
					element = await page.query_selector(selector)
					if element:
						await element.fill(username)
						username_filled = True
						log(f'[登录] {account_name}: 已填写用户名')
						break
				except Exception:
					continue

			if not username_filled:
				log(f'[失败] {account_name}: 无法找到用户名输入框')
				return BrowserResult(
					success=False,
					waf_cookies=waf_cookies,
					api_calls=api_calls,
					error='无法找到用户名输入框'
				)

			# 填写密码
			password_filled = False
			for selector in password_selectors:
				try:
					element = await page.query_selector(selector)
					if element:
						await element.fill(password)
						password_filled = True
						log(f'[登录] {account_name}: 已填写密码')
						break
				except Exception:
					continue

			if not password_filled:
				log(f'[失败] {account_name}: 无法找到密码输入框')
				return BrowserResult(
					success=False,
					waf_cookies=waf_cookies,
					api_calls=api_calls,
					error='无法找到密码输入框'
				)

			# 第三步：点击登录按钮
			log(f'[登录] {account_name}: 点击登录按钮...')
			submit_selectors = [
				'button[type="submit"]',
				'button:has-text("登录")',
				'button:has-text("登 录")',
				'button:has-text("Login")',
				'input[type="submit"]',
				'.login-button',
				'.submit-button',
			]

			login_clicked = False
			for selector in submit_selectors:
				try:
					element = await page.query_selector(selector)
					if element:
						await element.click()
						login_clicked = True
						log(f'[登录] {account_name}: 已点击登录按钮')
						break
				except Exception:
					continue

			if not login_clicked:
				# 尝试按回车提交
				log(f'[登录] {account_name}: 找不到登录按钮，尝试按回车提交...')
				await page.keyboard.press('Enter')

			# 第四步：等待登录完成和页面跳转
			log(f'[登录] {account_name}: 等待登录完成...')
			try:
				# 等待 URL 变化或页面跳转
				await page.wait_for_url('**/console**', timeout=15000)
				log(f'[成功] {account_name}: 登录成功，已跳转到控制台')
			except Exception:
				# 检查是否有错误提示
				current_url = page.url
				if 'login' in current_url.lower():
					# 还在登录页，可能登录失败
					error_text = await page.evaluate('''
						() => {
							const errorEl = document.querySelector('.error, .alert-error, .message-error, [class*="error"]');
							return errorEl ? errorEl.textContent.trim() : null;
						}
					''')
					if error_text:
						log(f'[失败] {account_name}: 登录失败 - {error_text[:50]}')
						return BrowserResult(
							success=False,
							waf_cookies=waf_cookies,
							api_calls=api_calls,
							error=f'登录失败: {error_text[:50]}'
						)
				log(f'[警告] {account_name}: 登录状态不确定，当前URL: {current_url}')

			# 第五步：等待签到逻辑执行
			log(f'[等待] {account_name}: 等待签到逻辑执行（{SIGNIN_TRIGGER_WAIT_MS // 1000}秒）...')
			await page.wait_for_timeout(SIGNIN_TRIGGER_WAIT_MS)

			# 输出 API 调用统计
			if api_calls:
				log(f'[信息] {account_name}: 捕获到 {len(api_calls)} 个 API 调用')
				for call in api_calls:
					if 'user/self' in call:
						log(f'[关键] {account_name}: 检测到 /api/user/self 调用（签到触发点）')

			log(f'[成功] {account_name}: 真正登录流程完成')

			return BrowserResult(
				success=True,
				waf_cookies=waf_cookies,
				api_calls=api_calls
			)

		except Exception as e:
			error_msg = str(e)[:100]
			log(f'[失败] {account_name}: 登录流程失败: {error_msg}')
			return BrowserResult(
				success=False,
				waf_cookies={},
				api_calls=[],
				error=error_msg
			)

		finally:
			if context:
				await context.close()
			if temp_dir:
				try:
					shutil.rmtree(temp_dir, ignore_errors=True)
				except Exception:
					pass


# Chrome 远程调试配置
CHROME_DEBUG_PORT = int(os.getenv('CHROME_DEBUG_PORT', '9022'))
CHROME_DEBUG_URL = f'http://127.0.0.1:{CHROME_DEBUG_PORT}'

# Playwright 专用配置目录（不使用 Chrome 的，避免锁定问题）
PLAYWRIGHT_USER_DATA_DIR = Path(__file__).parent.parent / 'data' / 'browser_profile'


# Chrome 用户数据目录（Windows 默认路径）
def get_chrome_user_data_dir() -> str:
	"""获取 Chrome 用户数据目录"""
	# Windows 默认路径
	default_path = os.path.expanduser('~\\AppData\\Local\\Google\\Chrome\\User Data')
	if os.path.exists(default_path):
		return default_path
	# 可以通过环境变量覆盖
	return os.getenv('CHROME_USER_DATA_DIR', default_path)


async def _check_chrome_debug_port() -> bool:
	"""检查 Chrome 远程调试端口是否可用"""
	import socket
	try:
		sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		sock.settimeout(1)
		result = sock.connect_ex(('127.0.0.1', CHROME_DEBUG_PORT))
		sock.close()
		return result == 0
	except Exception:
		return False


async def perform_oauth_signin_with_chrome(
	account_name: str,
	domain: str,
	login_url: str,
	oauth_provider: str = 'github',
	log_fn: Callable[[str], None] | None = None
) -> BrowserResult:
	"""使用 Chrome 已登录状态执行 OAuth 签到

	优先通过 CDP 远程调试端口连接已运行的 Chrome（需启动时添加 --remote-debugging-port=9022）。
	如果远程端口不可用，则尝试启动新的 Chrome 实例（需关闭已运行的 Chrome）。

	Args:
	    account_name: 账号名称（用于日志）
	    domain: 目标域名（如 https://agentrouter.org）
	    login_url: 登录页面 URL
	    oauth_provider: OAuth 提供商（github/google/linuxdo）
	    log_fn: 日志输出函数

	Returns:
	    BrowserResult: 包含成功状态和 API 调用记录
	"""

	def log(msg: str) -> None:
		if log_fn:
			log_fn(msg)
		else:
			print(msg)

	log(f'[浏览器] {account_name}: 使用 Chrome 已登录状态执行 OAuth 签到...')

	# 检查是否可以通过 CDP 连接
	cdp_available = await _check_chrome_debug_port()

	# 确保 Playwright 配置目录存在
	PLAYWRIGHT_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
	profile_exists = (PLAYWRIGHT_USER_DATA_DIR / 'Default').exists()

	async with async_playwright() as p:
		browser = None
		context = None
		page = None
		use_cdp = False

		try:
			if cdp_available:
				# 方式1：通过 CDP 连接已运行的 Chrome
				log(f'[CDP] {account_name}: 检测到远程调试端口 {CHROME_DEBUG_PORT}，通过 CDP 连接...')
				browser = await p.chromium.connect_over_cdp(CHROME_DEBUG_URL)
				contexts = browser.contexts
				if contexts:
					context = contexts[0]
					log(f'[CDP] {account_name}: 已连接到现有浏览器上下文')
				else:
					context = await browser.new_context()
					log(f'[CDP] {account_name}: 创建新的浏览器上下文')
				use_cdp = True
			else:
				# 方式2：使用 Playwright 专用配置目录（避免与 Chrome 冲突）
				log(f'[信息] {account_name}: 使用 Playwright 独立配置目录...')
				if not profile_exists:
					log(f'[首次] {account_name}: 首次运行，需要在浏览器中登录 GitHub')
					log(f'[提示] 请在打开的浏览器中完成 GitHub 登录，后续运行将自动使用此登录状态')

				context = await p.chromium.launch_persistent_context(
					user_data_dir=str(PLAYWRIGHT_USER_DATA_DIR),
					headless=False,  # OAuth 需要显示界面
					args=['--disable-blink-features=AutomationControlled'],
					ignore_https_errors=True,
				)

			page = await context.new_page()

			# 设置 API 请求监听
			api_calls: list[str] = []
			page.on('request', _create_api_logger(api_calls, log))

			# 第一步：访问登录页面
			log(f'[浏览器] {account_name}: 访问登录页面...')
			await page.goto(login_url, wait_until='networkidle')
			await _wait_for_page_load(page)

			# 检查是否已经登录（被重定向到非登录页面）
			current_url = page.url
			if '/login' not in current_url.lower():
				log(f'[成功] {account_name}: 已登录状态，被重定向到 {current_url}')
				log(f'[信息] {account_name}: 签到应该已经通过页面加载触发')
				# 等待签到逻辑执行
				await page.wait_for_timeout(SIGNIN_TRIGGER_WAIT_MS)
				return BrowserResult(
					success=True,
					waf_cookies={},
					api_calls=api_calls
				)

			# 第二步：点击 OAuth 登录按钮
			log(f'[登录] {account_name}: 点击 {oauth_provider} 登录按钮...')

			# 根据 OAuth 提供商选择按钮
			oauth_selectors = {
				'github': [
					# 文本匹配
					'button:has-text("GitHub")',
					'a:has-text("GitHub")',
					'button:has-text("使用 GitHub 登录")',
					'button:has-text("Sign in with GitHub")',
					'a:has-text("Sign in with GitHub")',
					# 类名匹配
					'[class*="github"]',
					'[class*="Github"]',
					# SVG 图标匹配
					'button:has(svg[class*="github"])',
					'a:has(svg[class*="github"])',
					# aria 标签
					'[aria-label*="GitHub"]',
					'[aria-label*="github"]',
					# 通用 OAuth 按钮
					'button[data-provider="github"]',
					'a[href*="github"]',
					'a[href*="/oauth/github"]',
				],
				'google': [
					'button:has-text("Google")',
					'a:has-text("Google")',
					'[class*="google"]',
					'[aria-label*="Google"]',
				],
				'linuxdo': [
					'button:has-text("LinuxDo")',
					'a:has-text("LinuxDo")',
					'button:has-text("LINUX DO")',
					'[class*="linuxdo"]',
				],
			}

			selectors = oauth_selectors.get(oauth_provider, oauth_selectors['github'])
			clicked = False

			for selector in selectors:
				try:
					element = await page.query_selector(selector)
					if element:
						await element.click()
						clicked = True
						log(f'[登录] {account_name}: 已点击登录按钮')
						break
				except Exception:
					continue

			if not clicked:
				log(f'[失败] {account_name}: 找不到 {oauth_provider} 登录按钮')
				return BrowserResult(
					success=False,
					waf_cookies={},
					api_calls=api_calls,
					error=f'找不到 {oauth_provider} 登录按钮'
				)

			# 第三步：等待 OAuth 流程完成
			log(f'[登录] {account_name}: 等待 OAuth 授权完成...')

			try:
				# 等待跳转回目标域名（OAuth 完成）
				await page.wait_for_url(f'{domain}/**', timeout=30000)
				log(f'[成功] {account_name}: OAuth 授权完成，已返回 {domain}')
			except Exception:
				# 可能需要用户手动授权
				current_url = page.url
				if oauth_provider in current_url.lower() or 'github.com' in current_url or 'google.com' in current_url:
					log(f'[等待] {account_name}: 请在浏览器中完成授权（30秒超时）...')
					try:
						await page.wait_for_url(f'{domain}/**', timeout=30000)
						log(f'[成功] {account_name}: OAuth 授权完成')
					except Exception:
						log(f'[失败] {account_name}: OAuth 授权超时')
						return BrowserResult(
							success=False,
							waf_cookies={},
							api_calls=api_calls,
							error='OAuth 授权超时'
						)

			# 第四步：等待签到逻辑执行
			log(f'[等待] {account_name}: 等待签到逻辑执行（{SIGNIN_TRIGGER_WAIT_MS // 1000}秒）...')
			await page.wait_for_timeout(SIGNIN_TRIGGER_WAIT_MS)

			# 检查是否有 OAuth 回调请求（签到触发点）
			oauth_callback_found = any('oauth' in call.lower() for call in api_calls)
			if oauth_callback_found:
				log(f'[关键] {account_name}: 检测到 OAuth 回调请求（签到已触发）')

			# 输出 API 调用统计
			if api_calls:
				log(f'[信息] {account_name}: 捕获到 {len(api_calls)} 个 API 调用')

			log(f'[成功] {account_name}: OAuth 签到流程完成')

			return BrowserResult(
				success=True,
				waf_cookies={},
				api_calls=api_calls
			)

		except Exception as e:
			error_msg = str(e)[:100]
			log(f'[失败] {account_name}: OAuth 登录失败: {error_msg}')
			return BrowserResult(
				success=False,
				waf_cookies={},
				api_calls=[],
				error=error_msg
			)

		finally:
			# CDP 模式只关闭页面，断开连接但不关闭浏览器
			if use_cdp:
				if page:
					try:
						await page.close()
					except Exception:
						pass
				# CDP 模式不关闭 browser，只断开连接
			else:
				if context:
					await context.close()


# ============ HTTP 签到（无浏览器依赖） ============


async def trigger_signin_via_http(
	account_name: str,
	domain: str,
	login_url: str,
	user_cookies: dict[str, str],
	api_user: str,
	api_user_key: str = 'new-api-user',
	log_fn: Callable[[str], None] | None = None
) -> BrowserResult:
	"""使用 HTTP 请求触发签到（无浏览器依赖）

	原理：AgentRouter 的签到在访问登录页时触发
	- 如果 session cookie 有效，服务器会重定向到首页
	- 重定向过程触发签到逻辑

	Args:
	    account_name: 账号名称（用于日志）
	    domain: 目标域名（如 https://agentrouter.org）
	    login_url: 登录页面 URL
	    user_cookies: 用户 cookies（包含 session）
	    api_user: API 用户标识
	    api_user_key: API 用户请求头名称
	    log_fn: 日志输出函数

	Returns:
	    BrowserResult: 包含成功状态和 API 调用记录
	"""

	def log(msg: str) -> None:
		if log_fn:
			log_fn(msg)
		else:
			print(msg)

	log(f'[HTTP] {account_name}: 使用 HTTP 请求触发签到...')

	api_calls: list[str] = []

	try:
		async with httpx.AsyncClient(
			http2=True,
			timeout=HTTP_TIMEOUT_SECONDS,
			follow_redirects=True,
			verify=True
		) as client:
			# 设置 cookies
			for name, value in user_cookies.items():
				client.cookies.set(name, value, domain=urlparse(domain).netloc)

			# 构建请求头
			headers = {
				'User-Agent': CHROME_USER_AGENT,
				'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
				'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
				'Accept-Encoding': 'gzip, deflate, br',
				'Connection': 'keep-alive',
				'Upgrade-Insecure-Requests': '1',
				api_user_key: api_user,
			}

			# 第一步：访问登录页面
			log(f'[HTTP] {account_name}: 访问登录页面 {login_url}')
			response = await client.get(login_url, headers=headers)
			api_calls.append(f'GET {login_url} -> {response.status_code}')

			# 检查是否被重定向（表示已登录）
			final_url = str(response.url)
			if '/login' not in final_url.lower():
				log(f'[成功] {account_name}: 已登录状态，被重定向到 {final_url}')
				log(f'[HTTP] {account_name}: 签到应该已经通过页面加载触发')

				# 第二步：调用 /api/user/self 确保签到触发
				user_self_url = f'{domain}/api/user/self'
				log(f'[HTTP] {account_name}: 调用 {user_self_url} 确认签到')

				api_headers = headers.copy()
				api_headers['Accept'] = 'application/json'

				user_response = await client.get(user_self_url, headers=api_headers)
				api_calls.append(f'GET {user_self_url} -> {user_response.status_code}')

				if user_response.status_code == 200:
					log(f'[成功] {account_name}: /api/user/self 调用成功')
					return BrowserResult(
						success=True,
						waf_cookies={},
						api_calls=api_calls
					)
				else:
					log(f'[警告] {account_name}: /api/user/self 返回 {user_response.status_code}')
					# 仍然返回成功，因为重定向本身可能已经触发了签到
					return BrowserResult(
						success=True,
						waf_cookies={},
						api_calls=api_calls
					)
			else:
				# 没有被重定向，可能 session 已过期
				log(f'[失败] {account_name}: 仍在登录页面，session 可能已过期')
				return BrowserResult(
					success=False,
					waf_cookies={},
					api_calls=api_calls,
					error='session 已过期，需要重新登录'
				)

	except httpx.HTTPStatusError as e:
		error_msg = f'HTTP 错误: {e.response.status_code}'
		log(f'[失败] {account_name}: {error_msg}')
		return BrowserResult(
			success=False,
			waf_cookies={},
			api_calls=api_calls,
			error=error_msg
		)
	except Exception as e:
		error_msg = str(e)[:100]
		log(f'[失败] {account_name}: HTTP 请求失败: {error_msg}')
		return BrowserResult(
			success=False,
			waf_cookies={},
			api_calls=api_calls,
			error=error_msg
		)
