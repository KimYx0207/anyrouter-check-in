import json

from utils.config import AppConfig, ProviderConfig, load_accounts_config


def test_builtin_provider_profile_persistence_defaults(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is True
	assert config.providers['agentrouter'].persist_profile is False


def test_provider_profile_persistence_can_override_builtin(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps(
			{
				'anyrouter': {'domain': 'https://anyrouter.top', 'persist_profile': False},
				'agentrouter': {'domain': 'https://agentrouter.org', 'persist_profile': True},
			}
		),
	)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is False
	assert config.providers['agentrouter'].persist_profile is True


def test_custom_provider_profile_persistence_defaults_to_false(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'custom': {'domain': 'https://custom.example.com'}}))

	config = AppConfig.load_from_env()

	assert config.providers['custom'].persist_profile is False


def test_provider_from_dict_inherits_profile_persistence_from_defaults():
	defaults = ProviderConfig(name='custom', domain='https://old.example.com', persist_profile=True)

	provider = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://new.example.com'},
		defaults=defaults,
	)

	assert provider.persist_profile is True


def test_partial_builtin_override_preserves_routes_and_legacy_method(monkeypatch):
	monkeypatch.setenv('PROVIDERS', '{"anyrouter":{"signin_method":"http_login","persist_profile":false}}')

	provider = AppConfig.load_from_env().providers['anyrouter']

	assert provider.domain == 'https://anyrouter.top'
	assert provider.sign_in_path == '/api/user/sign_in'
	assert provider.bypass_method is None
	assert provider.persist_profile is False


def test_cookie_accounts_keep_numeric_user_ids_compatible(monkeypatch):
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', '[{"cookies":{"session":"test-only"},"api_user":12345}]')

	accounts = load_accounts_config()

	assert accounts[0].api_user == '12345'
	assert accounts[0].has_login_credentials() is False


def test_email_login_can_omit_cookie_and_api_user(monkeypatch):
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', '[{"email":"test@example.com","password":"test-only"}]')

	accounts = load_accounts_config()

	assert accounts[0].has_login_credentials() is True
	assert accounts[0].cookies is None
	assert accounts[0].api_user is None


def test_cookie_login_rejects_empty_user_id(monkeypatch):
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', '[{"cookies":{"session":"test-only"},"api_user":null}]')

	assert load_accounts_config() is None
