"""
config.py - 应用配置
修复:
  [#14] 使用 model_config = ConfigDict() 替代旧式 class Config（pydantic v2）
  [#15] CORS_ORIGINS 改为字符串类型，运行时手动解析，避免 .env 中 List 解析问题
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List
import json


class Settings(BaseSettings):
    APP_NAME: str = "Smart Greenhouse Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "SET_LOCALLY_DO_NOT_COMMIT"
    API_PREFIX: str = "/api/v1"

    # PostgreSQL
    DATABASE_URL: str = "postgresql+asyncpg://greenhouse:SET_LOCALLY_DO_NOT_COMMIT@localhost:5432/greenhouse"
    DATABASE_SYNC_URL: str = "postgresql://greenhouse:SET_LOCALLY_DO_NOT_COMMIT@localhost:5432/greenhouse"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # MQTT (EMQX)
    MQTT_BROKER_HOST: str = "localhost"
    MQTT_BROKER_PORT: int = 1883
    # EMQX HTTP 认证钩子的共享密钥（T3 候选）。
    # 空串 = 钩子不可用(authn 端点直接 503)，避免裸奔；启用时才配到 .env 里。
    EMQX_AUTHN_TOKEN: str = ""
    MQTT_USERNAME: str = "admin"
    MQTT_PASSWORD: str = "SET_LOCALLY_DO_NOT_COMMIT"
    MQTT_CLIENT_ID: str = "greenhouse-server"
    MQTT_TOPIC_PREFIX: str = "agri"

    # 图片上传本地存储
    UPLOAD_DIR: str = "/opt/greenhouse/uploads"
    UPLOAD_URL_PREFIX: str = "http://localhost:8000/uploads"

    # CORS 配置（支持 JSON 数组或逗号分隔字符串两种格式）
    CORS_ORIGINS: str = '["http://localhost:3000","http://localhost","http://localhost:80"]'

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v):
        """兼容 .env 中的 JSON 数组格式和逗号分隔格式"""
        if isinstance(v, list):
            return json.dumps(v)
        return v

    def get_cors_origins(self) -> List[str]:
        """获取解析后的 CORS 来源列表"""
        try:
            result = json.loads(self.CORS_ORIGINS)
            return result if isinstance(result, list) else [self.CORS_ORIGINS]
        except (json.JSONDecodeError, TypeError):
            return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore",  # 忽略 .env 中未定义的额外字段
    }


settings = Settings()
