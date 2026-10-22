"""测试用最小 MQTT broker（**只给测试，不是产品组件**）。

为什么自己写一个：
测试环境里没有任何 broker（没装 mosquitto，也不允许改生产 EMQX 配置、
不允许起第二个 EMQX 实例）。而设备入网闭环的验收要求真实跑一遍
"连接 → 上传 → 收到 ACK → 停用后连不上"，这条链路必须走真的 MQTT 报文，
否则测的是桩不是链路。

支持的范围故意做得很窄：
    CONNECT / CONNACK / SUBSCRIBE / SUBACK / PUBLISH(QoS0,1) / PUBACK
    / PINGREQ / PINGRESP / DISCONNECT
够验证鉴权与上下行，不够当真 broker 用 —— 所以它放在 tests/ 下，
永远不会被 main.py 加载。

认证：调用方注入 `authenticate` 协程，判定逻辑与生产钩子
(`app/api/emqx_authn.py`) 共用
`app.services.mqtt_credential_service.authenticate` 同一份实现。
"""
import asyncio
import logging
import socket
import struct

logger = logging.getLogger("mqtt_test_broker")

CONNECT, CONNACK, PUBLISH, PUBACK, PUBREC, PUBREL, PUBCOMP = 1, 2, 3, 4, 5, 6, 7
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK = 8, 9, 10, 11
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14


