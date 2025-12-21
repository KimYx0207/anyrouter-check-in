#!/usr/bin/env python3
"""常量定义"""

# 余额计算因子（API 返回的原始值需要除以此值得到美元）
QUOTA_DIVISOR = 500000

# 签到冷却时间（小时）
SIGNIN_COOLDOWN_HOURS = 24

# 余额 Hash 截取长度（用于变化检测）
BALANCE_HASH_LENGTH = 16

# 浏览器等待时间（毫秒）
PAGE_LOAD_WAIT_MS = 3000
SIGNIN_TRIGGER_WAIT_MS = 15000
COOKIE_SET_WAIT_MS = 1000

# HTTP 请求超时（秒）
HTTP_TIMEOUT_SECONDS = 30

# 并发处理限制
MAX_CONCURRENT_ACCOUNTS = 3  # 最大并行处理账号数

# 文件路径（运行时数据统一存放在 data/ 目录）
DATA_DIR = 'data'
BALANCE_HASH_FILE = f'{DATA_DIR}/balance_hash.txt'
SIGNIN_HISTORY_FILE = f'{DATA_DIR}/signin_history.json'
LOG_FILE = f'{DATA_DIR}/task_run.log'
DATABASE_FILE = f'{DATA_DIR}/checkin.db'

# Gotify 优先级范围
GOTIFY_PRIORITY_MIN = 1
GOTIFY_PRIORITY_MAX = 10
GOTIFY_PRIORITY_DEFAULT = 9

# Chrome User-Agent
CHROME_USER_AGENT = (
	'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
	'AppleWebKit/537.36 (KHTML, like Gecko) '
	'Chrome/138.0.0.0 Safari/537.36'
)

# Playwright 浏览器启动参数（用于 stealth 模式）
BROWSER_ARGS = [
	'--disable-blink-features=AutomationControlled',
	'--disable-dev-shm-usage',
	'--disable-web-security',
	'--disable-features=VizDisplayCompositor',
	'--no-sandbox',
]
