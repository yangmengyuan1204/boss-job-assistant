"""P5 地理模块异常定义。"""

from __future__ import annotations


class GeoError(Exception):
    """地理模块基础异常。"""


class ApiKeyMissingError(GeoError):
    """API Key 缺失异常。

    读取顺序：环境变量 → .env → 配置文件。
    三者均不存在时立即抛出，禁止使用默认 Key 或公共 Key。
    """


class GeocodeError(GeoError):
    """地理编码失败异常（API 返回异常、网络错误等）。"""


class GeocodeTimeoutError(GeoError):
    """地理编码超时异常。"""


__all__ = [
    "GeoError",
    "ApiKeyMissingError",
    "GeocodeError",
    "GeocodeTimeoutError",
]
