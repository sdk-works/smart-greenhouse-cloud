#!/usr/bin/env bash
# 智慧大棚平台数据库备份
# 用法：/opt/greenhouse/scripts/backup_db.sh
# 建议加入 crontab：0 3 * * * /opt/greenhouse/scripts/backup_db.sh
set -euo pipefail

BACKUP_DIR="/opt/greenhouse/backups"
KEEP_DAYS=14
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/greenhouse-${STAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

# pg_dump 以 postgres 身份执行，避免在命令行传入密码
sudo -n -u postgres pg_dump greenhouse | gzip > "${OUT}"

# 校验：文件非空且 gzip 完整
if [ ! -s "${OUT}" ] || ! gzip -t "${OUT}" 2>/dev/null; then
    echo "[FAIL] 备份校验失败: ${OUT}" >&2
    rm -f "${OUT}"
    exit 1
fi

# 清理过期备份
find "${BACKUP_DIR}" -name 'greenhouse-*.sql.gz' -type f -mtime +${KEEP_DAYS} -delete

echo "[OK] $(date '+%F %T') 备份完成: ${OUT} ($(du -h "${OUT}" | cut -f1))"
