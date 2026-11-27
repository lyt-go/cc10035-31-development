"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import alarm_recheck, cross_module, resource

# alarm_recheck 的显式路径必须在 resource 的 /api/{module}/{entry_id} 之前注册，
# 否则 /api/alarm_trigger/readings 会被动态资源路由当成 int 入口校验而报 422。
ROUTERS = [cross_module, alarm_recheck, resource]
