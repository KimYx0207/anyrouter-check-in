#!/usr/bin/env bash
# 通过 mihomo 拉取订阅、启动本地代理并探测可用节点。
# 环境变量:
#   VEEE_SESSION_CONFIG    已授权的 Veee 会话 JSON，每轮获取新节点
#   PROXY_SUBSCRIPTION_URL  可选订阅链接（未配置 Veee 时使用）
#   PROXY_TEST_URL          探测目标，默认 https://www.google.com/generate_204
#   PROXY_REQUIRED          true 时探测失败则退出 1
#   PROXY_PORT              本地 mixed-port，默认 7890

set -euo pipefail

if [[ -z "${PROXY_SUBSCRIPTION_URL:-}" && -z "${VEEE_SESSION_CONFIG:-}" ]]; then
	echo "[INFO] No proxy credentials configured, skip proxy setup"
	if [[ "${PROXY_REQUIRED:-false}" == "true" ]]; then
		exit 1
	fi
	exit 0
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY_DIR="${RUNNER_TEMP:-/tmp}/checkin-proxy"
PROXY_PORT="${PROXY_PORT:-7890}"
PROXY_TEST_URL="${PROXY_TEST_URL:-https://www.google.com/generate_204}"
MIHOMO_VERSION="${MIHOMO_VERSION:-v1.19.0}"
PROXY_REQUIRED="${PROXY_REQUIRED:-false}"
PROXY_TEST_JSON="${PROXY_TEST_JSON:-false}"

umask 077
mkdir -p "${PROXY_DIR}"
cd "${PROXY_DIR}"

echo "[INFO] Downloading mihomo ${MIHOMO_VERSION}..."
ARCHIVE="mihomo-linux-amd64-${MIHOMO_VERSION}.gz"
if ! curl --retry 3 --retry-delay 5 --retry-all-errors -fsSL -o "${ARCHIVE}" \
	"https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/${ARCHIVE}"; then
	echo "[WARN] Failed to download mihomo ${MIHOMO_VERSION}, skip proxy setup"
	if [[ "${PROXY_REQUIRED}" == "true" ]]; then
		exit 1
	fi
	exit 0
fi
gunzip -f "${ARCHIVE}"
chmod +x "mihomo-linux-amd64-${MIHOMO_VERSION}"
MIHOMO_BIN="${PROXY_DIR}/mihomo-linux-amd64-${MIHOMO_VERSION}"

if [[ -n "${VEEE_SESSION_CONFIG:-}" ]]; then
	if ! uv run --project "${PROJECT_DIR}" python "${PROJECT_DIR}/scripts/refresh_veee_proxy.py" \
		--output "${PROXY_DIR}/config.json" --proof "${PROXY_DIR}/proxy-proof.json" --port "${PROXY_PORT}"; then
		echo "[FAILED] Could not refresh the authorized Veee node"
		exit 1
	fi
	CONFIG_FILE=config.json
else
	CONFIG_FILE=config.yaml
	cat > config.yaml <<EOF
mixed-port: ${PROXY_PORT}
allow-lan: false
ipv6: false
mode: rule
log-level: warning
unified-delay: true

proxy-providers:
  subscription:
    type: http
    url: "${PROXY_SUBSCRIPTION_URL}"
    interval: 3600
    path: ./subscription.yaml
    health-check:
      enable: true
      interval: 300
      url: https://www.gstatic.com/generate_204

proxy-groups:
  - name: CHECKIN
    type: url-test
    url: "${PROXY_TEST_URL}"
    interval: 300
    tolerance: 150
    lazy: false
    use:
      - subscription

rules:
  - MATCH,CHECKIN
EOF
fi

echo "[INFO] Starting mihomo on 127.0.0.1:${PROXY_PORT}..."
nohup "${MIHOMO_BIN}" -d "${PROXY_DIR}" -f "${CONFIG_FILE}" > mihomo.log 2>&1 &
echo $! > mihomo.pid

PROXY_URL="http://127.0.0.1:${PROXY_PORT}"
READY=false
for attempt in $(seq 1 6); do
	if curl -fsS -x "${PROXY_URL}" --max-time 20 "${PROXY_TEST_URL}" -o health-response.json 2>/dev/null; then
		if [[ "${PROXY_TEST_JSON}" != "true" ]] || python -c \
			'import json,sys; value=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(value,dict) and value.get("success") is True else 1)' \
			health-response.json 2>/dev/null; then
			READY=true
			break
		fi
	fi
	echo "[INFO] Waiting for proxy health check (${attempt}/6)..."
	sleep 2
done

if [[ "${READY}" != "true" ]]; then
	echo "[FAILED] Proxy health check failed for ${PROXY_TEST_URL}"
	echo "[INFO] Proxy core logs remain private in the temporary runner directory"
	if [[ -f mihomo.pid ]]; then
		kill "$(cat mihomo.pid)" 2>/dev/null || true
	fi
	if [[ "${PROXY_REQUIRED}" == "true" ]]; then
		exit 1
	fi
	exit 0
fi

echo "[SUCCESS] Proxy is ready: ${PROXY_URL}"
if [[ "${PROXY_TEST_JSON}" == "true" ]]; then
	echo "[SUCCESS] Proxy target returned success=true JSON"
fi
echo "[INFO] Proxy is scoped to CHECKIN_PROXY_URL (browser/python only, not global HTTP_PROXY)"
if [[ -n "${GITHUB_ENV:-}" ]]; then
	echo "CHECKIN_PROXY_URL=${PROXY_URL}" >> "${GITHUB_ENV}"
fi
