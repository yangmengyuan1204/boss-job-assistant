"""P5 高德地理编码客户端测试。

全部 Mock urllib.request.urlopen，不真实联网。

覆盖：
- API 成功
- API 超时（重试 2 次后失败）
- API 返回异常（status != "1"）
- API 返回无结果
- API 返回异常 JSON
- 一次运行同一地址最多请求一次
- API Key 读取（环境变量 / .env / 配置文件 / 缺失）
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_tool.geo.exceptions import ApiKeyMissingError
from boss_tool.geo.geocoder import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    AmapGeocoder,
    GeocodeResult,
    load_api_key,
)


# ==================== Mock 响应构造 ====================
def _make_success_response(formatted: str, location: str = "120.1769,30.2761") -> bytes:
    """构造高德 API 成功响应体。"""
    body = {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "geocodes": [
            {
                "formatted_address": formatted,
                "location": location,
            }
        ],
    }
    return json.dumps(body).encode("utf-8")


def _make_no_result_response() -> bytes:
    """构造 API 成功但无 geocodes 的响应。"""
    body = {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "geocodes": [],
    }
    return json.dumps(body).encode("utf-8")


def _make_error_response(infocode: str = "INVALID_USER_KEY") -> bytes:
    """构造 API 错误响应（status != "1"）。"""
    body = {
        "status": "0",
        "info": "INVALID_USER_KEY",
        "infocode": infocode,
    }
    return json.dumps(body).encode("utf-8")


def _make_malformed_response() -> bytes:
    """构造非法 JSON 响应。"""
    return b"not a json"


class _FakeContextManager:
    """模拟 urllib.request.urlopen 返回的上下文管理器。"""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


def _mock_urlopen_with_body(body: bytes):
    """构造返回指定 body 的 urlopen mock。"""
    return MagicMock(return_value=_FakeContextManager(body))


# ==================== API 成功 ====================
class TestGeocodeSuccess:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """API 成功返回坐标。"""
        mock_urlopen = _mock_urlopen_with_body(
            _make_success_response("浙江省杭州市拱墅区建国北路88号")
        )
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key")
        result = geocoder.geocode("建国北路88号")

        assert result is not None
        assert isinstance(result, GeocodeResult)
        assert result.longitude == pytest.approx(120.1769)
        assert result.latitude == pytest.approx(30.2761)
        assert "建国北路88号" in result.formatted_address

    def test_success_with_custom_location(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """API 成功返回自定义坐标。"""
        mock_urlopen = _mock_urlopen_with_body(_make_success_response("某地", "121.4737,31.2304"))
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key")
        result = geocoder.geocode("某地")

        assert result is not None
        assert result.longitude == pytest.approx(121.4737)
        assert result.latitude == pytest.approx(31.2304)


# ==================== API 超时 ====================
class TestGeocodeTimeout:
    def test_timeout_retries_then_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """超时重试 2 次后返回 None。"""
        # 模拟超时：URLError with reason=TimeoutError
        timeout_error = urllib.error.URLError(reason=TimeoutError("timed out"))

        mock_urlopen = MagicMock(side_effect=timeout_error)
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key", max_attempts=2)
        result = geocoder.geocode("某地")

        # 超时安全失败返回 None
        assert result is None
        # 应该请求 2 次（max_attempts）
        assert mock_urlopen.call_count == 2

    def test_timeout_default_max_attempts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认 max_attempts=2。"""
        timeout_error = urllib.error.URLError(reason=TimeoutError("timed out"))
        mock_urlopen = MagicMock(side_effect=timeout_error)
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key")
        assert geocoder._max_attempts == MAX_ATTEMPTS
        geocoder.geocode("某地")
        assert mock_urlopen.call_count == MAX_ATTEMPTS


# ==================== API 返回异常 ====================
class TestGeocodeApiError:
    def test_api_error_status_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """API 返回 status=0 时不重试，返回 None。"""
        mock_urlopen = _mock_urlopen_with_body(_make_error_response())
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key", max_attempts=2)
        result = geocoder.geocode("某地")

        assert result is None
        # API 错误不重试，只请求 1 次
        assert mock_urlopen.call_count == 1

    def test_api_no_geocodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """API 成功但无 geocodes，返回 None。"""
        mock_urlopen = _mock_urlopen_with_body(_make_no_result_response())
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key")
        result = geocoder.geocode("不存在的地址")

        assert result is None

    def test_malformed_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """响应非 JSON，返回 None。"""
        mock_urlopen = _mock_urlopen_with_body(_make_malformed_response())
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key")
        result = geocoder.geocode("某地")

        assert result is None


