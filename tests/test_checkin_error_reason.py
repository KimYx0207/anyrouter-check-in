#!/usr/bin/env python3
"""签到失败原因传递测试"""

import sys
from pathlib import Path
from types import SimpleNamespace

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from checkin import execute_check_in, parse_browser_check_in_response, parse_check_in_result


def test_parse_check_in_result_preserves_api_error_message():
	result = parse_check_in_result({'code': 1, 'message': 'cookie 已失效'}, 'AnyRouter主账号')

	assert result.success is False
	assert result.error == 'cookie 已失效'


def test_execute_check_in_preserves_http_error_status():
	class FakeClient:
		def post(self, url, headers, timeout):
			return SimpleNamespace(status_code=401, text='Unauthorized')

	provider_config = SimpleNamespace(domain='https://example.com', sign_in_path='/api/user/sign_in')

	result = execute_check_in(FakeClient(), 'AnyRouter主账号', provider_config, {})

	assert result.success is False
	assert result.error.startswith('HTTP 401')
	assert 'session 和 api_user' in result.error


def test_parse_browser_check_in_response_preserves_non_json_http_status():
	response = {'status': 401, 'contentType': 'text/html', 'text': 'Unauthorized'}

	result = parse_browser_check_in_response(response, 'AnyRouter主账号')

	assert result.success is False
	assert result.error.startswith('HTTP 401')
	assert 'session 和 api_user' in result.error
