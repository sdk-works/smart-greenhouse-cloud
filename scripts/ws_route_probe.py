#!/usr/bin/env python3
"""部署演练里的 WS 路由运行时探针（真实握手，不查 openapi）。

背景：FastAPI 的 OpenAPI schema **不包含 WebSocket 路由**，所以
`curl openapi.json | grep '"/api/v1/ws/realtime"'` 这条检查永远匹配不到，
却因为输出空行而不被发现 —— 一个从不生效的检查。
这里改成真的连一次：不带 token 访问 /api/v1/ws/realtime，
服务端应当 accept 后 close(4401)。token 为空时 `_resolve_user` 直接返回 None，
**不查 redis**，所以本探针不依赖 6399 是否起过，可在演练实例上直接用。

退出码：0=路由已挂载且按 4401 拒绝未认证连接；1=不符合预期；2=连不上（路由可能没挂）。
"""
import asyncio
import sys

import websockets


async def probe(port: str) -> int:
    url = f"ws://127.0.0.1:{port}/api/v1/ws/realtime"
    try:
        ws = await websockets.connect(url, open_timeout=5)
    except Exception as e:                      # 连不上/被拒 = 路由大概率没挂上
        print(f"  连接失败：{type(e).__name__}: {e}")
        print("  判定：WS 路由未挂载（或实例未就绪）")
        return 2
    try:
        await asyncio.wait_for(ws.recv(), timeout=5.0)
        print("  连上了但服务端既没发数据也没关闭 —— 与预期不符")
        return 1
    except websockets.exceptions.ConnectionClosed as e:
        ok = e.code == 4401
        print(f"  close_code={e.code}（期望 4401）-> {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    except asyncio.TimeoutError:
        print("  5s 内既未关闭也未发数据 —— 与预期不符")
        return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：ws_route_probe.py <port>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(probe(sys.argv[1])))
