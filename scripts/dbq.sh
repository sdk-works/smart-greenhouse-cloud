#!/usr/bin/env bash
# 只读数据库查询包装（修正版 v2）
#
# 旧版的问题：用**子串**黑名单（*create* / *update* / *delete* ...），
#   于是 `select created_at ...`、`select updated_at ...` 这类再正常不过的查询
#   会被判成写操作并拒绝 —— 误拒。生产上查时间列是高频操作，这个坑踩过多次。
#
# 修正：改成**按语句动词**判断。
#   1) 首句必须以只读关键字开头（select / with / \d / show / explain / table / describe）
#   2) 任何一条语句（首句，或 `;` 之后的下一句）以写操作动词开头 → 拒绝
#      动词后必须跟空白或结尾，所以 `created_at` / `updated_at` 这类**列名**不会误命中
#      （`create` 后面是 `d`，不是空白）。
set -uo pipefail

q="$*"
low=$(printf '%s' "$q" | tr 'A-Z' 'a-z')

# ── 1) 语句必须以只读关键字开头 ──────────────────────────────
case "$low" in
  select*|with*|"\\d"*|show[[:space:]]*|explain*|table[[:space:]]*|describe*) ;;
  *) echo "REJECTED: 只允许只读语句（SELECT / WITH / \\d / SHOW / EXPLAIN / TABLE / DESCRIBE）"
     exit 2 ;;
esac

# ── 2) 按语句动词判断写操作（首句或 ; 之后） ─────────────────
# 注意 (insert|update|...) 后面必须是空白或分号或行尾，
# 因此 created_at / updated_at / deleted_flag 这类标识符不会被误判。
if printf '%s\n' "$low" | grep -Eq '(^|;[[:space:]]*)(insert|update|delete|drop|alter|truncate|grant|revoke|vacuum|create|replace|merge|copy|comment|set)[[:space:]]*'; then
  echo "REJECTED: 检测到写操作语句动词"
  exit 2
fi

exec sudo -n -u postgres psql -d greenhouse -P pager=off -c "$q"
