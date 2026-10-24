# JY001 重新上电后的恢复验收清单（2026-10-24 夜间）

> 面向**明天（或任意一天）设备重新上电之后**。全部命令**只读**：生产库只 SELECT、
> `emqx ctl clients list` 只看、**不下发任何指令、不重启任何服务**。
> 本清单是为了避免重复 2026-10-23 那次的两个坑：
> ① 把"断电期间的空白"当成丢包算进分母；② 只看 EMQX `connected=true` 就宣布设备恢复。

## 0. 先知道现在的基线（上电前）

```
末条遥测：JY001/31/922  2026-10-23 08:54:18.978728+08
当前静默：~39 小时以上（见报告 §3 的实时 SQL）
EMQX：clientid=02501026081300004386 connected=true connected_at=2026-10-23 08:15:05
       —— 这是**上一次会话**的残留状态，keepalive=0 使它永不超时，**不代表设备在线**
```

## 1. 上电后等 3 分钟，再收第一条证据

```bash
# ① EMQX 会话是否刷新（connected_at 变成新时间 = DTU 重新拨号成功）
sudo -n emqx ctl clients list | grep 02501026081300004386

# ② 云端有没有新的上行（生产只读）
sudo -n -u postgres psql -d greenhouse -P pager=off -c \
  "select msg_id, created_at, temperature, humidity, co2_level
     from telemetry order by created_at desc limit 5;"
```

**判据**

| 现象 | 说明 |
|---|---|
| `connected_at` 刷新 + 有遥测 | 通信已恢复，继续 §2 |
| `connected_at` 刷新但**没有**遥测 | DTU 上电了、MCU 没发数据 → 查 MCU/串口侧，**不要再往"A 供电"上靠** |
| 两者都没变 | DTU 没连上 → 查天线/SIM/信号；此刻 `connected=true` 依然没有意义 |

## 2. 跑恢复验收（不是人工看两条数据就下结论）

```bash
cd /opt/greenhouse-nightly/worktree
/opt/greenhouse/venv/bin/python scripts/verify_recovery.py \
  --db-url "$(grep -E '^DATABASE_SYNC_URL=' /opt/greenhouse/.env | cut -d= -f2-)" \
  --device JY001 --outage-start '2026-10-23T08:54:18+08:00' --period 60
```

它验四件事，任一不满足就**不要宣布"恢复正常"**：

| 检查 | 期望 | 不满足时说明什么 |
|---|---|---|
| 新 `boot_id`（31 → 新值） | ✅ | 仍是 31 → MCU 没重启：要么没真断电，要么只是通信恢复，要复查本次维护记录 |
| `seq` 从 1 开始 | ✅ | 没重置 → 与上一条互证"没重启" |
| 恢复后 `seq` 连续 | 无跳号、无重复 | 跳号 = 补传或丢包；连续两次以上要查链路 |
| 上传节律中位数 ≈ 60s | 落在 45~75s 的比例应很高 | 节律乱 → 设备在补传，或时钟没同步好 |

口径（脚本会在输出里显式写出，别改）：

> **断电窗口整体排除**：设备没采样 → 那几千条从来没有存在过，**不推算应达条数，丢包固定记 0**，
> 不计入到报率 / 完整率的分母。

## 3. 用三态监测确认"已恢复"，并关掉维护窗口

```bash
/opt/greenhouse/venv/bin/python scripts/outage_watch.py \
  --db-url "$(grep -E '^DATABASE_SYNC_URL=' /opt/greenhouse/.env | cut -d= -f2-)" \
  --device JY001 --period 60
```

- 期望输出 `当前状态：正常运行`（配合"设备离线=False"）。
- 若仍显示`计划停机`：说明维护窗口 `end` 还没到 —— **把窗口 `end` 改成实际恢复时刻**，
  否则这段时间的**真实故障会被继续当成计划停机而静默**：

```bash
# 把 mw-20261024142159-JY001 的 end 改成实际恢复时刻（UTC 带时区写法）
/opt/greenhouse/venv/bin/python - <<'PY'
import json
p = "/opt/greenhouse-nightly/logs/maintenance.json"
d = json.load(open(p))
for w in d["windows"]:
    if w["id"] == "mw-20261024142159-JY001":
        w["end"] = "2026-10-25T10:00:00+08:00"   # ← 改成你的实际恢复时刻
        w["note"] = (w.get("note","") + "；恢复后已按实际关窗").strip("；")
json.dump(d, open(p,"w"), ensure_ascii=False, indent=2)
PY
```

> ⚠ 现在窗口 `end` 是 **2026-10-26 18:00**，那是白天登记时的**临时估计值**，不是约定停机时长。
> 到期自动升级为`计划停机超期未恢复`（会要求通知人），但在此之前**任何真实故障都不会报出来**。

## 4. 人工核对的两个展示口径（不要只看脚本）

1. `devices.status=ONLINE` 是**持久字段**，`last_seen` 已过期时页面仍显示"在线"。
   前端展示设备在线与否，必须改成「`last_seen` 在 N 个记录周期内才算在线」
   —— 候选实现在夜间分支的 T4 状态接口里（**未上生产**）。
2. EMQX 的 `connected` 与"有数据"要**分两栏**显示：`connected` 只是"没收到过 FIN"，
   `keepalive=0` 时它永远为真（见 `docs/outage-triage-20261023.md` §3）。

## 5. 恢复后建议顺手补的一件事

把凌晨 23:00 那份观测报告（`/opt/greenhouse-nightly/report-observe-*.md`）里的
"断流次数 / 缺口 / 补传"与 `verify_recovery.py` 的输出对齐一次。二者口径不同
（观测器按"列队是否有数据"计，脚本显式排除断电窗口），**以 `verify_recovery.py` 为准**，
不一致时按 §2 的口径重算，不要直接采信观测器里的"缺口"。
