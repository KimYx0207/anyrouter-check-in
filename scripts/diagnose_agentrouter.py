"""Bounded, manual AgentRouter browser comparison; never print session values."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from checkin import browser_fetch_json, complete_visible_slider, parse_cookies
from utils.browser import launch_login_context, load_browser_login_settings, save_login_screenshot, wait_for_waf_ready
from utils.config import load_accounts_config

ORIGIN = 'https://agentrouter.org'
API_URL = f'{ORIGIN}/api/user/self'

REQUEST_JS = """async ({url, apiUser, transport}) => {
	const headers = {'Accept': 'application/json, text/plain, */*',
		'New-API-User': apiUser, 'Cache-Control': 'no-store'};
	if (transport === 'fetch') {
		const response = await fetch(url, {credentials: 'include', headers,
			signal: AbortSignal.timeout(30000)});
		return {status: response.status, contentType: response.headers.get('content-type') || '',
			text: await response.text()};
	}
	return await new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		xhr.open('GET', url);
		xhr.withCredentials = true;
		xhr.timeout = 30000;
		Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
		xhr.onload = () => resolve({status: xhr.status,
			contentType: xhr.getResponseHeader('content-type') || '', text: xhr.responseText});
		xhr.onerror = () => reject(new Error('XHR network error'));
		xhr.ontimeout = () => reject(new Error('XHR timeout'));
		xhr.send();
	});
}"""


def report(label: str, response: dict, api_user: str) -> bool:
	"""Log status and identity match only; omit body, headers and cookie values."""
	payload = None
	try:
		payload = json.loads(response.get('text') or '')
	except (json.JSONDecodeError, TypeError):
		pass
	valid = isinstance(payload, dict) and payload.get('success') is True
	matched = valid and str((payload.get('data') or {}).get('id')) == api_user
	title = re.search(r'<title[^>]*>([^<]{0,80})</title>', response.get('text') or '', re.I)
	print(
		json.dumps(
			{
				'stage': label,
				'status': response.get('status'),
				'content_type': response.get('contentType', '')[:80],
				'authenticated': bool(valid),
				'account_matches': bool(matched),
				'verification_page': bool(title and 'verif' in title[1].lower()),
			},
			ensure_ascii=False,
		),
		flush=True,
	)
	return bool(matched)


async def main() -> int:
	"""Compare website request transport and verification scoped to the API URL."""
	accounts = [account for account in load_accounts_config() or [] if account.provider == 'agentrouter']
	if not accounts:
		print('No AgentRouter account configured')
		return 2
	account = accounts[0]
	api_user = str(account.api_user)
	settings = load_browser_login_settings('transport-probe', 'agentrouter', persist_profile=False)
	context = await launch_login_context(settings)
	try:
		await context.add_cookies(
			[
				{
					'name': 'session',
					'value': parse_cookies(account.cookies)['session'],
					'domain': 'agentrouter.org',
					'path': '/',
					'httpOnly': True,
					'secure': True,
					'sameSite': 'Lax',
				}
			]
		)
		page = await context.new_page()
		await page.goto(f'{ORIGIN}/login', wait_until='domcontentloaded', timeout=45000)
		await wait_for_waf_ready(page, timeout_ms=10000)
		if await complete_visible_slider(page, 'transport-probe'):
			await wait_for_waf_ready(page, timeout_ms=10000)
		baseline = await browser_fetch_json(page, API_URL, 'GET', 'new-api-user', api_user)
		report('original-fetch', baseline, api_user)
		passed = False
		for transport in ('fetch', 'xhr'):
			response = await page.evaluate(REQUEST_JS, {'url': API_URL, 'apiUser': api_user, 'transport': transport})
			passed = report(f'website-headers-{transport}', response, api_user) or passed
		if passed:
			return 0

		async def authenticate_api_navigation(route) -> None:
			headers = {**route.request.headers, 'new-api-user': api_user}
			await route.continue_(headers=headers)

		await page.route(API_URL, authenticate_api_navigation)
		await page.goto(API_URL, wait_until='domcontentloaded', timeout=45000)
		await wait_for_waf_ready(page, timeout_ms=10000)
		if await complete_visible_slider(page, 'api-navigation-probe'):
			await wait_for_waf_ready(page, timeout_ms=10000)
		for transport in ('fetch', 'xhr'):
			response = await page.evaluate(REQUEST_JS, {'url': API_URL, 'apiUser': api_user, 'transport': transport})
			passed = report(f'api-navigation-{transport}', response, api_user) or passed
		await save_login_screenshot(page, 'agentrouter', 'transport-probe', 'final-page')
		return 0 if passed else 1
	finally:
		await context.close()


if __name__ == '__main__':
	raise SystemExit(asyncio.run(asyncio.wait_for(main(), timeout=240)))
