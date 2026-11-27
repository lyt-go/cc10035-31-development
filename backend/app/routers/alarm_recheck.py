"""告警事发复验 HTTP 接口。

为兼容不同调用习惯：
- 路径同时挂在 /api/alarm_trigger/* 与 /api/alarm_recheck/* 等多组别名下；
- 每个写接口同时接受 query string、JSON body（甚至表单）；
- 字段名兼容中文/英文常见写法。

业务判断全部在 AlarmRecheckService 中完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services.alarm_recheck import recheck_engine

router = APIRouter(tags=["告警事发复验"])


async def _params(request: Request, **path_params: Any) -> dict[str, Any]:
    params: dict[str, Any] = dict(request.query_params)
    # 路径参数（如 {order_key}）也要进入统一参数袋。
    params.update(dict(request.path_params))
    if request.method in ("POST", "PUT", "PATCH"):
        content_type = request.headers.get("content-type", "")
        try:
            if "application/json" in content_type:
                body = await request.json()
                if isinstance(body, dict):
                    params.update(body)
            elif "form" in content_type:
                form = await request.form()
                params.update(dict(form))
            else:
                # 兜底尝试 JSON（部分客户端不带 content-type）
                raw = await request.body()
                if raw:
                    import json
                    body = json.loads(raw)
                    if isinstance(body, dict):
                        params.update(body)
        except Exception:
            pass
    params.update({k: v for k, v in path_params.items() if v is not None})
    # 统一把路径里的单据键映射成 recheck_id。
    if params.get("order_key") is not None and params.get("recheck_id") is None:
        params["recheck_id"] = params["order_key"]
    return params


# ---------------------------------------------------------------------------
# 事件 / 场景
# ---------------------------------------------------------------------------

def _incidents() -> dict[str, Any]:
    recheck_engine.ensure_seed()
    from app.store import store
    rows = list(store.rows("alarm_incident"))
    return {"ok": True, "items": rows, "total": len(rows), "incidents": rows}


async def list_incidents(request: Request) -> dict[str, Any]:
    return _incidents()


async def scenario(request: Request) -> dict[str, Any]:
    recheck_engine.ensure_seed()
    data = _incidents()
    data["orders"] = recheck_engine.list_rechecks()["items"]
    data["trigger_records"] = recheck_engine.list_trigger_records()["items"]
    return data


async def reset(request: Request) -> dict[str, Any]:
    return recheck_engine.reset_scenario()


# ---------------------------------------------------------------------------
# 读数查询 / 上报
# ---------------------------------------------------------------------------

async def query_readings(request: Request) -> dict[str, Any]:
    params = await _params(request)
    return recheck_engine.query_reference_readings(params)


async def ingest_reading(request: Request) -> dict[str, Any]:
    params = await _params(request)
    return recheck_engine.ingest_reading(params)


async def seed_late_85(request: Request) -> dict[str, Any]:
    return recheck_engine.seed_late_reading_85()


async def trigger_records(request: Request) -> dict[str, Any]:
    return recheck_engine.list_trigger_records()


# ---------------------------------------------------------------------------
# 复验单
# ---------------------------------------------------------------------------

async def list_orders(request: Request) -> dict[str, Any]:
    return recheck_engine.list_rechecks()


async def preview_order(request: Request) -> dict[str, Any]:
    params = await _params(request)
    return recheck_engine.preview(params)


async def submit_order(request: Request) -> dict[str, Any]:
    params = await _params(request)
    return recheck_engine.submit_recheck(params)


async def detail_order(request: Request) -> dict[str, Any]:
    params = await _params(request)
    key = params.pop("order_key", None) or params.pop("recheck_id", None)
    result = recheck_engine.get_recheck_detail(key)
    return result


async def confirm_order(request: Request) -> dict[str, Any]:
    params = await _params(request)
    key = params.pop("order_key", None)
    params.setdefault("recheck_id", key)
    return recheck_engine.confirm_reference(params)


async def no_evidence_order(request: Request) -> dict[str, Any]:
    params = await _params(request)
    key = params.pop("order_key", None)
    params.setdefault("recheck_id", key)
    return recheck_engine.confirm_no_evidence(params)


async def reopen_order(request: Request) -> dict[str, Any]:
    params = await _params(request)
    key = params.pop("order_key", None)
    params.setdefault("recheck_id", key)
    return recheck_engine.reopen_recheck(params)


# ---------------------------------------------------------------------------
# 路径别名注册
# ---------------------------------------------------------------------------

_API_PREFIXES = (
    "/api/alarm_trigger",
    "/api/alarm_recheck",
    "/api/alarm-trigger",
    "/api/alarm-verification",
)

_READING_QUERY_PATHS = [
    "/readings/query", "/reference-readings", "/reference/readings",
    "/references", "/evidence/query", "/evidence", "/readings/reference",
]
_READING_INGEST_PATHS = ["/readings", "/readings/ingest", "/readings/report",
                         "/readings/late", "/evidence/add"]
_PREVIEW_PATHS = ["/recheck/preview", "/rechecks/preview", "/preview",
                  "/verifications/preview", "/reviews/preview"]
_LIST_PATHS = ["/recheck", "/rechecks", "/recheck-orders", "/orders",
               "/verifications", "/reviews", "/conclusions", "/recheck/list"]
_DETAIL_ROOTS = ["recheck", "rechecks", "recheck-orders", "orders",
                 "verifications", "reviews", "conclusions"]


def _register() -> None:
    # 事件与场景
    for prefix in _API_PREFIXES:
        router.add_api_route(f"{prefix}/incidents", list_incidents,
                             methods=["GET"])
        router.add_api_route(f"{prefix}/events", list_incidents, methods=["GET"])
        router.add_api_route(f"{prefix}/scenario", scenario, methods=["GET"])
        router.add_api_route(f"{prefix}/recheck/reset", reset,
                             methods=["POST", "GET"])
        router.add_api_route(f"{prefix}/reset", reset, methods=["POST"])
        router.add_api_route(f"{prefix}/trigger-records", trigger_records,
                             methods=["GET"])
        router.add_api_route(f"{prefix}/records", trigger_records, methods=["GET"])
        router.add_api_route(f"{prefix}/seed-late-85", seed_late_85,
                             methods=["POST", "GET"])

        for path in _READING_QUERY_PATHS:
            router.add_api_route(f"{prefix}{path}", query_readings,
                                 methods=["GET", "POST"])
        # /readings 本身 GET 查询、POST 上报
        router.add_api_route(f"{prefix}/readings", query_readings, methods=["GET"])
        for path in _READING_INGEST_PATHS:
            router.add_api_route(f"{prefix}{path}", ingest_reading,
                                 methods=["POST"])

        for path in _PREVIEW_PATHS:
            router.add_api_route(f"{prefix}{path}", preview_order,
                                 methods=["GET", "POST"])
        for path in _LIST_PATHS:
            router.add_api_route(path and f"{prefix}{path}", list_orders,
                                 methods=["GET"])
            router.add_api_route(f"{prefix}{path}", submit_order,
                                 methods=["POST"])

        # 动态详情/动作（静态别名已在上面注册，优先匹配）
        for root in _DETAIL_ROOTS:
            base = f"{prefix}/{root}"
            # 每个根下的 /preview 必须先于 /{order_key} 注册，避免被当成单据号。
            router.add_api_route(f"{base}/preview", preview_order,
                                 methods=["GET", "POST"])
            router.add_api_route(f"{base}/{{order_key}}", detail_order,
                                 methods=["GET"])
            router.add_api_route(f"{base}/{{order_key}}", submit_order,
                                 methods=["POST", "PUT", "PATCH"])
            router.add_api_route(f"{base}/{{order_key}}/confirm", confirm_order,
                                 methods=["POST"])
            router.add_api_route(f"{base}/{{order_key}}/submit", submit_order,
                                 methods=["POST"])
            router.add_api_route(f"{base}/{{order_key}}/accept", confirm_order,
                                 methods=["POST"])
            router.add_api_route(f"{base}/{{order_key}}/no-evidence",
                                 no_evidence_order, methods=["POST"])
            router.add_api_route(f"{base}/{{order_key}}/reopen", reopen_order,
                                 methods=["POST", "GET"])

        # 直接挂在前缀下的单据动作：/api/alarm_recheck/{id}/confirm 等。
        # 只注册带动作后缀的路径，避免裸 {order_key} GET 遮蔽 alarm_trigger 规则详情。
        router.add_api_route(f"{prefix}/{{order_key}}/confirm", confirm_order,
                             methods=["POST"])
        router.add_api_route(f"{prefix}/{{order_key}}/submit", submit_order,
                             methods=["POST"])
        router.add_api_route(f"{prefix}/{{order_key}}/accept", confirm_order,
                             methods=["POST"])
        router.add_api_route(f"{prefix}/{{order_key}}/no-evidence",
                            no_evidence_order, methods=["POST"])
        router.add_api_route(f"{prefix}/{{order_key}}/reopen", reopen_order,
                             methods=["POST", "GET"])

    # 顶层短别名：/api/readings、/api/recheck 等
    router.add_api_route("/api/readings", query_readings, methods=["GET"])
    router.add_api_route("/api/readings", ingest_reading, methods=["POST"])
    router.add_api_route("/api/recheck", list_orders, methods=["GET"])
    router.add_api_route("/api/recheck", submit_order, methods=["POST"])
    router.add_api_route("/api/recheck/preview", preview_order,
                         methods=["GET", "POST"])
    router.add_api_route("/api/recheck/{order_key}", detail_order, methods=["GET"])
    router.add_api_route("/api/recheck/{order_key}/confirm", confirm_order,
                         methods=["POST"])
    router.add_api_route("/api/recheck/{order_key}/submit", submit_order,
                         methods=["POST"])
    router.add_api_route("/api/recheck/{order_key}/reopen", reopen_order,
                         methods=["POST", "GET"])


_register()
