#!/usr/bin/env bash
# test_idempotency.sh — 校验 5 分钟内重复提交的幂等性
#
# 1. 用同 user_id + 同文本提交两次
# 2. 校验：第一次 201、第二次 200、ticket_id 一致
# 3. 改文本后再发，应该建立新工单（ticket_id 不同）
#
# 用法：BASE_URL=http://localhost:8080 ./test_idempotency.sh

set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
USER_ID="test_user_idem_$(date +%s)"
TEXT="5栋301马桶堵了"

fail() { echo "[FAIL: $1]"; exit 1; }
pass() { echo "[PASS] $1"; }

# --- 第一次提交 ---
R1=$(mktemp); C1=$(mktemp)
HTTP1=$(curl -sS -o "$R1" -w "%{http_code}" \
  -X POST "$BASE_URL/tickets" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$USER_ID\", \"text\": \"$TEXT\"}")

[ "$HTTP1" != "201" ] && { cat "$R1"; fail "第一次提交期望 201，实际 $HTTP1"; }
TICKET1=$(jq -r '.ticket_id // empty' "$R1")
[ -z "$TICKET1" ] && fail "第一次响应缺 ticket_id"
pass "第一次提交：ticket_id=$TICKET1，HTTP=201"

# --- 第二次提交（同内容）---
sleep 1   # 给一点时间让幂等 key 写入 Redis
R2=$(mktemp)
HTTP2=$(curl -sS -o "$R2" -w "%{http_code}" \
  -X POST "$BASE_URL/tickets" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$USER_ID\", \"text\": \"$TEXT\"}")

if [ "$HTTP2" != "200" ]; then
  echo "Response body: $(cat "$R2")"
  fail "第二次提交期望 200（幂等命中），实际 $HTTP2"
fi

TICKET2=$(jq -r '.ticket_id // empty' "$R2")
[ "$TICKET2" != "$TICKET1" ] && fail "幂等返回的 ticket_id 不一致：第一次 $TICKET1，第二次 $TICKET2"
pass "第二次提交（同内容）：HTTP=200，ticket_id 一致 ✓"

# --- 第三次提交（改文本）---
TEXT3="5栋301水管漏了"
R3=$(mktemp)
HTTP3=$(curl -sS -o "$R3" -w "%{http_code}" \
  -X POST "$BASE_URL/tickets" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\": \"$USER_ID\", \"text\": \"$TEXT3\"}")

[ "$HTTP3" != "201" ] && { cat "$R3"; fail "第三次（不同文本）期望 201，实际 $HTTP3"; }
TICKET3=$(jq -r '.ticket_id // empty' "$R3")
[ "$TICKET3" = "$TICKET1" ] && fail "不同文本不应该幂等命中（误返回旧 ticket_id）"
pass "第三次提交（不同文本）：建立新工单 ticket_id=$TICKET3"

rm -f "$R1" "$R2" "$R3" "$C1"

echo ""
echo "[PASS] test_idempotency.sh 全部通过 ✓"
exit 0
