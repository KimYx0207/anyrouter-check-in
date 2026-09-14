"""登录失效及上游合并兼容性的回归测试，全部使用虚构凭据。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import checkin
from utils.browser import BrowserLoginResult
from utils.config import AccountConfig, AppConfig, ProviderConfig
from utils.result import SigninStatus, update_signin_history


def make_account():
	return AccountConfig(cookies={'session': 'test-only'}, api_user='12345', name='测试账号')


def user_info(quota):
	return {'success': True, 'quota': quota, 'used_quota': 0.0, 'display': f'余额 {quota}'}


def test_json_401_is_not_treated_as_success():
	response = {'status': 401, 'contentType': 'application/json', 'text': '{"code":0,"success":true}'}

	attempt = checkin.parse_browser_check_in_response(response, '测试账号')

	assert not attempt.success
	assert attempt.error.startswith('HTTP 401')


@pytest.mark.parametrize(
	'response',
	[
		{'status': 200, 'contentType': 'text/html', 'text': '<html>Site verification</html>'},
		{'status': 200, 'contentType': 'application/json', 'text': 'not-json'},
		{'status': 200, 'contentType': 'application/json', 'text': '[]'},
	],
)
def test_invalid_user_info_response_reports_format(response):
	result = checkin.parse_browser_user_info_response(response)

	assert result['success'] is False
	assert 'HTTP 200' in result['error']
	assert 'JSON' in result['error']
	assert response['text'] not in result['error']


def test_html_401_preserves_authentication_error():
	result = checkin.parse_browser_user_info_response(
		{'status': 401, 'contentType': 'text/html', 'text': '<html>Unauthorized</html>'}
	)

	assert result['success'] is False
	assert result['error'].startswith('HTTP 401')
	assert 'session 和 api_user' in result['error']


@pytest.mark.asyncio
async def test_visible_slider_uses_track_bounds_and_releases_mouse():
	handle = SimpleNamespace(
		is_visible=AsyncMock(return_value=True),
		bounding_box=AsyncMock(return_value={'x': 100, 'y': 200, 'width': 40, 'height': 40}),
	)
	track = SimpleNamespace(
		locator=MagicMock(return_value=SimpleNamespace(first=handle)),
		bounding_box=AsyncMock(return_value={'x': 100, 'y': 200, 'width': 320, 'height': 40}),
	)
	frame = SimpleNamespace(locator=MagicMock(return_value=SimpleNamespace(first=track)))
	mouse = SimpleNamespace(move=AsyncMock(), down=AsyncMock(), up=AsyncMock())
	page = SimpleNamespace(frames=[frame], mouse=mouse)

	assert await checkin.complete_visible_slider(page, '测试账号') is True
	assert mouse.move.await_args_list[0].args == (120, 220)
	assert mouse.move.await_args_list[1].args == (400, 220)
	mouse.down.assert_awaited_once()
	mouse.up.assert_awaited_once()

	mouse.move = AsyncMock(side_effect=[None, RuntimeError('drag failed')])
	mouse.up.reset_mock()
	with pytest.raises(RuntimeError, match='drag failed'):
		await checkin.complete_visible_slider(page, '测试账号')
	mouse.up.assert_awaited_once()


@pytest.mark.asyncio
async def test_slider_helper_leaves_normal_page_alone():
	handle = SimpleNamespace(is_visible=AsyncMock(return_value=False))
	track = SimpleNamespace(locator=MagicMock(return_value=SimpleNamespace(first=handle)))
	frame = SimpleNamespace(
		locator=MagicMock(return_value=SimpleNamespace(first=track)),
		get_by_text=MagicMock(return_value=SimpleNamespace(first=handle)),
	)
	mouse = SimpleNamespace(move=AsyncMock(), down=AsyncMock(), up=AsyncMock())

	assert await checkin.complete_visible_slider(SimpleNamespace(frames=[frame], mouse=mouse), '测试账号') is False
	mouse.move.assert_not_awaited()
	mouse.down.assert_not_awaited()


@pytest.mark.asyncio
async def test_slider_with_new_class_names_uses_visible_verification_label():
	handle = SimpleNamespace(is_visible=AsyncMock(return_value=False))
	legacy_track = SimpleNamespace(locator=MagicMock(return_value=SimpleNamespace(first=handle)))
	track = SimpleNamespace(bounding_box=AsyncMock(return_value={'x': 100, 'y': 200, 'width': 320, 'height': 40}))
	label = SimpleNamespace(is_visible=AsyncMock(return_value=True), locator=MagicMock(return_value=track))
	frame = SimpleNamespace(
		locator=MagicMock(return_value=SimpleNamespace(first=legacy_track)),
		get_by_text=MagicMock(return_value=SimpleNamespace(first=label)),
	)
	mouse = SimpleNamespace(move=AsyncMock(), down=AsyncMock(), up=AsyncMock())

	assert await checkin.complete_visible_slider(SimpleNamespace(frames=[frame], mouse=mouse), '测试账号') is True
	assert mouse.move.await_args_list[0].args == (120, 220)
	assert mouse.move.await_args_list[1].args == (400, 220)
	track.bounding_box.return_value = {'x': 0, 'y': 0, 'width': 1920, 'height': 1080}
	mouse.down.reset_mock()
	assert await checkin.complete_visible_slider(SimpleNamespace(frames=[frame], mouse=mouse), '测试账号') is False
	mouse.down.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
	'cookies',
	[
		{'session': 'test-only', 'acw_tc': 'old-acw', 'cdn_sec_tc': 'old-cdn', 'acw_sc__v2': 'old-v2'},
		'session=test-only; acw_tc=old-acw; cdn_sec_tc=old-cdn; acw_sc__v2=old-v2',
	],
)
async def test_expired_cookie_stops_before_checkin_and_closes_browser(monkeypatch, cookies):
	account = make_account()
	account.cookies = cookies
	config = AppConfig.load_from_env()
	page = SimpleNamespace(goto=AsyncMock())
	context = SimpleNamespace(
		new_page=AsyncMock(return_value=page),
		add_cookies=AsyncMock(),
		close=AsyncMock(),
	)
	launch = AsyncMock(return_value=context)
	fetch = AsyncMock(return_value={'status': 401, 'contentType': 'application/json', 'text': '{"success":false}'})
	monkeypatch.setattr(checkin, 'launch_login_context', launch)
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'browser_fetch_json', fetch)

	attempt, before, after = await checkin.check_in_with_browser(account, '测试账号', config.providers['anyrouter'])

	assert not attempt.success
	assert 'session 和 api_user' in attempt.error
	assert before['success'] is False
	assert after is None
	fetch.assert_awaited_once()
	assert fetch.await_args.args[2] == 'GET'
	assert launch.await_args.args[0].persist_profile is False
	injected_cookies = {cookie['name']: cookie['value'] for cookie in context.add_cookies.await_args.args[0]}
	assert injected_cookies == {'session': 'test-only'}
	context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_cookies_keep_fresh_waf_and_discard_old_missing_values(monkeypatch):
	provider = AppConfig.load_from_env().providers['anyrouter']
	account_cookies = {
		'session': 'test-only',
		'acw_tc': 'old-acw',
		'cdn_sec_tc': 'old-cdn',
		'acw_sc__v2': 'old-v2',
		'theme': 'dark',
	}
	monkeypatch.setattr(
		checkin,
		'get_waf_cookies_with_browser',
		AsyncMock(return_value={'acw_tc': 'fresh-acw', 'acw_sc__v2': 'fresh-v2'}),
	)

	cookies = await checkin.prepare_cookies('测试账号', provider, account_cookies)

	assert cookies == {'session': 'test-only', 'theme': 'dark', 'acw_tc': 'fresh-acw', 'acw_sc__v2': 'fresh-v2'}
	assert account_cookies['cdn_sec_tc'] == 'old-cdn'


@pytest.mark.asyncio
async def test_failed_api_is_not_hidden_by_unchanged_balance(monkeypatch):
	monkeypatch.setattr(
		checkin,
		'check_in_with_browser',
		AsyncMock(return_value=(checkin.CheckInAttempt(False, 'HTTP 401'), user_info(100), user_info(100))),
	)

	result = await checkin.check_in_account(make_account(), 0, AppConfig.load_from_env(), {})

	assert result.status is SigninStatus.FAILED
	assert result.error == 'HTTP 401'
	assert result.new_record is None
	assert update_signin_history({}, [result]) == {}


@pytest.mark.asyncio
async def test_automatic_checkin_requires_authenticated_user_info(monkeypatch):
	provider = ProviderConfig(name='test', domain='https://example.com', sign_in_path=None)
	client = MagicMock()
	client.get.return_value = SimpleNamespace(status_code=401, headers={})
	monkeypatch.setattr(checkin.httpx, 'Client', MagicMock(return_value=client))
	account = AccountConfig(cookies={'session': 'test-only'}, api_user='12345', provider='test')

	result = await checkin.check_in_account(account, 0, AppConfig(providers={'test': provider}), {})

	assert result.status is SigninStatus.FAILED
	assert result.error.startswith('HTTP 401')
	assert result.new_record is None
	client.post.assert_not_called()
	client.close.assert_called_once()


@pytest.mark.asyncio
async def test_email_login_uses_discovered_account_id(monkeypatch):
	account = AccountConfig(email='test@example.com', password='test-only')
	login = AsyncMock(return_value=BrowserLoginResult(cookies={'session': 'new-test-only'}, api_user='67890'))
	browser = AsyncMock(return_value=(checkin.CheckInAttempt(True), user_info(0), user_info(25)))
	monkeypatch.setattr(checkin, 'login_with_credentials', login)
	monkeypatch.setattr(checkin, 'check_in_with_browser', browser)

	result = await checkin.check_in_account(account, 0, AppConfig.load_from_env(), {})

	assert result.account_key == 'anyrouter_67890'
	assert result.status is SigninStatus.SUCCESS
	assert result.balance_before == 0
	assert result.balance_diff == 25
	assert browser.await_args.args[0].cookies == {'session': 'new-test-only'}
	assert browser.await_args.args[0].api_user == '67890'
	assert account.api_user is None


@pytest.mark.asyncio
async def test_failed_email_login_does_not_fall_back_to_stale_cookie(monkeypatch):
	account = AccountConfig(
		cookies={'session': 'old-test-only'}, api_user='12345', email='test@example.com', password='test-only'
	)
	browser = AsyncMock()
	monkeypatch.setattr(checkin, 'login_with_credentials', AsyncMock(return_value=None))
	monkeypatch.setattr(checkin, 'check_in_with_browser', browser)

	result = await checkin.check_in_account(account, 0, AppConfig.load_from_env(), {})

	assert result.status is SigninStatus.ERROR
	assert result.new_record is None
	browser.assert_not_awaited()
