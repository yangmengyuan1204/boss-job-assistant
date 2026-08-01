"""P5 高德地图 Web Service API 地理编码客户端。

约束：
- API Key 读取顺序：环境变量 → .env → 配置文件
- 三者均不存在 → 立即抛 ApiKeyMissingError，禁止默认 Key / 公共 Key
- API Key 不得写入源码 / 日志 / Git
- 一次运行同一地址最多请求一次 API（内存 Set 跟踪）
- API 超时安全失败，最大重试 2 次（含首次）
- 不记录 API Key、HTTP Header、Token、Cookie

依赖：仅使用标准库 urllib（不引入 requests），避免新增项目依赖。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from boss_tool.geo.exceptions import ApiKeyMissingError, GeocodeError, GeocodeTimeoutError
from boss_tool.logging_config import get_logger

logger = get_logger(__name__)

# ==================== 常量 ====================
# API Key 环境变量名
ENV_KEY_NAME = "AMAP_API_KEY"

# .env 文件名（项目根）
DOTENV_NAME = ".env"

# 配置文件名（config/ 目录，需在 .gitignore 中）
CONFIG_FILE_NAME = "geo.local.yaml"

# 配置文件中 Key 的字段名
CONFIG_KEY_FIELD = "amap_api_key"

# 高德地理编码 API 端点
AMAP_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"

# API 超时（秒）
DEFAULT_TIMEOUT_SECONDS = 5.0

# 最大重试次数（含首次，共 2 次请求）
MAX_ATTEMPTS = 2

# 高德 API 成功状态码
AMAP_STATUS_SUCCESS = "1"


@dataclass(frozen=True)
class GeocodeResult:
    """地理编码结果。

    Attributes:
        formatted_address: 标准化后的地址（API 返回）
        longitude: 经度
        latitude: 纬度
    """

    formatted_address: str
    longitude: float
    latitude: float


def _read_dotenv(project_root: Path | None) -> str | None:
    """从 .env 文件读取 API Key。

    .env 文件格式：KEY=VALUE，每行一个。
    不解析引号、不展开变量，仅做最简单的 KEY=VALUE 提取。

    Args:
        project_root: 项目根目录（.env 文件所在位置）

    Returns:
        API Key 字符串；文件不存在或未找到 Key 时返回 None
    """
    if project_root is None:
        return None

    dotenv_path = project_root / DOTENV_NAME
    if not dotenv_path.exists():
        return None

    try:
        content = dotenv_path.read_text(encoding="utf-8")
    except OSError:
        return None

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == ENV_KEY_NAME:
            v = value.strip()
            # 去除可能的引号包裹
            if v and v[0] in ("'", '"') and v[-1] == v[0]:
                v = v[1:-1]
            return v if v else None
    return None


def _read_config_file(project_root: Path | None) -> str | None:
    """从配置文件读取 API Key。

    配置文件位置：{project_root}/config/geo.local.yaml
    格式：YAML，含 amap_api_key 字段

    Args:
        project_root: 项目根目录

    Returns:
        API Key 字符串；文件不存在或未找到 Key 时返回 None
    """
    if project_root is None:
        return None

    config_path = project_root / "config" / CONFIG_FILE_NAME
    if not config_path.exists():
        return None

    try:
        import yaml

        content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None

    if not isinstance(content, dict):
        return None

    value = content.get(CONFIG_KEY_FIELD)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value else None


def load_api_key(project_root: Path | None = None) -> str:
    """按优先级读取高德 API Key。

    读取顺序：
    1. 环境变量 AMAP_API_KEY
    2. .env 文件（项目根）
    3. 配置文件 config/geo.local.yaml

    三者均不存在或为空 → 抛 ApiKeyMissingError。

    Args:
        project_root: 项目根目录（用于定位 .env 与 config/）；
                      None 时仅检查环境变量

    Returns:
        API Key 字符串（非空）

    Raises:
        ApiKeyMissingError: 三处均未找到有效 Key
    """
    # 1. 环境变量
    env_value = os.environ.get(ENV_KEY_NAME)
    if env_value and env_value.strip():
        return env_value.strip()

    # 2. .env 文件
    dotenv_value = _read_dotenv(project_root)
    if dotenv_value and dotenv_value.strip():
        return dotenv_value.strip()

    # 3. 配置文件
    config_value = _read_config_file(project_root)
    if config_value and config_value.strip():
        return config_value.strip()

    raise ApiKeyMissingError(
        f"未找到高德 API Key。请通过以下方式之一提供："
        f"1) 环境变量 {ENV_KEY_NAME}；"
        f"2) 项目根 {DOTENV_NAME} 文件；"
        f"3) config/{CONFIG_FILE_NAME} 配置文件。"
    )


class AmapGeocoder:
    """高德地图地理编码客户端。

    一次运行内同一地址最多请求一次 API（内存 Set 跟踪）。
    超时安全失败，最大重试 2 次（含首次）。

    不得记录 API Key、HTTP Header、Token、Cookie。
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
    ):
        """初始化地理编码客户端。

        Args:
            api_key: 高德 API Key（由 load_api_key() 获取）
            timeout_seconds: 单次请求超时（秒）
            max_attempts: 最大请求次数（含首次，默认 2）
        """
        if not api_key or not api_key.strip():
            raise ApiKeyMissingError("API Key 不能为空")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max(_validate_max_attempts(max_attempts), 1)
        # 本次运行已请求的地址集合（防止重复请求）
        self._requested_addresses: set[str] = set()

    def geocode(self, address: str) -> GeocodeResult | None:
        """对地址进行地理编码。

        一次运行内同一地址最多请求一次：
        - 已请求过的地址（无论成功失败）不再请求，直接返回 None
        - 未请求过的地址发起 API 请求

        失败安全：超时 / 网络错误 / API 返回异常 → 返回 None

        Args:
            address: 待编码的地址（建议先标准化）

        Returns:
            GeocodeResult 含坐标；失败时返回 None

        Note:
            日志仅记录地址是否成功、是否超时，不记录 API Key、Header。
        """
        if not address or not address.strip():
            return None

        normalized_addr = address.strip()

        # 一次运行同一地址最多请求一次
        if normalized_addr in self._requested_addresses:
            logger.info("地址已在本轮请求过，跳过重复请求: addr=%s", normalized_addr)
            return None

        self._requested_addresses.add(normalized_addr)

        try:
            result = self._request_with_retry(normalized_addr)
        except GeocodeTimeoutError:
            logger.warning(
                "地理编码超时（已重试 %s 次）: addr=%s", self._max_attempts, normalized_addr
            )
            return None
        except GeocodeError as e:
            logger.warning("地理编码失败: addr=%s err=%s", normalized_addr, str(e))
            return None

        if result is not None:
            logger.info(
                "地理编码成功: addr=%s lon=%s lat=%s",
                normalized_addr,
                result.longitude,
                result.latitude,
            )
        else:
            logger.info("地理编码未返回结果: addr=%s", normalized_addr)

        return result

    def _request_with_retry(self, address: str) -> GeocodeResult | None:
        """带重试的 API 请求。

        超时或网络错误时重试，最多 max_attempts 次。
        API 返回异常（status != "1"）不重试，直接视为失败。

        Raises:
            GeocodeTimeoutError: 所有重试均超时
            GeocodeError: API 返回异常或解析失败
        """
        last_error: Exception | None = None
        timed_out = False

        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._request_once(address)
            except urllib.error.URLError as e:
                last_error = e
                # 超时类错误标记
                reason = getattr(e, "reason", None)
                if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                    timed_out = True
                logger.debug(
                    "请求失败 attempt=%s/%s addr=%s",
                    attempt,
                    self._max_attempts,
                    address,
                )
            except OSError as e:
                # socket.timeout 也属于 OSError
                last_error = e
                if "timed out" in str(e).lower():
                    timed_out = True
                logger.debug(
                    "请求失败(网络) attempt=%s/%s addr=%s",
                    attempt,
                    self._max_attempts,
                    address,
                )

        if timed_out:
            raise GeocodeTimeoutError(f"请求超时，已重试 {self._max_attempts} 次")
        raise GeocodeError(f"请求失败: {last_error}")

    def _request_once(self, address: str) -> GeocodeResult | None:
        """单次 API 请求。

        Returns:
            GeocodeResult 或 None（API 返回无结果）

        Raises:
            urllib.error.URLError: 网络错误（含超时）
            GeocodeError: API 返回异常或响应解析失败
        """
        # 构造请求 URL（API Key 作为 query 参数，不记录到日志）
        params = urllib.parse.urlencode(
            {
                "address": address,
                "key": self._api_key,
                "output": "json",
            }
        )
        url = f"{AMAP_GEOCODE_URL}?{params}"

        req = urllib.request.Request(url, method="GET")
        # 不设置自定义 Header，避免日志中意外记录
        # 不记录 req.header_items() 或 url（含 Key）

        with urllib.request.urlopen(req, timeout=self._timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise GeocodeError(f"响应 JSON 解析失败: {e}") from e

        if not isinstance(data, dict):
            raise GeocodeError("响应非 JSON 对象")

        status = data.get("status")
        if status != AMAP_STATUS_SUCCESS:
            # API 返回错误，不重试
            infocode = data.get("infocode", "unknown")
            raise GeocodeError(f"API 返回错误 status={status} infocode={infocode}")

        geocodes = data.get("geocodes")
        if not geocodes or not isinstance(geocodes, list) or len(geocodes) == 0:
            # API 成功但无结果
            return None

        first = geocodes[0]
        if not isinstance(first, dict):
            return None

        location = first.get("location")
        if not location or not isinstance(location, str) or "," not in location:
            raise GeocodeError(f"API 返回 location 字段异常: {location!r}")

        lon_str, _, lat_str = location.partition(",")
        try:
            longitude = float(lon_str)
            latitude = float(lat_str)
        except ValueError as e:
            raise GeocodeError(f"坐标解析失败: location={location!r}") from e

        formatted = first.get("formatted_address") or address

        return GeocodeResult(
            formatted_address=formatted,
            longitude=longitude,
            latitude=latitude,
        )


def _validate_max_attempts(max_attempts: int) -> int:
    """校验最大重试次数（内部辅助函数）。

    确保至少为 1。用户要求最大 2 次，由构造函数默认值保证。
    """
    if max_attempts < 1:
        return 1
    return max_attempts


__all__ = [
    "AmapGeocoder",
    "GeocodeResult",
    "load_api_key",
    "ENV_KEY_NAME",
    "AMAP_GEOCODE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
]
