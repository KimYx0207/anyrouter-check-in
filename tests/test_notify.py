#!/usr/bin/env python3
"""通知模块测试"""

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# 添加项目根目录到 PATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.notify import NotificationError, NotificationKit


@pytest.fixture
def mock_env():
	"""设置测试环境变量"""
	env_backup = os.environ.copy()

	os.environ['EMAIL_USER'] = 'test@example.com'
	os.environ['EMAIL_PASS'] = 'test_password'
	os.environ['EMAIL_TO'] = 'recipient@example.com'
	os.environ['PUSHPLUS_TOKEN'] = 'test_pushplus_token'
	os.environ['SERVERPUSHKEY'] = 'test_server_key'
	os.environ['DINGDING_WEBHOOK'] = 'https://oapi.dingtalk.com/robot/send?access_token=test'
	os.environ['FEISHU_WEBHOOK'] = 'https://open.feishu.cn/open-apis/bot/v2/hook/test'
	os.environ['WEIXIN_WEBHOOK'] = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test'
	os.environ['GOTIFY_URL'] = 'https://gotify.example.com'
	os.environ['GOTIFY_TOKEN'] = 'test_gotify_token'
	os.environ['TELEGRAM_BOT_TOKEN'] = 'test_bot_token'
	os.environ['TELEGRAM_CHAT_ID'] = '123456789'

	yield

	os.environ.clear()
	os.environ.update(env_backup)


@pytest.fixture
def notification_kit(mock_env):
	"""创建带 mock 环境变量的 NotificationKit"""
	kit = NotificationKit()
	kit._config_loaded = False  # 强制重新加载配置
	return kit


def test_real_notification(notification_kit):
	"""真实接口测试，需要配置.env.local文件"""
	if os.getenv('ENABLE_REAL_TEST') != 'true':
		pytest.skip('未启用真实接口测试')

	notification_kit.push_message(
		'测试消息', f'这是一条测试消息\n发送时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
	)


@patch('utils.notify.smtplib.SMTP_SSL')
def test_send_email(mock_smtp, notification_kit):
	"""测试邮件发送"""
	mock_server = MagicMock()
	mock_smtp.return_value.__enter__.return_value = mock_server

	notification_kit.send_email('测试标题', '测试内容')

	assert mock_server.login.called
	assert mock_server.send_message.called


@patch('utils.notify.httpx.Client')
def test_send_pushplus(mock_client_class, notification_kit):
	"""测试 PushPlus 发送"""
	mock_client = MagicMock()
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_client.post.return_value = mock_response
	mock_client_class.return_value.__enter__.return_value = mock_client

	notification_kit.send_pushplus('测试标题', '测试内容')

	mock_client.post.assert_called_once()
	call_args = mock_client.post.call_args
	assert 'pushplus.plus' in call_args[0][0]


@patch('utils.notify.httpx.Client')
def test_send_dingtalk(mock_client_class, notification_kit):
	"""测试钉钉发送"""
	mock_client = MagicMock()
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_client.post.return_value = mock_response
	mock_client_class.return_value.__enter__.return_value = mock_client

	notification_kit.send_dingtalk('测试标题', '测试内容')

	mock_client.post.assert_called_once()
	call_args = mock_client.post.call_args
	assert 'dingtalk' in call_args[0][0]


@patch('utils.notify.httpx.Client')
def test_send_feishu(mock_client_class, notification_kit):
	"""测试飞书发送"""
	mock_client = MagicMock()
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_client.post.return_value = mock_response
	mock_client_class.return_value.__enter__.return_value = mock_client

	notification_kit.send_feishu('测试标题', '测试内容')

	mock_client.post.assert_called_once()
	call_args = mock_client.post.call_args
	assert 'card' in str(call_args)


@patch('utils.notify.httpx.Client')
def test_send_wecom(mock_client_class, notification_kit):
	"""测试企业微信发送"""
	mock_client = MagicMock()
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_client.post.return_value = mock_response
	mock_client_class.return_value.__enter__.return_value = mock_client

	notification_kit.send_wecom('测试标题', '测试内容')

	mock_client.post.assert_called_once()


