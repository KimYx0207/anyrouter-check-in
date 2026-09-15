"""Cloud proxy regression tests with fake session and node credentials."""

import base64
import json
from uuid import uuid4

import httpx
import pytest

from scripts.refresh_veee_proxy import ProxySetupError, build_config, decode_service_response, fetch_node, load_session


@pytest.fixture
def session():
	return {
		'api_base': 'https://cdn.kisslucky.com:9527',
		'response_alphabet': 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/',
		'auth_token': 'fake-session-token',
		'device_id': 'fake-device-id',
		'line_id': 123,
		'model': 'test',
	}


def test_decode_plain_and_obfuscated_service_response(session):
	payload = {'code': 200, 'bean': {'value': 'test'}}
	text = json.dumps(payload)
	alphabet = session['response_alphabet']
	translation = {ord(char): alphabet[index ^ 1] for index, char in enumerate(alphabet)}
	encoded = base64.b64encode(text.encode()).decode().translate(translation)
	for response in (text, encoded, json.dumps(encoded)):
		assert decode_service_response(response, alphabet) == payload


def test_installed_client_service_port_is_supported(session):
	assert load_session(json.dumps(session))['api_base'] == 'https://cdn.kisslucky.com:9527'


@pytest.mark.parametrize('response', ['<html>verification</html>', '[]', 'null', '"not-valid"'])
def test_invalid_service_response_fails_without_echoing_payload(session, response):
	with pytest.raises(ProxySetupError) as error:
		decode_service_response(response, session['response_alphabet'])
	assert response not in str(error.value)


@pytest.mark.parametrize(
	'origin',
	[
		'http://cdn.kisslucky.com',
		'https://example.com',
		'https://cdn.kisslucky.com@example.com',
		'https://cdn.kisslucky.com?token=bad',
	],
)
def test_credentials_cannot_be_sent_to_another_origin(session, origin):
	session['api_base'] = origin
	with pytest.raises(ProxySetupError, match='authorized host'):
		load_session(json.dumps(session))


def test_refresh_requests_new_credentials_every_time_without_global_proxy(session, monkeypatch):
	monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
	requests = []
	uuids = [str(uuid4()), str(uuid4())]

	def handle(request):
		requests.append(request)
		assert request.url.path == '/Desktop/linkBegin'
		assert request.headers['Authorization'] == session['auth_token']
		assert json.loads(request.content)['lineId'] == session['line_id']
		return httpx.Response(200, json={'code': 200, 'bean': {'password': uuids[len(requests) - 1]}})

	transport = httpx.MockTransport(handle)
	first = fetch_node(session, transport=transport)
	second = fetch_node(session, transport=transport)
	assert first['password'] != second['password']
	assert len(requests) == 2


@pytest.mark.parametrize('status', [302, 401, 503])
def test_http_failure_does_not_follow_redirect_or_expose_response(session, status):
	requests = []

	def handle(request):
		requests.append(request)
		return httpx.Response(status, headers={'location': 'https://example.com'}, text=session['auth_token'])

	with pytest.raises(ProxySetupError) as error:
		fetch_node(session, transport=httpx.MockTransport(handle))
	assert str(status) in str(error.value)
	assert session['auth_token'] not in str(error.value)
	assert len(requests) == 1


def test_expired_session_is_failure_even_with_http_200(session):
	transport = httpx.MockTransport(
		lambda request: httpx.Response(200, json={'code': 401, 'message': session['auth_token'], 'bean': {}})
	)
	with pytest.raises(ProxySetupError, match='code 401') as error:
		fetch_node(session, transport=transport)
	assert session['auth_token'] not in str(error.value)


def test_config_keeps_the_transport_required_by_existing_working_node():
	node = {'ip': '192.0.2.1', 'port': 443, 'domain': 'node.example.com', 'password': str(uuid4())}
	config = build_config(node, 7890)
	proxy = config['proxies'][0]
	assert proxy['type'] == 'vless'
	assert proxy['alpn'] == ['http/1.1']
	assert proxy['ws-opts'] == {
		'path': '/download',
		'headers': {'Host': '192.0.2.1:443', 'User-Agent': 'Go-http-client/1.1'},
	}
	assert proxy['skip-cert-verify'] is False
	assert config['bind-address'] == '127.0.0.1'
	assert config['allow-lan'] is False


def test_invalid_node_credentials_are_not_printed():
	with pytest.raises(ProxySetupError) as error:
		build_config({'password': 'bad-sensitive-value'}, 7890)
	assert 'bad-sensitive-value' not in str(error.value)