class TestBroker:
    """单实例、进程内、只在 127.0.0.1 监听的最小 MQTT broker。"""

    __test__ = False          # 别让 pytest 把它当成测试类收集

    def __init__(self, authenticate, on_publish=None):
        """
        authenticate: async (username, password, clientid) -> (bool, reason)
        on_publish  : async (topic, payload_bytes) -> None    上行回调，测试里接被测服务
        """
        self.authenticate = authenticate
        self.on_publish = on_publish
        self.server = None
        self.port = None
        self.subscribers = {}          # client_writer -> set(topics_filter_str)
        self.conn_results = []         # [(username, clientid, ok, reason)]
        self.sessions = {}             # clientid -> writer
        self.published = []            # [(topic, payload_bytes)]

    async def start(self):
        self.server = await asyncio.start_server(
            self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        logger.info(f"[test-broker] listening 127.0.0.1:{self.port}")
        return self.port

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    # ── 报文编解码 ────────────────────────────────────────────
    @staticmethod
    def _encode_remaining_length(n: int) -> bytes:
        out = b""
        while True:
            b = n % 128
            n //= 128
            if n > 0:
                b |= 0x80
            out += bytes([b])
            if n == 0:
                break
        return out

    @staticmethod
    def pack(ptype: int, flags: int, body: bytes) -> bytes:
        return (bytes([(ptype << 4) | flags])
                + TestBroker._encode_remaining_length(len(body)) + body)

    @staticmethod
    def _pack_str(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("!H", len(b)) + b

    # ── 连接处理 ──────────────────────────────────────────────
    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            clientid = ""
            while True:
                packet = await self._read_packet(reader)
                if packet is None:
                    break
                ptype, flags, body = packet
                if ptype == CONNECT:
                    ok, cid, username, password, rc = await self._handle_connect(
                        body, writer)
                    clientid = cid
                    writer.write(self.pack(CONNACK, 0, bytes([0, rc])))
                    await writer.drain()
                    if rc != 0:
                        break
                    self.sessions[cid] = writer
                elif ptype == PUBLISH:
                    await self._handle_publish(body, flags, writer)
                elif ptype == SUBSCRIBE:
                    packet_id = struct.unpack("!H", body[:2])[0]
                    i, returns = 2, []
                    topics = set()
                    while i < len(body):
                        ln = struct.unpack("!H", body[i:i + 2])[0]
                        topics.add(body[i + 2:i + 2 + ln].decode())
                        i += 2 + ln + 1
                    for _t in topics:
                        returns.append(0)          # 一律 QoS0 授予
                    self.subscribers[writer] = topics
                    writer.write(self.pack(SUBACK, 0,
                                           struct.pack("!H", packet_id)
                                           + bytes(returns)))
                    await writer.drain()
                elif ptype == PINGREQ:
                    writer.write(self.pack(PINGRESP, 0, b""))
                    await writer.drain()
                elif ptype == DISCONNECT:
                    break
                elif ptype == PUBREL:
                    writer.write(self.pack(PUBCOMP, 0, body[:2]))
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self.subscribers.pop(writer, None)
            for k, v in list(self.sessions.items()):
                if v is writer:
                    self.sessions.pop(k, None)
            try:
                writer.close()
            except Exception:
                pass

    async def _read_packet(self, reader):
        hdr = await reader.readexactly(1)
        (b0,) = hdr
        ptype, flags = b0 >> 4, b0 & 0x0F
        mult, val = 1, 0
        while True:
            (b,) = await reader.readexactly(1)
            val += (b & 0x7F) * mult
            if not (b & 0x80):
                break
            mult *= 128
        body = await reader.readexactly(val) if val else b""
        return ptype, flags, body

    async def _handle_connect(self, body, writer):
        i = 0
        plen = struct.unpack("!H", body[i:i + 2])[0]
        proto = body[i + 2:i + 2 + plen].decode()
        i += 2 + plen
        i += 1                                    # protocol level
        flags = body[i]
        i += 1                                    # connect flags
        i += 2                                    # keepalive
        cid_len = struct.unpack("!H", body[i:i + 2])[0]
        clientid = body[i + 2:i + 2 + cid_len].decode()
        i += 2 + cid_len

        username = password = ""
        if flags & 0x80:                          # user name flag
            ln = struct.unpack("!H", body[i:i + 2])[0]
            username = body[i + 2:i + 2 + ln].decode()
            i += 2 + ln
        if flags & 0x40:                          # password flag
            ln = struct.unpack("!H", body[i:i + 2])[0]
            password = body[i + 2:i + 2 + ln].decode()
            i += 2 + ln
        logger.debug(f"[test-broker] CONNECT proto={proto} cid={clientid} user={username}")
        ok, reason = await self.authenticate(username, password, clientid)
        self.conn_results.append((username, clientid, ok, reason))
        # MQTT 3.1.1: rc=0 接受；rc=5 未授权（3.1.1 里 4/5 都是用户名口令错）
        return ok, clientid, username, password, (0 if ok else 5)

    async def _handle_publish(self, body, flags, writer):
        ln = struct.unpack("!H", body[:2])[0]
        topic = body[2:2 + ln].decode()
        i = 2 + ln
        qos = (flags & 0x06) >> 1
        if qos > 0:
            i += 2                                # packet identifier
        payload = body[i:]
        self.published.append((topic, payload))
        if qos == 1:
            pid = struct.unpack("!H", body[2 + ln:4 + ln])[0]
            writer.write(self.pack(PUBACK, 0, struct.pack("!H", pid)))
            await writer.drain()
        if self.on_publish:
            await self.on_publish(topic, payload)
        await self.deliver(topic, payload)

    # ── 分发 ──────────────────────────────────────────────────
    @staticmethod
    def _matches(filt: str, topic: str) -> bool:
        f, t = filt.split("/"), topic.split("/")
        for i, part in enumerate(f):
            if part == "#":
                return True
            if part == "+":
                if i >= len(t):
                    return False
                continue
            if i >= len(t) or t[i] != part:
                return False
        return len(f) == len(t)

    async def deliver(self, topic: str, payload: bytes):
        body = self._pack_str(topic) + payload
        for w, topics in list(self.subscribers.items()):
            if any(self._matches(flt, topic) for flt in topics):
                try:
                    w.write(self.pack(PUBLISH, 0, body))
                    await w.drain()
                except Exception:
                    pass
