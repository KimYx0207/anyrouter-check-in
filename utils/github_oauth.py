"""Use an explicitly configured GitHub session for AgentRouter OAuth.

GitHub cookies live only in an ephemeral browser context. Neither browser profiles,
screenshots, callback URLs nor response bodies are saved by this module.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlencode, urlsplit

from utils.browser import launch_login_context, load_browser_login_settings, wait_for_waf_ready


class GithubOAuthError(ValueError):
	"""A message that is safe for public workflow logs."""


def github_browser_cookies(cookies: dict | None) -> list[dict]:
	if not isinstance(cookies, dict) or not cookies.get('user_session'):
		raise GithubOAuthError('AgentRouter 需要 GitHub OAuth 重新登录；请配置该账号的 github_cookies')
	allowed = {'user_session', '__Host-user_session_same_site', 'logged_in', 'dotcom_user'}
	result = []
	for name in allowed:
		if name not in cookies:
			continue
		value = cookies[name]
		if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
			raise GithubOAuthError('github_cookies 格式无效')
		result.append(
			{
				'name': name,
				'value': value,
				'url': 'https://github.com/',
				'secure': True,
				'httpOnly': True,
				'sameSite': 'Lax',
			}
		)
	return result


def validate_agentrouter_origin(domain: str) -> str:
	parsed = urlsplit(domain)
	if (
		parsed.scheme != 'https'
		or parsed.netloc != 'agentrouter.org'
		or parsed.path not in ('', '/')
		or parsed.query
		or parsed.fragment
	):
		raise GithubOAuthError('GitHub OAuth 仅支持已配置的 https://agentrouter.org')
	return 'https://agentrouter.org'


async def login_agentrouter_with_github(account, account_name: str, provider) -> dict:
	"""Finish OAuth and verify the resulting platform identity, without an old site session."""
	origin = validate_agentrouter_origin(provider.domain)
	cookies = github_browser_cookies(account.github_cookies)
	if not account.api_user:
		raise GithubOAuthError('GitHub OAuth 缺少预期平台账号 api_user')
	settings = load_browser_login_settings(account_name, account.provider, persist_profile=False)
	context = None
	stage = 'browser'
	page = None
	try:
		context = await launch_login_context(settings, use_proxy=provider.use_proxy)
		await context.add_cookies(cookies)
		page = await context.new_page()
		stage = 'provider-login'
		await page.goto(origin + '/login', wait_until='domcontentloaded', timeout=settings.wait_timeout_ms)
		await wait_for_waf_ready(page, timeout_ms=settings.wait_timeout_ms)
		stage = 'oauth-state'
		params = await page.evaluate("""async () => {
			const status = await (await fetch('/api/status')).json();
			const state = await (await fetch('/api/oauth/state', {cache: 'no-store'})).json();
			if (!status.success || !state.success) return null;
			return {clientId: status.data?.github_client_id, state: state.data};
		}""")
		if not isinstance(params, dict) or not all(
			isinstance(params.get(key), str) and params[key] for key in ('clientId', 'state')
		):
			raise GithubOAuthError('无法获取 AgentRouter GitHub OAuth 配置')

		callback_done = asyncio.Event()
		callback_ok = False

		async def on_response(response):
			nonlocal callback_ok
			url = urlsplit(response.url)
			if url.scheme != 'https' or url.netloc != 'agentrouter.org' or url.path != '/api/oauth/github':
				return
			try:
				payload = await response.json()
				callback_ok = response.status == 200 and isinstance(payload, dict) and payload.get('success') is True
			except Exception:
				callback_ok = False
			callback_done.set()

		page.on('response', on_response)
		try:
			authorize_url = 'https://github.com/login/oauth/authorize?' + urlencode(
				{
					'client_id': params['clientId'],
					'state': params['state'],
					'scope': 'user:email',
				}
			)
			stage = 'github-navigation'
			await page.goto(authorize_url, wait_until='domcontentloaded', timeout=settings.wait_timeout_ms)
			stage = 'github-authorization'
			location = urlsplit(page.url)
			if location.netloc == 'github.com':
				if location.path != '/login/oauth/authorize':
					raise GithubOAuthError('GitHub 会话已失效或需要人工验证，请更新该账号的 github_cookies')
				button = page.locator('button[name="authorize"]').first
				if await button.is_visible():
					await button.click(timeout=10000)
			elif location.scheme != 'https' or location.netloc != 'agentrouter.org':
				raise GithubOAuthError('GitHub OAuth 跳转到非预期站点')
			stage = 'provider-callback'
			await asyncio.wait_for(callback_done.wait(), timeout=min(settings.wait_timeout_ms / 1000, 60))
			if not callback_ok:
				raise GithubOAuthError('AgentRouter GitHub OAuth 回调未成功')
			# Some OAuth frontends stay on their callback route instead of navigating themselves.
			stage = 'provider-verification'
			await page.goto(origin + '/console', wait_until='domcontentloaded', timeout=settings.wait_timeout_ms)
			payload = await page.evaluate(
				"""async ({header, user}) => {
				const response = await fetch('/api/user/self', {headers: {[header]: user}, cache: 'no-store'});
				if (!response.ok) return null;
				return await response.json();
			}""",
				{'header': provider.api_user_key, 'user': account.api_user},
			)
			if (
				not isinstance(payload, dict)
				or payload.get('success') is not True
				or not isinstance(payload.get('data'), dict)
			):
				raise GithubOAuthError('OAuth 后未能验证平台登录状态')
			profile = payload['data']
			if str(profile.get('id')) != str(account.api_user):
				raise GithubOAuthError(
					'GitHub OAuth 登录到了其他平台账号，请核对 github_cookies 与 api_user 的对应关系'
				)
			if not all(
				isinstance(profile.get(key), (int, float)) and not isinstance(profile[key], bool)
				for key in ('quota', 'used_quota')
			):
				raise GithubOAuthError('OAuth 后缺少有效额度信息')
			# Return only the fields needed to prove identity and reward, never the full profile.
			return {key: profile[key] for key in ('id', 'quota', 'used_quota')}
		finally:
			page.remove_listener('response', on_response)
	except GithubOAuthError:
		raise
	except Exception as error:
		# Browser exceptions often contain callback URLs, OAuth codes or response bodies.
		location = urlsplit(page.url) if page is not None else None
		known_paths = {'/login', '/login/oauth/authorize', '/login/oauth/select_account', '/oauth/github', '/console'}
		path = location.path if location and location.path in known_paths else 'other'
		host = location.hostname if location and location.hostname in {'github.com', 'agentrouter.org'} else 'other'
		button_state = 'unknown'
		if page is not None and host == 'github.com' and path == '/login/oauth/authorize':
			try:
				button = page.locator('button[name="authorize"]').first
				button_state = 'visible' if await button.is_visible() else 'absent'
			except Exception:
				pass
		raise GithubOAuthError(
			f'GitHub OAuth 未完成（{type(error).__name__}; stage={stage}; page={host}{path}; authorize={button_state}），'
			'请检查会话有效性和站点验证'
		) from None
	finally:
		if context is not None:
			await context.close()
