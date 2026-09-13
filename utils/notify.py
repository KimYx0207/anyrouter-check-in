#!/usr/bin/env python3
"""通知模块 - 支持多渠道消息推送

修复问题：
1. 使用延迟初始化（不在模块级别创建实例）
2. Gotify token 使用 Header 而非 URL 参数
3. 所有 HTTP 请求检查响应状态码
4. 统一错误处理
"""

import os
import smtplib
from email.mime.text import MIMEText
from typing import Literal

import httpx

from utils.constants import (
	GOTIFY_PRIORITY_DEFAULT,
	GOTIFY_PRIORITY_MAX,
	GOTIFY_PRIORITY_MIN,
	HTTP_TIMEOUT_SECONDS,
)


class NotificationError(Exception):
	"""通知发送错误"""

	pass


class NotificationKit:
	"""多渠道通知管理器

	支持延迟初始化，在首次使用时才读取环境变量配置。
	"""

	_instance: 'NotificationKit | None' = None

	def __init__(self):
		# 延迟初始化标记，首次调用 push_message 时才加载配置
		self._config_loaded = False

	def _load_config(self) -> None:
		"""延迟加载配置"""
		if self._config_loaded:
			return

		self.email_user = os.getenv('EMAIL_USER', '')
		self.email_pass = os.getenv('EMAIL_PASS', '')
		self.email_to = os.getenv('EMAIL_TO', '')
		self.email_sender = os.getenv('EMAIL_SENDER', '')
		self.smtp_server = os.getenv('CUSTOM_SMTP_SERVER', '')
		self.pushplus_token = os.getenv('PUSHPLUS_TOKEN', '')
		self.server_push_key = os.getenv('SERVERPUSHKEY', '')
		self.dingding_webhook = os.getenv('DINGDING_WEBHOOK', '')
		self.feishu_webhook = os.getenv('FEISHU_WEBHOOK', '')
		self.weixin_webhook = os.getenv('WEIXIN_WEBHOOK', '')
		self.gotify_url = os.getenv('GOTIFY_URL', '')
		self.gotify_token = os.getenv('GOTIFY_TOKEN', '')
		self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
		self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
		self.bark_key = os.getenv('BARK_KEY', '')
		self.bark_server = os.getenv('BARK_SERVER', 'https://api.day.app')

		# Gotify 优先级
		gotify_priority_env = os.getenv('GOTIFY_PRIORITY', str(GOTIFY_PRIORITY_DEFAULT))
		try:
			priority = int(gotify_priority_env.strip()) if gotify_priority_env.strip() else GOTIFY_PRIORITY_DEFAULT
			self.gotify_priority = max(GOTIFY_PRIORITY_MIN, min(GOTIFY_PRIORITY_MAX, priority))
		except ValueError:
			self.gotify_priority = GOTIFY_PRIORITY_DEFAULT

		self._config_loaded = True

	def _check_response(self, response: httpx.Response, service_name: str) -> None:
		"""检查 HTTP 状态和上游新增的通知服务业务错误。"""
		if response.status_code >= 400:
			raise NotificationError(f'{service_name} 返回错误状态码: {response.status_code}')
		try:
			payload = response.json()
		except ValueError:
			return
		if not isinstance(payload, dict):
			return

		error_msg = payload.get('errmsg') or payload.get('message') or payload.get('msg') or payload.get('error')
		if payload.get('ok') is False:
			raise NotificationError(f'{service_name} 发送失败: {error_msg or payload.get("description") or "ok=false"}')
		for field, accepted in (
			('errcode', (None, 0)),
			('StatusCode', (None, 0)),
			('code', (None, 0, 200)),
			('ret', (None, 0, 1, 200)),
		):
			if payload.get(field) not in accepted:
				raise NotificationError(f'{service_name} 发送失败: {error_msg or payload[field]}')

	def send_email(self, title: str, content: str, msg_type: Literal['text', 'html'] = 'text') -> None:
		"""发送邮件通知"""
		self._load_config()

		if not self.email_user or not self.email_pass or not self.email_to:
			raise NotificationError('邮件配置不完整')

		mime_subtype = 'plain' if msg_type == 'text' else 'html'
		msg = MIMEText(content, mime_subtype, 'utf-8')
		sender = self.email_sender if self.email_sender else self.email_user
		msg['From'] = f'公益站签到助手 <{sender}>'
		msg['To'] = self.email_to
		msg['Subject'] = title

		smtp_server = self.smtp_server if self.smtp_server else f'smtp.{self.email_user.split("@")[1]}'

		try:
			with smtplib.SMTP_SSL(smtp_server, 465, timeout=HTTP_TIMEOUT_SECONDS) as server:
				server.login(self.email_user, self.email_pass)
				server.send_message(msg)
		except smtplib.SMTPException as e:
			raise NotificationError(f'邮件发送失败: {e}')

	def send_pushplus(self, title: str, content: str) -> None:
		"""发送 PushPlus 通知"""
		self._load_config()

		if not self.pushplus_token:
			raise NotificationError('PushPlus Token 未配置')

		data = {'token': self.pushplus_token, 'title': title, 'content': content, 'template': 'html'}

		with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
			response = client.post('http://www.pushplus.plus/send', json=data)
			self._check_response(response, 'PushPlus')

	def send_server_push(self, title: str, content: str) -> None:
		"""发送 Server酱 通知"""
		self._load_config()

		if not self.server_push_key:
			raise NotificationError('Server酱 Key 未配置')

		data = {'title': title, 'desp': content}

		with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
			response = client.post(f'https://sctapi.ftqq.com/{self.server_push_key}.send', json=data)
			self._check_response(response, 'Server酱')

	def send_dingtalk(self, title: str, content: str) -> None:
		"""发送钉钉通知"""
		self._load_config()

		if not self.dingding_webhook:
			raise NotificationError('钉钉 Webhook 未配置')

		data = {'msgtype': 'text', 'text': {'content': f'{title}\n{content}'}}

		with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
			response = client.post(self.dingding_webhook, json=data)
			self._check_response(response, '钉钉')

	def send_feishu(self, title: str, content: str) -> None:
		"""发送飞书通知"""
		self._load_config()

		if not self.feishu_webhook:
			raise NotificationError('飞书 Webhook 未配置')

		data = {
			'msg_type': 'interactive',
			'card': {
				'elements': [{'tag': 'markdown', 'content': content, 'text_align': 'left'}],
				'header': {'template': 'blue', 'title': {'content': title, 'tag': 'plain_text'}},
			},
		}

		with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
			response = client.post(self.feishu_webhook, json=data)
			self._check_response(response, '飞书')

	def send_wecom(self, title: str, content: str) -> None:
		"""发送企业微信通知"""
		self._load_config()

		if not self.weixin_webhook:
			raise NotificationError('企业微信 Webhook 未配置')

		data = {'msgtype': 'text', 'text': {'content': f'{title}\n{content}'}}

		with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
			response = client.post(self.weixin_webhook, json=data)
			self._check_response(response, '企业微信')

	def send_gotify(self, title: str, content: str) -> None:
		"""发送 Gotify 通知

		使用 Header 认证而非 URL 参数，更安全。
		"""
		self._load_config()

		if not self.gotify_url or not self.gotify_token:
			raise NotificationError('Gotify URL 或 Token 未配置')

		data = {'title': title, 'message': content, 'priority': self.gotify_priority}

		# 使用 Header 认证，避免 token 暴露在 URL 中
		headers = {'X-Gotify-Key': self.gotify_token}

		with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
			response = client.post(f'{self.gotify_url}/message', json=data, headers=headers)
			self._check_response(response, 'Gotify')

	def send_telegram(self, title: str, content: str) -> None:
		"""发送 Telegram 通知"""
		self._load_config()

		if not self.telegram_bot_token or not self.telegram_chat_id:
			raise NotificationError('Telegram Bot Token 或 Chat ID 未配置')

		message = f'<b>{title}</b>\n\n{content}'
		data = {'chat_id': self.telegram_chat_id, 'text': message, 'parse_mode': 'HTML'}
		url = f'https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage'

		with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
			response = client.post(url, json=data)
			self._check_response(response, 'Telegram')

	def send_bark(self, title: str, content: str) -> None:
		"""发送 Bark 通知"""
		self._load_config()

		if not self.bark_key:
			raise NotificationError('Bark Key 未配置')

		url = f'{self.bark_server.rstrip("/")}/push'
		data = {
			'device_key': self.bark_key,
			'title': title,
			'body': content,
			'icon': 'https://anyrouter.top/favicon.ico',
			'group': 'AnyRouter',
		}

		with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
			response = client.post(url, json=data)
			self._check_response(response, 'Bark')

	def push_message(self, title: str, content: str, msg_type: Literal['text', 'html'] = 'text') -> dict[str, bool]:
		"""推送消息到所有已配置的渠道

		Args:
		    title: 消息标题
		    content: 消息内容
		    msg_type: 消息类型 ('text' 或 'html')

		Returns:
		    每个渠道的发送结果 {渠道名: 是否成功}
		"""
		self._load_config()

		results: dict[str, bool] = {}

		notifications = [
			('Email', lambda: self.send_email(title, content, msg_type)),
			('PushPlus', lambda: self.send_pushplus(title, content)),
			('Server酱', lambda: self.send_server_push(title, content)),
			('钉钉', lambda: self.send_dingtalk(title, content)),
			('飞书', lambda: self.send_feishu(title, content)),
			('企业微信', lambda: self.send_wecom(title, content)),
			('Gotify', lambda: self.send_gotify(title, content)),
			('Telegram', lambda: self.send_telegram(title, content)),
			('Bark', lambda: self.send_bark(title, content)),
		]

		for name, func in notifications:
			try:
				func()
				print(f'[{name}]: 消息推送成功')
				results[name] = True
			except NotificationError as e:
				print(f'[{name}]: 消息推送失败 - {e}')
				results[name] = False
			except Exception as e:
				print(f'[{name}]: 消息推送异常 - {str(e)[:50]}')
				results[name] = False

		return results


def get_notifier() -> NotificationKit:
	"""获取通知管理器单例（延迟初始化）"""
	if NotificationKit._instance is None:
		NotificationKit._instance = NotificationKit()
	return NotificationKit._instance


# 兼容旧代码的别名
notify = get_notifier()
