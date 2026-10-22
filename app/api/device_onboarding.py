"""设备入网与凭据管理（T3）。

接口都挂在**现有设备详情**之下：`/devices/{device_id}/credential*`，
不新增"凭据管理"这种独立菜单，避免同一个东西两处登记。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET    | /devices/{id}/credential             | 凭据摘要（永远不含口令） |
| POST   | /devices/{id}/credential             | 登记并生成凭据（**明文口令只返回这一次**） |
| POST   | /devices/{id}/credential/rotate      | 换密（旧口令立刻失效，**新明文只返回这一次**） |
| PATCH  | /devices/{id}/credential/enabled     | 停用 / 重新启用 |
| GET    | /devices/{id}/credential/install-sheet | 安装人员配置清单（json / text 两种格式，可直接打印） |

约束：
- 写操作要 `manage_robots` 权限；读凭据摘要也要登录（口令是设备入网凭证，不能匿名看）。
- 设备归属：业务用户只能操作本公司大棚下的设备，跨公司一律 404。
- 每次写都落 `audit_logs`，明细里**只有口令指纹，没有明文**。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.api.auth import check_permission, get_current_user
from app.core.audit import record_cross_company_access
from app.core.company_scope import can_access_company, is_platform
from app.core.config import settings
from app.core.database import get_db
from app.models.models import Device, Greenhouse, User
from app.services import mqtt_credential_service as svc

router = APIRouter(tags=["DeviceOnboarding 设备入网"])


class CredentialCreateIn(BaseModel):
    asset_no: str = Field(..., min_length=1, max_length=64,
                          description="DTU 资产编号（设备壳上的号）")
    clientid: str = Field(..., min_length=1, max_length=100,
                          description="设备模块序列号，作为唯一允许的 Client ID")
    username: Optional[str] = Field(None, max_length=100)
    note: Optional[str] = None


class EnabledIn(BaseModel):
    enabled: bool
    reason: Optional[str] = None


async def _load_device(db: AsyncSession, device_id: str) -> Device:
    """device_id 既可能是主键 UUID，也可能是业务 device_id（如 JY001）。

    现场安装人员手上拿到的是后者，所以两种都要能查到，否则这个页面没法用。
    """
    r = await db.execute(select(Device).where(Device.id == device_id))
    dev = r.scalar_one_or_none()
    if dev is None:
        r = await db.execute(select(Device).where(Device.device_id == device_id))
        dev = r.scalar_one_or_none()
    if dev is None:
        raise HTTPException(404, "设备不存在")
    return dev


async def _guard(db: AsyncSession, user: User, dev: Device) -> Optional[str]:
    """公司归属校验（跨公司对业务用户 404，不泄漏存在性）。返回所属 company_id。"""
    gh = await db.get(Greenhouse, dev.greenhouse_id) if dev.greenhouse_id else None
    cid = gh.company_id if gh else None
    # 业务用户没有访问权限时统一 404（不泄漏设备是否存在）
    if cid and not is_platform(user) and not can_access_company(user, cid):
        raise HTTPException(404, "设备不存在")
    # 平台运维跨公司查看凭据要留痕
    if is_platform(user) and cid:
        await record_cross_company_access(
            db, user, cid, "device_credential", dev.id)
    return cid


async def _cred_or_404(db: AsyncSession, dev: Device):
    cred = await svc.get_by_device(db, dev.device_id)
    if cred is None:
        raise HTTPException(404, "该设备尚未登记凭据")
    return cred


@router.get("/devices/{device_id}/credential")
async def get_credential(device_id: str,
                         user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    dev = await _load_device(db, device_id)
    await _guard(db, user, dev)
    cred = await svc.get_by_device(db, dev.device_id)
    return {"has_credential": cred is not None,
            "credential": svc.public_view(cred) if cred else None}


@router.post("/devices/{device_id}/credential", status_code=201)
async def create_credential(device_id: str, payload: CredentialCreateIn,
                            user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_robots")
    dev = await _load_device(db, device_id)
    await _guard(db, user, dev)
    existing = await svc.get_by_device(db, dev.device_id)
    if existing is not None:
        raise HTTPException(409, "该设备已有凭据；需要重置请走 /credential/rotate")
    try:
        cred, plain = await svc.create_credential(
            db, device=dev, asset_no=payload.asset_no,
            clientid=payload.clientid, username=payload.username,
            note=payload.note, actor=user)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {
        "credential": svc.public_view(cred),
        # 唯一一次能看到明文的地方；前端必须提示"刷新后不可再找回"
        "password": plain,
        "password_shown_once": True,
        "broker": {"host": settings.MQTT_BROKER_HOST, "port": settings.MQTT_BROKER_PORT},
    }


@router.post("/devices/{device_id}/credential/rotate")
async def rotate_credential(device_id: str,
                            user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_robots")
    dev = await _load_device(db, device_id)
    await _guard(db, user, dev)
    cred = await _cred_or_404(db, dev)
    plain = await svc.rotate_secret(db, cred=cred, actor=user)
    return {"credential": svc.public_view(cred),
            "password": plain,
            "password_shown_once": True,
            "old_password_invalid": True}


@router.patch("/devices/{device_id}/credential/enabled")
async def set_credential_enabled(device_id: str, payload: EnabledIn,
                                 user: User = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_robots")
    dev = await _load_device(db, device_id)
    await _guard(db, user, dev)
    cred = await _cred_or_404(db, dev)
    cred = await svc.set_enabled(db, cred=cred, enabled=payload.enabled,
                                 actor=user, reason=payload.reason)
    return {"credential": svc.public_view(cred),
            "message": "凭据已启用" if payload.enabled else "凭据已停用，设备将无法再次连接"}


@router.get("/devices/{device_id}/credential/install-sheet")
async def install_sheet(device_id: str,
                        fmt: str = Query("json", pattern="^(json|text)$"),
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """安装人员可用的配置清单。

    fmt=text 返回可直接打印/导出的纯文本；fmt=json 返回结构化，便于页面渲染。
    **清单里不含口令**——口令只在创建/换密那一次给过；要对现网设备重新下发口令，
    必须走 /credential/rotate 重新生成（并重新打印清单）。
    """
    dev = await _load_device(db, device_id)
    cid = await _guard(db, user, dev)
    cred = await svc.get_by_device(db, dev.device_id)
    gh = await db.get(Greenhouse, dev.greenhouse_id) if dev.greenhouse_id else None

    topics = {
        "uplink":   f"agri/device/{dev.device_id}/up",
        "ack":      f"agri/device/{dev.device_id}/ack",
        "command":  f"agri/device/{dev.device_id}/command",
        "config":   f"agri/device/{dev.device_id}/config",
    }
    data = {
        "device": {
            "device_id":      dev.device_id,
            "name":           dev.name,
            "asset_no":       cred.asset_no if cred else None,
            "greenhouse":     gh.name if gh else None,
            "greenhouse_id":  gh.id if gh else None,
            "company_id":     cid,
        },
        "mqtt": {
            "broker_host": settings.MQTT_BROKER_HOST,
            "broker_port": settings.MQTT_BROKER_PORT,
            "username":    cred.username if cred else None,
            "clientid":    cred.clientid if cred else None,
            "password":    "〈创建/换密时现场抄录，系统不再保存明文〉",
            "topics":      topics,
            "qos":         {"uplink": 0, "ack": 0, "command": 1, "config": 1},
        },
        "credential_state": {
            "registered": cred is not None,
            "enabled":    cred.enabled if cred else None,
            "rotation_count": cred.rotation_count if cred else 0,
        },
        "checklist": [
            "1. 核对 Client ID 与 DTU 模块序列号一致（不一致会被 broker 拒绝）",
            "2. 用户名/口令按清单填写，口令区分大小写，录入后请二次复核",
            "3. 上行主题必须严格等于 uplink 一项，不要填通配符",
            "4. 只订阅本机的 ack/command/config，不要订阅其它设备",
            "5. 装完在详情页确认「最近收到有效数据」时间开始走动",
        ],
    }
    if fmt == "json":
        return data

    lines = [
        "设备入网安装配置清单",
        "=" * 46,
        f"设备编号(device_id)：{data['device']['device_id']}",
        f"设备名称          ：{data['device']['name']}",
        f"DTU 资产编号      ：{data['device']['asset_no'] or '（未登记）'}",
        f"所属大棚          ：{data['device']['greenhouse'] or '（未绑定）'}",
        "",
        "MQTT 连接参数",
        "-" * 46,
        f"服务器地址        ：{data['mqtt']['broker_host']}",
        f"端口              ：{data['mqtt']['broker_port']}",
        f"Client ID         ：{data['mqtt']['clientid'] or '（未登记凭据）'}",
        f"用户名            ：{data['mqtt']['username'] or '（未登记凭据）'}",
        f"口令              ：{data['mqtt']['password']}",
        "",
        "主题",
        "-" * 46,
        f"上行              ：{topics['uplink']}  (QoS 0)",
        f"回执订阅          ：{topics['ack']}",
        f"命令订阅          ：{topics['command']}  (QoS 1)",
        f"配置订阅          ：{topics['config']}  (QoS 1)",
        "",
        "安装检查项",
        "-" * 46,
    ]
    lines += data["checklist"]
    lines += ["", f"凭据状态：{'已登记 / ' + ('启用' if data['credential_state']['enabled'] else '停用') if data['credential_state']['registered'] else '未登记'}",
              f"换密次数：{data['credential_state']['rotation_count']}"]
    return {"format": "text", "content": "\n".join(lines)}
