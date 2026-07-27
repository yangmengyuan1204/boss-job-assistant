"""日志初始化。

使用标准库 logging + RotatingFileHandler。
要求：
- 同时输出控制台和文件
- 日志目录不存在时自动创建
- 日志级别来自配置
- 日志中不得记录 Cookie、验证码、完整用户目录内容、API Key
- 测试验证日志初始化不会重复添加 Handler
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from boss_tool.config import LoggingConfig

# 顶层 logger 名称
LOGGER_NAME = "boss_tool"

# 敏感关键字（出现在日志中会脱敏）
SENSITIVE_PATTERNS = (
    "cookie",
    "set-cookie",
    "authorization",
    "api_key",
    "apikey",
    "api-key",
    "access_token",
    "refresh_token",
    "password",
    "sms_code",
    "captcha",
    "verify_code",
    "sessionid",
)


class _RedactFilter(logging.Filter):
    """对包含敏感关键字的日志记录进行脱敏。

    仅用于防止意外打印敏感信息，不替代主动避免记录敏感信息的代码责任。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = str(record.getMessage())
            lower = msg.lower()
            for kw in SENSITIVE_PATTERNS:
                if kw in lower:
                    record.msg = f"[REDACTED: 含敏感关键字 {kw!r}]"
                    record.args = ()
                    break
        except Exception:  # noqa: BLE001 - Filter 不能抛出
            pass
        return True


def setup_logging(
    log_config: LoggingConfig,
    logs_dir: str | Path,
    *,
    force: bool = False,
) -> logging.Logger:
    """初始化日志系统。

    Args:
        log_config: 日志配置
        logs_dir: 日志目录（不存在时自动创建）
        force: 是否强制重新初始化（清除已有 handler）

    Returns:
        配置好的顶层 logger（boss_tool）
    """
    logger = logging.getLogger(LOGGER_NAME)

    if force:
        for h in list(logger.handlers):
            logger.removeHandler(h)

    # 防止重复添加 handler
    if logger.handlers and not force:
        return logger

    logger.setLevel(log_config.level)
    logger.propagate = False  # 避免被 root logger 二次打印

    formatter = logging.Formatter(
        fmt=log_config.format,
        datefmt=log_config.date_format,
    )

    redact = _RedactFilter()

    if log_config.console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_config.level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(redact)
        logger.addHandler(console_handler)

    if log_config.file_enabled:
        logs_path = Path(logs_dir)
        logs_path.mkdir(parents=True, exist_ok=True)
        log_file = logs_path / log_config.file_name
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=log_config.max_bytes,
            backupCount=log_config.backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_config.level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redact)
        logger.addHandler(file_handler)

    logger.debug("日志系统初始化完成 (level=%s, dir=%s)", log_config.level, logs_dir)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取子 logger。

    Usage:
        from boss_tool.logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("...")
    """
    if name is None or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if not name.startswith(f"{LOGGER_NAME}."):
        name = f"{LOGGER_NAME}.{name}"
    return logging.getLogger(name)


__all__ = ["setup_logging", "get_logger", "LOGGER_NAME"]
