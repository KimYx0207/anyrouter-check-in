"""OAuth identity, credential isolation and reward regressions using fake sessions only."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import checkin
from utils import github_oauth
from utils.config import AccountConfig, AppConfig, ProviderConfig
from utils.result import SigninRecord, SigninStatus, update_signin_history


def account():
	return AccountConfig(
		provider='agentrouter',
		api_user='123',
		cookies={'session': 'fake-platform'},
		github_cookies={'user_session': 'fake-github'},
	)


def provider():
	return ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None)


def profile(quota=100, used=20, user_id=123):
	return {'id': user_id, 'quota': quota * 500000, 'used_quota': used * 500000}


def fake_browser(monkeypatch, *, after=None, callback_success=True, github_path=None, delayed_button=False):
	listeners = {}
	page = SimpleNamespace(
		url='https://agentrouter.org/login',
		evaluate=AsyncMock(
			side_effect=[
				{'clientId': 'fake-client', 'state': 'fake-state'},
				{'success': True, 'data': after or profile()},
			]
		),
		wait_for_url=AsyncMock(),
		on=lambda event, handler: listeners.update({event: handler}),
		remove_listener=MagicMock(),
	)

	async def complete_callback(**kwargs):
		page.url = 'https://agentrouter.org/console'
		await listeners['response'](
			SimpleNamespace(
				url='https://agentrouter.org/api/oauth/github?code=fake-code',
				status=200,
				json=AsyncMock(return_value={'success': callback_success}),
			)
		)

	button = SimpleNamespace(
		is_visible=AsyncMock(side_effect=[False, True] if delayed_button else None, return_value=True),
		is_enabled=AsyncMock(return_value=True),
		click=AsyncMock(side_effect=complete_callback),
	)
	page.locator = MagicMock(return_value=SimpleNamespace(first=button))

	async def navigate(url, **kwargs):
		page.url = url
		if url.startswith('https://github.com/login/oauth/authorize?'):
			if github_path:
				page.url = 'https://github.com' + github_path
			else:
				await complete_callback()

	page.goto = AsyncMock(side_effect=navigate)
	context = SimpleNamespace(new_page=AsyncMock(return_value=page), add_cookies=AsyncMock(), close=AsyncMock())
	launch = AsyncMock(return_value=context)
	monkeypatch.setattr(github_oauth, 'launch_login_context', launch)
	monkeypatch.setattr(github_oauth, 'wait_for_waf_ready', AsyncMock())
	return page, context, launch


def test_config_reads_explicit_github_session_without_showing_it_in_repr():
	config = AccountConfig.from_dict({'api_user': 123, 'github_cookies': {'user_session': 'secret-test'}}, 0)
	assert config.github_cookies == {'user_session': 'secret-test'}
	assert 'secret-test' not in repr(config)


def test_only_github_auth_cookies_are_injected_and_only_on_github():
	cookies = github_oauth.github_browser_cookies(
		{'user_session': 'fake', 'session': 'provider-secret', 'other': 'unrelated'}
	)
	assert [item['name'] for item in cookies] == ['user_session']
	assert cookies[0]['url'] == 'https://github.com/'
	assert cookies[0]['secure'] is True
	assert 'provider-secret' not in json.dumps(cookies)


@pytest.mark.parametrize('value', [None, {}, {'user_session': ''}, {'user_session': 'secret\nvalue'}, 'secret'])
def test_invalid_cookies_fail_without_exposing_input(value):
	with pytest.raises(github_oauth.GithubOAuthError) as error:
		github_oauth.github_browser_cookies(value)
	assert 'secret' not in str(error.value)


@pytest.mark.parametrize(
	'origin',
	[
		'http://agentrouter.org',
		'https://agentrouter.org.evil.test',
		'https://agentrouter.org@evil.test',
		'https://agentrouter.org/path',
	],
)
def test_oauth_rejects_unexpected_site_origin(origin):
	with pytest.raises(github_oauth.GithubOAuthError):
		github_oauth.validate_agentrouter_origin(origin)


@pytest.mark.asyncio
async def test_oauth_requires_callback_and_matching_identity_and_closes_private_browser(monkeypatch):
	page, context, launch = fake_browser(monkeypatch)
	result = await github_oauth.login_agentrouter_with_github(account(), 'Test', provider())
	assert result == profile()
	assert launch.await_args.args[0].persist_profile is False
	assert {cookie['name'] for cookie in context.add_cookies.await_args.args[0]} == {'user_session'}
	context.close.assert_awaited_once()
	page.remove_listener.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/login/oauth/authorize', '/login/oauth/select_account'])
async def test_oauth_waits_for_authorization_and_selects_only_current_account(monkeypatch, path):
	page, context, _ = fake_browser(monkeypatch, github_path=path, delayed_button=True)
	assert await github_oauth.login_agentrouter_with_github(account(), 'Test', provider()) == profile()
	page.locator.return_value.first.click.assert_awaited_once()
	if path.endswith('select_account'):
		assert 'authorize_app' in page.locator.call_args.args[0]
	context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_github_session_reports_login_required_without_clicking(monkeypatch):
	page, context, _ = fake_browser(monkeypatch, github_path='/login')
	with pytest.raises(github_oauth.GithubOAuthError, match='login-required'):
		await github_oauth.login_agentrouter_with_github(account(), 'Test', provider())
	page.locator.return_value.first.click.assert_not_awaited()
	context.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [{'after': profile(user_id=456)}, {'callback_success': False}])
async def test_wrong_account_or_rejected_callback_fails(monkeypatch, options):
	_, context, _ = fake_browser(monkeypatch, **options)
	with pytest.raises(github_oauth.GithubOAuthError):
		await github_oauth.login_agentrouter_with_github(account(), 'Test', provider())
	context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_exception_does_not_expose_oauth_codes_or_cookies(monkeypatch):
	page, context, _ = fake_browser(monkeypatch)
	page.goto.side_effect = RuntimeError('https://agentrouter.org/oauth/github?code=secret-code fake-github')
	with pytest.raises(github_oauth.GithubOAuthError) as error:
		await github_oauth.login_agentrouter_with_github(account(), 'Test', provider())
	assert 'secret-code' not in str(error.value)
	assert 'fake-github' not in str(error.value)
	assert 'stage=provider-login' in str(error.value)
	context.close.assert_awaited_once()


@pytest.mark.parametrize(
	'changes',
	[
		{'type': 2},
		{'created_at': 0},
		{'created_at': 2000000000},
		{'created_at': True},
		{'content': '登录成功'},
		{'content': '每日签到成功，增加额度 ＄0.000000 额度'},
		{'content': '每日签到成功，增加额度 ＄25.000000 额度 extra'},
	],
)
def test_only_recent_explicit_positive_server_rewards_restore_history(changes):
	now = datetime.fromtimestamp(1900000000)
	item = {'type': 4, 'created_at': 1899999940, 'content': '每日签到成功，增加额度 ＄25.000000 额度'}
	client = MagicMock()
	client.get.return_value.status_code = 200
	client.get.return_value.json.return_value = {'success': True, 'data': {'items': [item]}}
	assert checkin.get_recent_agentrouter_reward(client, provider(), '123', now=now) == now - timedelta(seconds=60)
	item.update(changes)
	assert checkin.get_recent_agentrouter_reward(client, provider(), '123', now=now) is None


@pytest.mark.asyncio
async def test_server_reward_recovers_original_cooldown_without_repeating_oauth(monkeypatch):
	login = setup_reward_check(monkeypatch, profile())
	reward_time = datetime.now() - timedelta(hours=2)
	monkeypatch.setattr(checkin, 'get_recent_agentrouter_reward', lambda *args: reward_time)
	result = await checkin.check_in_account(account(), 0, AppConfig({'agentrouter': provider()}), {})
	assert result.status is SigninStatus.SKIPPED
	assert result.balance_diff is None
	assert result.new_record.time == reward_time
	assert result.new_record.reward_verified is True
	assert update_signin_history({}, [result])[result.account_key].time == reward_time
	login.assert_not_awaited()


def setup_reward_check(monkeypatch, after):
	client = MagicMock()
	client.__enter__.return_value = client
	monkeypatch.setattr(checkin.httpx, 'Client', MagicMock(return_value=client))
	monkeypatch.setattr(
		checkin, 'get_user_info', lambda *args: checkin.parse_user_info_payload({'success': True, 'data': profile()})
	)
	login = AsyncMock(return_value=after)
	monkeypatch.setattr(checkin, 'login_agentrouter_with_github', login)
	return login


@pytest.mark.asyncio
async def test_unchanged_quota_after_oauth_is_not_success_or_a_new_cooldown(monkeypatch):
	setup_reward_check(monkeypatch, profile())
	result = await checkin.check_in_account(account(), 0, AppConfig({'agentrouter': provider()}), {})
	assert result.status is SigninStatus.FAILED
	assert result.new_record is None
	assert update_signin_history({}, [result]) == {}
	assert '未观察到新增奖励' in result.error


@pytest.mark.asyncio
async def test_reward_is_detected_even_when_usage_reduces_remaining_balance(monkeypatch):
	setup_reward_check(monkeypatch, profile(quota=90, used=55))
	result = await checkin.check_in_account(account(), 0, AppConfig({'agentrouter': provider()}), {})
	assert result.status is SigninStatus.SUCCESS
	assert result.balance_diff == 25
	assert result.new_record.reward_verified is True


@pytest.mark.asyncio
async def test_old_false_cooldown_does_not_skip_new_verification(monkeypatch):
	login = setup_reward_check(monkeypatch, profile(quota=125))
	old_history = {'agentrouter_123': SigninRecord(datetime.now(), 100)}
	result = await checkin.check_in_account(account(), 0, AppConfig({'agentrouter': provider()}), old_history)
	login.assert_awaited_once()
	assert result.status is SigninStatus.SUCCESS
	assert old_history['agentrouter_123'].reward_verified is False


@pytest.mark.asyncio
async def test_verified_reward_keeps_cooldown(monkeypatch):
	login = setup_reward_check(monkeypatch, profile())
	history = {'agentrouter_123': SigninRecord(datetime.now(), 100, reward_verified=True)}
	result = await checkin.check_in_account(account(), 0, AppConfig({'agentrouter': provider()}), history)
	assert result.status is SigninStatus.SKIPPED
	login.assert_not_awaited()


def test_reward_verification_survives_history_round_trip():
	record = SigninRecord(datetime.now(), 100, reward_verified=True)
	assert SigninRecord.from_dict(record.to_dict()).reward_verified is True
	assert SigninRecord.from_dict({'time': record.time.isoformat(), 'balance': 100}).reward_verified is False


def test_receipt_contains_results_without_identifiers_or_errors(monkeypatch, tmp_path):
	path = tmp_path / 'proof.json'
	monkeypatch.setenv('CHECKIN_PROOF_PATH', str(path))
	result = checkin.SigninResult('agentrouter_123', 'Private account', SigninStatus.FAILED, error='secret-test')
	checkin.write_checkin_proof([result])
	data = json.loads(path.read_text())
	assert data['accounts'][0]['rewardVerified'] is False
	assert 'secret-test' not in path.read_text()
	assert '123' not in json.dumps(data['accounts'])
	assert 'Private account' not in path.read_text()


@pytest.mark.asyncio
async def test_anyrouter_balance_is_captured_before_console_can_trigger_reward(monkeypatch):
	page = SimpleNamespace(goto=AsyncMock())
	context = SimpleNamespace(new_page=AsyncMock(return_value=page), add_cookies=AsyncMock(), close=AsyncMock())
	events = []

	async def goto(url, **kwargs):
		events.append(('navigate', url))

	async def fetch(_page, url, method, *args):
		events.append(('fetch', method))
		payload = {'success': True, 'data': profile()} if method == 'GET' else {'success': True}
		return {'status': 200, 'contentType': 'application/json', 'text': json.dumps(payload)}

	page.goto.side_effect = goto
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'wait_for_waf_ready', AsyncMock())
	monkeypatch.setattr(checkin, 'browser_fetch_json', fetch)
	await checkin.check_in_with_browser(
		AccountConfig(api_user='123', cookies={'session': 'fake'}),
		'Test',
		AppConfig.load_from_env().providers['anyrouter'],
	)
	assert events.index(('fetch', 'GET')) < events.index(('navigate', 'https://anyrouter.top/console/token'))
