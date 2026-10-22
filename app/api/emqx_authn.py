"""EMQX HTTP 认证钩子（**候选**，生产未启用）。

为什么要这个文件：
现在生产 ACL 是按 clientid 收紧的，但 EMQX **没有开 authentication**，
任何客户端都能匿名连接、也能自称任意 clientid —— ACL 挡不住主动冒充。
补丁方向已确定：设备侧改成"用户名 + 口令"，EMQX 开 authn，
判据落在 `mqtt_credentials`（username ↔ clientid 一对一绑定）。

EMQX 支持把认证请求转发给一个 HTTP 服务。这个端点就是那个服务：
    POST /api/v1/emqx/authn   {"username":..,"password":..,"clientid":..}
    -> 200 {"result":"allow"} / {"result":"deny"}

判定逻辑不在本文件，而在 `app.services.mqtt_credential_service.authenticate`，
**和仓库自带的测试 broker 用同一份代码**，避免"测的和上线的是两套。"

【2026-10-22 夜间：未启用】
- 没有改 EMQX 运行配置 (`/etc/emqx/emqx.conf`)，没有 restart 任何服务。
- 启用步骤见 `docs/emqx-authn-enable.md`。
- 端点不吃 `get_current_user`（broker 不会带业务 token），改用共享密钥：
  未配置 `EMQX_AUTHN_TOKEN` 时直接 503，宁可不可用也不裸奔。
"""
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.services import mqtt_credential_service as svc

# 注意：注册时不挂 get_current_user 依赖（EMQX 拿不到业务 token）
router = APIRouter(tags=["EMQX authn hook（候选，未启用）"])

FAIL_OPEN_WARNING = "EMQX authn hook 未配置共享密钥，拒绝服务"


class AuthnRequest(BaseModel):
    username: str = ""
    password: str = ""
    clientid: str = ""
    peerhost: str = ""


@router.post("/emqx/authn")
async def emqx_authn(payload: AuthnRequest,
                     x_authn_token: str = Header("", alias="X-Authn-Token"),
                     db: AsyncSession = Depends(get_db)):
    token = getattr(settings, "EMQX_AUTHN_TOKEN", "") or ""
    if not token:
        # 没配密钥 = 谁都能问它允许不允许，直接不可用
        raise HTTPException(503, FAIL_OPEN_WARNING)
    if x_authn_token != token:
        return {"result": "deny"}
    ok, _reason = await svc.authenticate(
        db, username=payload.username, password=payload.password,
        clientid=payload.clientid)
    return {"result": "allow" if ok else "deny"}