# ==================== 一次运行同一地址最多请求一次 ====================
class TestNoDuplicateRequest:
    def test_same_address_requested_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同一地址第二次调用直接返回 None，不请求 API。"""
        mock_urlopen = _mock_urlopen_with_body(_make_success_response("某地", "120.0,30.0"))
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key")
        result1 = geocoder.geocode("某地")
        assert result1 is not None

        result2 = geocoder.geocode("某地")
        # 第二次不请求 API，直接返回 None
        assert result2 is None
        # 只请求了 1 次
        assert mock_urlopen.call_count == 1

    def test_different_addresses_requested_separately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不同地址分别请求。"""
        mock_urlopen = _mock_urlopen_with_body(_make_success_response("某地", "120.0,30.0"))
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key")
        geocoder.geocode("地址A")
        geocoder.geocode("地址B")

        # 两个不同地址各请求 1 次
        assert mock_urlopen.call_count == 2

    def test_empty_address_not_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空地址不请求 API。"""
        mock_urlopen = MagicMock()
        monkeypatch.setattr("boss_tool.geo.geocoder.urllib.request.urlopen", mock_urlopen)

        geocoder = AmapGeocoder(api_key="test-key")
        assert geocoder.geocode("") is None
        assert geocoder.geocode("   ") is None
        assert mock_urlopen.call_count == 0


# ==================== API Key 读取 ====================
class TestLoadApiKey:
    def test_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """从环境变量读取。"""
        monkeypatch.setenv("AMAP_API_KEY", "env-key")
        # 确保无 .env 和配置文件
        (tmp_path / ".env").unlink(missing_ok=True)
        (tmp_path / "config").mkdir(exist_ok=True)

        key = load_api_key(tmp_path)
        assert key == "env-key"

    def test_from_dotenv(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """从 .env 文件读取。"""
        monkeypatch.delenv("AMAP_API_KEY", raising=False)
        (tmp_path / ".env").write_text("AMAP_API_KEY=dotenv-key\n", encoding="utf-8")
        (tmp_path / "config").mkdir(exist_ok=True)

        key = load_api_key(tmp_path)
        assert key == "dotenv-key"

    def test_from_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """从配置文件读取。"""
        monkeypatch.delenv("AMAP_API_KEY", raising=False)
        (tmp_path / ".env").unlink(missing_ok=True)
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "geo.local.yaml").write_text("amap_api_key: config-key\n", encoding="utf-8")

        key = load_api_key(tmp_path)
        assert key == "config-key"

    def test_priority_env_over_dotenv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """环境变量优先于 .env。"""
        monkeypatch.setenv("AMAP_API_KEY", "env-key")
        (tmp_path / ".env").write_text("AMAP_API_KEY=dotenv-key\n", encoding="utf-8")
        (tmp_path / "config").mkdir(exist_ok=True)

        key = load_api_key(tmp_path)
        assert key == "env-key"

    def test_priority_dotenv_over_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """.env 优先于配置文件。"""
        monkeypatch.delenv("AMAP_API_KEY", raising=False)
        (tmp_path / ".env").write_text("AMAP_API_KEY=dotenv-key\n", encoding="utf-8")
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "geo.local.yaml").write_text("amap_api_key: config-key\n", encoding="utf-8")

        key = load_api_key(tmp_path)
        assert key == "dotenv-key"

    def test_missing_all_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """三处都没有 Key 时抛 ApiKeyMissingError。"""
        monkeypatch.delenv("AMAP_API_KEY", raising=False)
        (tmp_path / ".env").unlink(missing_ok=True)
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "geo.local.yaml").unlink(missing_ok=True)

        with pytest.raises(ApiKeyMissingError):
            load_api_key(tmp_path)

    def test_empty_env_falls_through(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """环境变量为空字符串时继续查找 .env。"""
        monkeypatch.setenv("AMAP_API_KEY", "")
        (tmp_path / ".env").write_text("AMAP_API_KEY=dotenv-key\n", encoding="utf-8")
        (tmp_path / "config").mkdir(exist_ok=True)

        key = load_api_key(tmp_path)
        assert key == "dotenv-key"

    def test_no_project_root_only_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """project_root=None 时仅检查环境变量。"""
        monkeypatch.setenv("AMAP_API_KEY", "env-key")
        key = load_api_key(None)
        assert key == "env-key"

    def test_no_project_root_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """project_root=None 且无环境变量时抛异常。"""
        monkeypatch.delenv("AMAP_API_KEY", raising=False)
        with pytest.raises(ApiKeyMissingError):
            load_api_key(None)


# ==================== 初始化校验 ====================
class TestAmapGeocoderInit:
    def test_empty_api_key_raises(self) -> None:
        """空 API Key 抛异常。"""
        with pytest.raises(ApiKeyMissingError):
            AmapGeocoder(api_key="")

    def test_whitespace_api_key_raises(self) -> None:
        """纯空白 API Key 抛异常。"""
        with pytest.raises(ApiKeyMissingError):
            AmapGeocoder(api_key="   ")

    def test_default_timeout(self) -> None:
        geocoder = AmapGeocoder(api_key="test-key")
        assert geocoder._timeout_seconds == DEFAULT_TIMEOUT_SECONDS

    def test_max_attempts_at_least_one(self) -> None:
        """max_attempts 至少为 1。"""
        geocoder = AmapGeocoder(api_key="test-key", max_attempts=0)
        assert geocoder._max_attempts >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
