"""命名兼容模块：告警复验 / 复核服务入口。"""
from __future__ import annotations

from app.services.alarm_recheck import (  # noqa: F401
    AlarmReviewService,
    AlarmVerificationService,
    recheck_engine,
)

# 常见中文业务命名对应的英文类名
AlarmReverificationService = AlarmVerificationService

__all__ = [
    "AlarmVerificationService",
    "AlarmReviewService",
    "AlarmReverificationService",
    "recheck_engine",
]