@patch('utils.notify.httpx.Client')
def test_send_gotify(mock_client_class, notification_kit):
	"""测试 Gotify 发送（使用 Header 认证）"""
	mock_client = MagicMock()
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_client.post.return_value = mock_response
	mock_client_class.return_value.__enter__.return_value = mock_client

	notification_kit.send_gotify('测试标题', '测试内容')

	mock_client.post.assert_called_once()
	call_args = mock_client.post.call_args

	# 验证使用 Header 认证而非 URL 参数
	assert 'headers' in call_args.kwargs
	assert 'X-Gotify-Key' in call_args.kwargs['headers']


@patch('utils.notify.httpx.Client')
def test_send_telegram(mock_client_class, notification_kit):
	"""测试 Telegram 发送"""
	mock_client = MagicMock()
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_client.post.return_value = mock_response
	mock_client_class.return_value.__enter__.return_value = mock_client

	notification_kit.send_telegram('测试标题', '测试内容')

	mock_client.post.assert_called_once()
	call_args = mock_client.post.call_args
	assert 'telegram' in call_args[0][0]


def test_missing_config():
	"""测试缺少配置时抛出 NotificationError"""
	# 清除环境变量
	env_backup = os.environ.copy()
	os.environ.clear()

	try:
		kit = NotificationKit()
		kit._config_loaded = False

		with pytest.raises(NotificationError, match='邮件配置不完整'):
			kit.send_email('测试', '测试')

		with pytest.raises(NotificationError, match='PushPlus Token 未配置'):
			kit.send_pushplus('测试', '测试')

		with pytest.raises(NotificationError, match='Gotify URL 或 Token 未配置'):
			kit.send_gotify('测试', '测试')
	finally:
		os.environ.clear()
		os.environ.update(env_backup)


@patch('utils.notify.httpx.Client')
@patch('utils.notify.smtplib.SMTP_SSL')
def test_push_message(mock_smtp, mock_client_class, notification_kit):
	"""测试批量推送"""
	# Mock SMTP
	mock_server = MagicMock()
	mock_smtp.return_value.__enter__.return_value = mock_server

	# Mock HTTP Client
	mock_client = MagicMock()
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_client.post.return_value = mock_response
	mock_client_class.return_value.__enter__.return_value = mock_client

	results = notification_kit.push_message('测试标题', '测试内容')

	# 验证返回结果是字典
	assert isinstance(results, dict)

	# 验证所有渠道都有结果
	expected_channels = ['Email', 'PushPlus', 'Server酱', '钉钉', '飞书', '企业微信', 'Gotify', 'Telegram']
	for channel in expected_channels:
		assert channel in results


@patch('utils.notify.httpx.Client')
def test_response_check(mock_client_class, notification_kit):
	"""测试 HTTP 响应状态检查"""
	mock_client = MagicMock()
	mock_response = MagicMock()
	mock_response.status_code = 500  # 模拟服务器错误
	mock_client.post.return_value = mock_response
	mock_client_class.return_value.__enter__.return_value = mock_client

	with pytest.raises(NotificationError, match='返回错误状态码'):
		notification_kit.send_pushplus('测试标题', '测试内容')


def test_lazy_initialization():
	"""测试延迟初始化"""
	kit = NotificationKit()

	# 初始化时不应该加载配置
	assert kit._config_loaded is False

	# 设置环境变量
	os.environ['PUSHPLUS_TOKEN'] = 'test_token'

	try:
		# 调用方法后应该加载配置
		kit._load_config()
		assert kit._config_loaded is True
		assert kit.pushplus_token == 'test_token'
	finally:
		del os.environ['PUSHPLUS_TOKEN']


@pytest.mark.parametrize(
	'payload',
	[
		{'ok': False, 'description': 'test failure'},
		{'errcode': 40001, 'errmsg': 'test failure'},
		{'StatusCode': 1, 'message': 'test failure'},
		{'code': 500, 'msg': 'test failure'},
		{'ret': 100, 'error': 'test failure'},
	],
)
def test_notification_business_errors_are_reported(notification_kit, payload):
	response = httpx.Response(200, json=payload)

	with pytest.raises(NotificationError, match='test failure'):
		notification_kit._check_response(response, 'Test')
