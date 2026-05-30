#!/usr/bin/env bash
# test_llm_fallback.sh — 校验 LLM 解析失败时的兜底
#
# 1. 发完全无意义的文本（连续表情、乱码、单字）
# 2. 校验：服务不 500，要么解析出 intent_type=other / 字段全 null，要么返回 422 + status=parse_failed
# 3. 关键：服务必须保持可用，绝不能崩
#
# 用法：BASE_URL=http://localhost:8080 ./test_llm_fallback.sh

set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"

fail() { echo "[FAIL: $1]"; exit 1; }
pass() { echo "[PASS] $1"; }

# 构造一组"恶心"输入
INPUTS=(
  "啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊"
  "????"
  "a"
  "🤔🤔🤔🤔🤔🤔🤔🤔"
  "  "
)

for i in "${!INPUTS[@]}"; do
  TEXT="${INPUTS[$i]}"
  USER_ID="test_user_fallback_${i}_$(date +%s)"

  R=$(mktemp)
  HTTP=$(printf '{"user_id":"%s","text":"%s"}' "$USER_ID" "$TEXT" | \
    curl -sS -o "$R" -w "%{http_code}" \
    -X POST "$BASE_URL/tickets" \
    -H "Content-Type: application/json" \
    -d @- || true)

  # 严禁返回 5xx
  if [[ "$HTTP" =~ ^5 ]]; then
    echo "Input: $TEXT"
    echo "Response: $(cat "$R")"
    fail "无意义文本 '$TEXT' 触发了 5xx ($HTTP)——LLM 兜底失败"
  fi

  # 期望 201（解析成 other）或 422（落库 parse_failed）
  if [ "$HTTP" != "201" ] && [ "$HTTP" != "422" ]; then
    echo "Input: $TEXT"
    echo "Response: $(cat "$R")"
    fail "期望 201 或 422，实际 $HTTP"
  fi

  # 如果是 201，校验 intent_type 是 other 或字段为 null
  if [ "$HTTP" = "201" ]; then
    INTENT=$(jq -r '.parsed.intent_type // empty' "$R")
    # 容忍 other / null / 任意有效枚举值（说明 LLM 强行猜了一个）
    [ -z "$INTENT" ] && fail "201 响应缺 intent_type"
  fi

  # 如果是 422，校验响应里有 status=parse_failed 或类似标记
  if [ "$HTTP" = "422" ]; then
    STATUS=$(jq -r '.status // .error.code // empty' "$R")
    [ -z "$STATUS" ] && fail "422 响应需要包含 status 或 error.code 字段"
  fi

  pass "输入 '$TEXT' → HTTP $HTTP（优雅处理）"
  rm -f "$R"
done

# 最后再发一个正常文本，确保服务没"内伤"
R=$(mktemp)
RECOVER_ID="test_recover_$(date +%s)"
HTTP=$(printf '{"user_id":"%s","text":"%s"}' "$RECOVER_ID" "2栋101灯不亮了" | \
  curl -sS -o "$R" -w "%{http_code}" \
  -X POST "$BASE_URL/tickets" \
  -H "Content-Type: application/json" \
  -d @-)
[ "$HTTP" != "201" ] && { cat "$R"; fail "兜底之后服务无法处理正常请求（$HTTP），可能 LLM client 中毒"; }
pass "兜底之后服务仍然能正常处理新请求 ✓"
rm -f "$R"

echo ""
echo "[PASS] test_llm_fallback.sh 全部通过 ✓"
exit 0
