#!/usr/bin/env bash
# test_basic.sh — 校验创建工单的主流程
#
# 1. POST /tickets 创建一条 "3栋402卫生间漏水" 工单
# 2. 校验响应：HTTP 201、ticket_id 存在、parsed 字段完整、assigned_to 有 staff_id
# 3. GET /tickets/{id} 查询单条，校验字段一致
#
# 用法：BASE_URL=http://localhost:8080 ./test_basic.sh

set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
USER_ID="test_user_basic_$(date +%s)"
TEXT="3栋402卫生间漏水严重，急"

fail() { echo "[FAIL: $1]"; exit 1; }
pass() { echo "[PASS] $1"; }

# --- 1. POST /tickets ---
RESP_FILE=$(mktemp)
HTTP_CODE=$(curl -sS -o "$RESP_FILE" -w "%{http_code}" \
  -X POST "$BASE_URL/tickets" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$USER_ID\", \"text\": \"$TEXT\"}") || fail "curl 请求失败，服务是否启动？"

if [ "$HTTP_CODE" != "201" ]; then
  echo "Response body: $(cat "$RESP_FILE")"
  fail "POST /tickets 期望 201，实际 $HTTP_CODE"
fi

# --- 2. 校验响应结构 ---
TICKET_ID=$(jq -r '.ticket_id // empty' "$RESP_FILE")
[ -z "$TICKET_ID" ] && fail "响应缺少 ticket_id"

PARSED=$(jq -r '.parsed // empty' "$RESP_FILE")
[ -z "$PARSED" ] && fail "响应缺少 parsed 对象"

INTENT=$(jq -r '.parsed.intent_type // empty' "$RESP_FILE")
[ -z "$INTENT" ] && fail "parsed.intent_type 为空"

URGENCY=$(jq -r '.parsed.urgency // empty' "$RESP_FILE")
[ -z "$URGENCY" ] && fail "parsed.urgency 为空"

ASSIGNED=$(jq -r '.assigned_to.staff_id // empty' "$RESP_FILE")
[ -z "$ASSIGNED" ] && fail "assigned_to.staff_id 为空"

NOTIF_SENT=$(jq -r '.notification.sent // empty' "$RESP_FILE")
[ "$NOTIF_SENT" != "true" ] && fail "notification.sent 不是 true，实际：$NOTIF_SENT"

pass "POST /tickets 创建成功：$TICKET_ID（intent=$INTENT, assigned=$ASSIGNED）"

# --- 3. GET /tickets/{id} ---
GET_FILE=$(mktemp)
GET_CODE=$(curl -sS -o "$GET_FILE" -w "%{http_code}" "$BASE_URL/tickets/$TICKET_ID")
[ "$GET_CODE" != "200" ] && fail "GET /tickets/$TICKET_ID 期望 200，实际 $GET_CODE"

GET_TICKET_ID=$(jq -r '.ticket_id // empty' "$GET_FILE")
[ "$GET_TICKET_ID" != "$TICKET_ID" ] && fail "GET 返回的 ticket_id 不一致：期望 $TICKET_ID，实际 $GET_TICKET_ID"

pass "GET /tickets/$TICKET_ID 查询成功"

# 清理
rm -f "$RESP_FILE" "$GET_FILE"

echo ""
echo "[PASS] test_basic.sh 全部通过 ✓"
exit 0
