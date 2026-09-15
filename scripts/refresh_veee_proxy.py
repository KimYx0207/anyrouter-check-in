"""Fetch a fresh Veee node for one check-in run without logging credentials."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx


class ProxySetupError(ValueError):
	"""A diagnostic that is safe to include in public Actions logs."""


def mask_secret(value: str) -> None:
	if value and os.getenv('GITHUB_ACTIONS') == 'true':
		escaped = value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
		print(f'::add-mask::{escaped}', flush=True)


def load_session(raw: str) -> dict:
	try:
		session = json.loads(raw)
	except (TypeError, ValueError):
		raise ProxySetupError('VEEE_SESSION_CONFIG must be a JSON object') from None
	if not isinstance(session, dict):
		raise ProxySetupError('VEEE_SESSION_CONFIG must be a JSON object')
	for key in ('api_base', 'response_alphabet', 'auth_token', 'device_id'):
		value = session.get(key)
		if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
			raise ProxySetupError(f'VEEE_SESSION_CONFIG has an invalid {key}')
	for key in ('auth_token', 'device_id'):
		mask_secret(session[key])
	try:
		origin = urlsplit(session['api_base'])
		allowed = (
			origin.scheme == 'https'
			and origin.hostname == 'cdn.kisslucky.com'
			and origin.port in (None, 443, 9527)
			and not origin.username
			and not origin.password
			and not origin.query
			and not origin.fragment
		)
	except ValueError:
		allowed = False
	if not allowed:
		raise ProxySetupError('Veee service origin is outside the authorized host')
	alphabet = session['response_alphabet']
	if len(alphabet) % 2 or len(set(alphabet)) != len(alphabet):
		raise ProxySetupError('Veee response alphabet is invalid')
	if not isinstance(session.get('line_id'), (int, str)) or not str(session['line_id']):
		raise ProxySetupError('VEEE_SESSION_CONFIG is missing line_id')
	if not isinstance(session.get('model', ''), str):
		raise ProxySetupError('VEEE_SESSION_CONFIG has an invalid model')
	return session


def decode_service_response(text: str, alphabet: str) -> dict:
	try:
		payload = json.loads(text)
		if isinstance(payload, dict):
			return payload
		if isinstance(payload, str):
			text = payload
	except ValueError:
		pass
	try:
		translation = {ord(char): alphabet[index ^ 1] for index, char in enumerate(alphabet)}
		payload = json.loads(base64.b64decode(text.translate(translation), validate=True))
	except (ValueError, UnicodeError, binascii.Error, IndexError):
		raise ProxySetupError('Veee returned an invalid service response') from None
	if not isinstance(payload, dict):
		raise ProxySetupError('Veee returned an invalid service response')
	return payload


def fetch_node(session: dict, *, transport: httpx.BaseTransport | None = None) -> dict:
	"""Request a new node directly on every run; never reuse cached node credentials."""
	headers = {
		'os': 'win',
		'clientVersion': '3.0.2',
		'appId': 'com.veee',
		'channelId': 'win-official',
		'deviceId': session['device_id'],
		'osV': '10',
		'Authorization': session['auth_token'],
	}
	body = {
		'lineId': session['line_id'],
		'model': session.get('model', ''),
		'platform': 'DESKTOP',
		'os': 'Windows',
		'deviceId': session['device_id'],
		'osVersion': '10',
		'pv': 1,
	}
	try:
		with httpx.Client(trust_env=False, follow_redirects=False, timeout=45, transport=transport) as client:
			response = client.post(session['api_base'].rstrip('/') + '/Desktop/linkBegin', headers=headers, json=body)
	except httpx.HTTPError:
		raise ProxySetupError('Direct Veee node request failed at the network layer') from None
	if response.status_code != 200:
		raise ProxySetupError(f'Veee node request returned HTTP {response.status_code}')
	payload = decode_service_response(response.text, session['response_alphabet'])
	if payload.get('code') != 200 or not isinstance(payload.get('bean'), dict):
		code = payload.get('code')
		safe_code = str(code) if isinstance(code, int) else 'unknown'
		raise ProxySetupError(f'Veee rejected node refresh (code {safe_code}); renew the authorized session if expired')
	return payload['bean']


def build_config(node: dict, port: int) -> dict:
	try:
		server = node['ip']
		domain = node['domain']
		password = str(UUID(node['password']))
		node_port = int(node['port'])
		path = node.get('path') or '/download'
		if not all(
			isinstance(value, str) and value and not any(ord(c) < 32 for c in value) for value in (server, domain, path)
		):
			raise ValueError
		if not path.startswith('/') or not 1 <= node_port <= 65535 or not 1 <= port <= 65535:
			raise ValueError
	except (KeyError, TypeError, ValueError, AttributeError):
		raise ProxySetupError('Veee returned incomplete or invalid node settings') from None
	mask_secret(password)
	host = f'[{server}]' if ':' in server else server
	proxy = {
		'name': 'checkin-node',
		'type': 'vless',
		'server': server,
		'port': node_port,
		'uuid': password,
		'tls': True,
		'servername': domain,
		'skip-cert-verify': node.get('insecure') is True,
		'alpn': ['http/1.1'],
		'network': 'ws',
		'ws-opts': {'path': path, 'headers': {'Host': f'{host}:{node_port}', 'User-Agent': 'Go-http-client/1.1'}},
		'udp': False,
	}
	return {
		'mixed-port': port,
		'bind-address': '127.0.0.1',
		'allow-lan': False,
		'ipv6': False,
		'mode': 'rule',
		'log-level': 'warning',
		'proxies': [proxy],
		'proxy-groups': [{'name': 'CHECKIN', 'type': 'select', 'proxies': ['checkin-node']}],
		'rules': ['MATCH,CHECKIN'],
	}


def write_private_json(path: Path, data: dict) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as output:
		json.dump(data, output, ensure_ascii=False)
		output.write('\n')


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('--output', type=Path, required=True)
	parser.add_argument('--proof', type=Path)
	parser.add_argument('--port', type=int, default=7890)
	args = parser.parse_args()
	try:
		session = load_session(os.getenv('VEEE_SESSION_CONFIG', ''))
		requested_at = datetime.now(timezone.utc).isoformat()
		node = fetch_node(session)
		write_private_json(args.output, build_config(node, args.port))
		proof = {
			'nodeRequestedAtUtc': requested_at,
			'configWrittenAtUtc': datetime.now(timezone.utc).isoformat(),
			'freshNodeRequested': True,
			'serviceCode': 200,
			'transport': 'vless-ws-tls',
		}
		if args.proof:
			write_private_json(args.proof, proof)
		print(json.dumps(proof), flush=True)
		return 0
	except ProxySetupError as error:
		print(f'[FAILED] {error}', flush=True)
		return 1
	except OSError:
		print('[FAILED] Could not write the temporary proxy configuration', flush=True)
		return 1


if __name__ == '__main__':
	raise SystemExit(main())
