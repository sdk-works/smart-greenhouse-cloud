#!/usr/bin/env python3
"""T2 WS 候选运行时验收（协议级，非浏览器）。

在**测试库 + 独立 redis(6399)** 上起 ac133fa 实例，用 websockets 客户端实测 5 条语义：

  1. 无效 token        -> close 4401
  2. 账号无公司         -> close 4403
  3. A 公司遥测只推 A   -> A 收到，B 收不到
  4. 吊销 token 后空闲连接 -> <= WS_AUTH_RECHECK_SECONDS(+slack) 内被 4401 断开
  5. 复查查询异常        -> fail-closed，同样 4401 断开（本例用停掉 redis 制造异常）

用法：先起实例（见 ws_runtime_start.sh），再 `python3 ws_runtime_check.py`
"""
import asyncio
import json
import os
import subprocess
import sys
import time

import websockets

BASE = os.environ.get("WS_BASE", "ws://127.0.0.1:8011")
HTTP = BASE.replace("ws://", "http://")
RECHECK = float(os.environ.get("WS_AUTH_RECHECK_SECONDS", "2"))
SLACK = 3.0  # 复查周期 + 网络/调度余量

COMPANY_A = "00eb15e8-487c-4cc3-bb92-be7ce3a795c3"
COMPANY_B = "ceea68f0-9916-4cc2-a3b1-13a9d4abda3c"

results = []


def record(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n      {detail}")


def redis(*args):
    return subprocess.run(["redis-cli", "-p", "6399", *args],
                          capture_output=True, text=True).stdout.strip()


async def connect(token, timeout=8.0):
    """连上并返回 (ws, close_code|None)。accept 后若被服务端 close 可读到 code。"""
    url = f"{BASE}/api/v1/ws/realtime?token={token}" if token else f"{BASE}/api/v1/ws/realtime"
    ws = await websockets.connect(url, open_timeout=timeout)
    return ws


async def wait_close(ws, timeout):
    """等服务端主动关闭，返回 (code, elapsed)；超时返回 (None, elapsed)。"""
    t0 = time.time()
    try:
        await asyncio.wait_for(ws.recv(), timeout=timeout)
        # 收到数据而不是关闭 -> 继续等
        while True:
            await asyncio.wait_for(ws.recv(), timeout=max(0.1, timeout - (time.time() - t0)))
    except websockets.exceptions.ConnectionClosed as e:
        return (e.code, time.time() - t0)
    except asyncio.TimeoutError:
        return (None, time.time() - t0)


async def case_invalid_token():
    ws = await connect("not-a-real-token")
    code, el = await wait_close(ws, 5.0)
    record("1. 无效 token -> 4401", code == 4401,
           f"close_code={code} elapsed={el:.2f}s（期望 4401）")


async def case_no_company():
    ws = await connect("tok-noco")
    code, el = await wait_close(ws, 5.0)
    record("2. 账号无公司 -> 4403", code == 4403,
           f"close_code={code} elapsed={el:.2f}s（期望 4403）")


async def case_isolation():
    a = await connect("tok-a")
    b = await connect("tok-b")
    try:
        payload = json.dumps({"type": "telemetry", "device_id": "JY001",
                              "temperature": 25.7, "tag": "isolation-probe"})
        redis("publish", f"telemetry_updates:{COMPANY_A}", payload)
        got_a, got_b = None, None
        try:
            got_a = await asyncio.wait_for(a.recv(), timeout=4.0)
        except asyncio.TimeoutError:
            pass
        try:
            got_b = await asyncio.wait_for(b.recv(), timeout=4.0)
        except asyncio.TimeoutError:
            pass
        ok = got_a is not None and got_b is None
        record("3. A 公司遥测只推 A，B 收不到", ok,
               f"A 收到={got_a!r}\n      B 收到={got_b!r}（B 必须为 None）"
               f"\n      发布频道=telemetry_updates:{COMPANY_A}")
    finally:
        await a.close()
        await b.close()


async def case_revocation():
    redis("set", "auth:token:tok-a", "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa")
    ws = await connect("tok-a")
    # 先确认连接是活的
    try:
        await ws.send("ping")
        pong = await asyncio.wait_for(ws.recv(), timeout=3.0)
    except Exception as e:
        record("4. 吊销后空闲连接被 4401 断开", False, f"连接建立后不可用：{e}")
        return
    redis("del", "auth:token:tok-a")   # 吊销（不通知服务端，连接保持空闲）
    code, el = await wait_close(ws, RECHECK + SLACK)
    ok = code == 4401 and el <= RECHECK + SLACK
    record("4. 吊销后空闲连接被 4401 断开", ok,
           f"ping/pong={pong!r} -> 吊销 -> close_code={code} elapsed={el:.2f}s"
           f"（复查周期={RECHECK}s，允许上限={RECHECK + SLACK}s）")


async def case_fail_closed():
    """让复查查询抛异常（停掉独立 redis），验证 fail-closed 会断开而不是沿用旧权限。"""
    redis("set", "auth:token:tok-a", "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa")
    ws = await connect("tok-a")
    try:
        await ws.send("ping")
        await asyncio.wait_for(ws.recv(), timeout=3.0)
    except Exception as e:
        record("5. 复查异常 -> fail-closed 4401", False, f"连接不可用：{e}")
        return
    subprocess.run(["redis-cli", "-p", "6399", "shutdown", "nosave"],
                   capture_output=True, text=True)
    code, el = await wait_close(ws, RECHECK + SLACK + 3)
    ok = code == 4401
    record("5. 复查查询异常 -> fail-closed 4401", ok,
           f"停掉 redis(6399) 制造复查异常 -> close_code={code} elapsed={el:.2f}s"
           f"（期望 4401，即异常时按失效处理而非保持连接）")


async def main():
    print(f"WS 运行时验收  base={BASE}  WS_AUTH_RECHECK_SECONDS={RECHECK}")
    print("-" * 70)
    await case_invalid_token()
    await case_no_company()
    await case_isolation()
    await case_revocation()
    await case_fail_closed()
    print("-" * 70)
    failed = [n for n, ok, _ in results if not ok]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
