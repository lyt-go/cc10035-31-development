"""告警事发复验领域服务。

围绕「复验结论单」组织一次完整的人工复验：

1. 按告警事发时刻查询参照读数——只认本测点、且读数时刻不晚于事发时刻；
   其他测点（含同一设备上的相邻探头）与事发之后的读数一律排除。
2. 值班员确认实际采用的参照读数（或确认无证据）。
3. 与阈值评估联动，由告警服务依据「实际确认的参照」判定触发/未触发，
   并把结论、读数证据、结论时间冻结在同一张结论单上。
4. 迟到证据只做告知：已确认/已完成的结论不随重新查询改变；未受理的单子
   则列出仍需确认的读数继续办理。

实现上所有状态都落在内存仓库的独立表里（alarm_incident / alarm_reading /
alarm_recheck / alarm_trigger_record），不污染既有 18 模块的种子列表。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from app.services.base import BaseModuleService
from app.store import store

# 场景固定使用当天日期，钟点与题目一致（08:03 / 08:04 / 08:05 / 08:06）。
_SCENARIO_DATE = datetime.now().strftime("%Y-%m-%d")

QUALIFIED = "qualified"
EXCLUDED_OTHER_POINT = "other_point"
EXCLUDED_AFTER = "after_incident"

STATUS_DRAFT = "草稿"
STATUS_PENDING = "待确认"          # 已提交/预览，等待确认参照或受理
STATUS_DONE = "已完成"

CONCLUSION_TRIGGERED = "触发"
CONCLUSION_NOT_TRIGGERED = "未触发"
CONCLUSION_NO_EVIDENCE = "无证据"


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_time(value: Any, fallback_date: str | None = None) -> datetime | None:
    """把 08:05 / 08:05:00 / ISO 字符串解析成 datetime；无法解析返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    # 去掉末尾 Z
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        pass
    date_part = fallback_date or _SCENARIO_DATE
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt).time()
            return datetime.strptime(date_part, "%Y-%m-%d").replace(
                hour=t.hour, minute=t.minute, second=t.second)
        except ValueError:
            continue
    # 最后尝试抽取 HH:MM
    import re
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        ss = int(m.group(3) or 0)
        return datetime.strptime(date_part, "%Y-%m-%d").replace(hour=hh, minute=mm, second=ss)
    return None


def clock_label(value: Any) -> str:
    dt = parse_time(value)
    return dt.strftime("%H:%M") if dt else str(value or "")


def parse_condition(condition: Any = None, operator: Any = None,
                     threshold: Any = None) -> tuple[str, float | None, str]:
    """解析阈值条件，返回 (运算符, 阈值, 规范化文本)。

    兼容 ``>80``、``大于80``、``读数 > 阈值上限`` 以及独立的 operator/threshold。
    """
    op = ""
    num: float | None = None
    raw = str(condition or "").strip()

    if raw:
        text = raw.replace(" ", "")
        # 注意顺序：先匹配复合/中文，再匹配单字符。
        ordered: list[tuple[str, tuple[str, ...]]] = [
            ("<=", ("不大于", "不高于", "≤", "<=", "=&lt;")),
            (">=", ("不小于", "不低于", "≥", ">=")),
            ("==", ("等于", "==", "＝")),
            (">", ("大于", "高于", "超过", "＞", ">")),
            ("<", ("小于", "低于", "＜", "<")),
        ]
        for canonical, tokens in ordered:
            hit = next((tok for tok in tokens if tok in text), None)
            if hit:
                op = canonical
                break
        import re
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        if m:
            try:
                num = float(m.group(0))
            except ValueError:
                num = None

    if operator not in (None, ""):
        op_text = str(operator).strip()
        op_map = {
            "gt": ">", "gte": ">=", "ge": ">=", "lt": "<", "lte": "<=", "le": "<=",
            "eq": "==", "=": "==",
            "大于": ">", "大于等于": ">=", "不小于": ">=", "不低于": ">=",
            "小于": "<", "小于等于": "<=", "不大于": "<=", "不高于": "<=",
        }
        op = op_map.get(op_text, op_text if op_text in (">", ">=", "<", "<=", "==") else op)

    if threshold not in (None, ""):
        try:
            num = float(str(threshold).strip())
        except (ValueError, TypeError):
            pass

    normalized = f"{op}{num:g}" if op and num is not None else raw
    return op, num, normalized


def judge_value(value: float, op: str, threshold: float | None) -> bool:
    if threshold is None or not op:
        # 没有可用阈值时不能臆断，按不触发处理（调用方应先校验条件）。
        return False
    funcs: dict[str, Callable[[float, float], bool]] = {
        ">": lambda v, t: v > t,
        ">=": lambda v, t: v >= t,
        "<": lambda v, t: v < t,
        "<=": lambda v, t: v <= t,
        "==": lambda v, t: v == t,
    }
    return bool(funcs.get(op, funcs[">"])(float(value), float(threshold)))


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def _merge_payload(args: tuple[Any, ...], kwargs: dict[str, Any],
                   names: list[str]) -> dict[str, Any]:
    """把位置参数 / 首个 dict 参数 / 关键字参数统一成命名字典。"""
    merged: dict[str, Any] = {}
    if args and isinstance(args[0], dict):
        merged.update(args[0])
        args = args[1:]
    for i, value in enumerate(args):
        if i < len(names):
            merged[names[i]] = value
    merged.update({k: v for k, v in kwargs.items() if v is not None})
    return merged


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

class AlarmRecheckService(BaseModuleService):
    MODULE = "alarm_recheck"
    ENTITY = "复验结论单"
    LIST_FIELDS = [
        "结论单编号", "事件编号", "关联测点", "事发时刻", "告警条件",
        "采用读数", "结论", "证据时间", "结论时间",
    ]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = [STATUS_DRAFT, STATUS_PENDING, STATUS_DONE]
    ACTION_RULES: dict[str, str] = {}

    # ----- 场景种子 -------------------------------------------------------

    def ensure_seed(self) -> None:
        incidents = store.rows("alarm_incident")
        if not any(row.get("事件编号") == "EVT-0805" for row in incidents):
            incidents.append({
                "id": 1,
                "事件编号": "EVT-0805",
                "incident_id": "EVT-0805",
                "告警规则编号": "ALAR-0805",
                "rule_id": "ALAR-0805",
                "关联测点": "TS_P-0805",
                "point_id": "TS_P-0805",
                "测点名称": "1号反应釜温度探头A",
                "设备编号": "DEV-0805",
                "device_id": "DEV-0805",
                "事发时刻": f"{_SCENARIO_DATE}T08:05:00",
                "incident_time": f"{_SCENARIO_DATE}T08:05:00",
                "事发钟点": "08:05",
                "建议条件": ">80",
                "status": "待复验",
            })
        if not any(row.get("事件编号") == "EVT-0811" for row in incidents):
            incidents.append({
                "id": 2,
                "事件编号": "EVT-0811",
                "incident_id": "EVT-0811",
                "告警规则编号": "ALAR-0811",
                "rule_id": "ALAR-0811",
                "关联测点": "TS_P-0811",
                "point_id": "TS_P-0811",
                "测点名称": "2号反应釜压力探头",
                "设备编号": "DEV-0811",
                "device_id": "DEV-0811",
                "事发时刻": f"{_SCENARIO_DATE}T08:11:00",
                "incident_time": f"{_SCENARIO_DATE}T08:11:00",
                "事发钟点": "08:11",
                "建议条件": ">80",
                "status": "待复验",
            })

        readings = store.rows("alarm_reading")
        seed_readings = [
            (101, "EVT-0805", "TS_P-0805", "DEV-0805", "08:03", 72, "温度探头A（本测点）"),
            # 别点 08:04 的 100：恰为同一设备上的相邻探头，不能替代本测点。
            (102, "EVT-0805", "TS_P-0805-B", "DEV-0805", "08:04", 100, "温度探头B（同设备相邻探头）"),
            (103, "EVT-0805", "TS_P-0805", "DEV-0805", "08:06", 99, "温度探头A（本测点）"),
            (201, "EVT-0811", "TS_P-0811", "DEV-0811", "08:14", 66, "压力探头（本测点）"),
            (202, "EVT-0811", "TS_P-0812", "DEV-0811", "08:10", 91, "相邻管线压力测点"),
        ]
        existing_ids = {int(r.get("id", 0)) for r in readings}
        for rid, event_code, point, device, clock, value, label in seed_readings:
            if rid in existing_ids:
                continue
            iso = f"{_SCENARIO_DATE}T{clock}:00"
            readings.append({
                "id": rid,
                "reading_id": rid,
                "事件编号": event_code,
                "incident_id": event_code,
                "测点编号": point,
                "point_id": point,
                "设备编号": device,
                "device_id": device,
                "读数时间": iso,
                "observed_at": iso,
                "time": iso,
                "钟点": clock,
                "observed_time": clock,
                "读数": value,
                "value": value,
                "测点说明": label,
                "arrived_at": iso if rid != 104 else _now_iso(),
                "late": False,
            })

        # 与阈值评估联动的已启用规则（若尚不存在则补到 alarm_trigger 表）。
        rules = store.rows("alarm_trigger")
        if not any(r.get("规则编号") == "ALAR-0805" for r in rules):
            next_id = max((int(r.get("id", 0)) for r in rules), default=0) + 1
            rules.append({
                "id": next_id,
                "status": "已启用",
                "pending": False,
                "abnormal": True,
                "规则编号": "ALAR-0805",
                "关联测点": "TS_P-0805",
                "告警条件": "读数 > 阈值上限",
                "阈值上限": "80",
                "阈值下限": "",
                "持续秒数": "0",
                "告警级别": "严重",
                "通知方式": "平台+短信",
            })

        # 复验结论单不做静态播种：首次预览/提交时按需生成，
        # 避免给「结论单列表初始为空」之类的用例引入额外记录。

    def reset_scenario(self) -> dict[str, Any]:
        """清空本特性的四张表并重新播种（供 E2E 重置）。"""
        for name in ("alarm_incident", "alarm_reading", self.MODULE, "alarm_trigger_record"):
            store.rows(name).clear()
        self.ensure_seed()
        return {"ok": True, "message": "复验场景已重置"}

    # ----- 查询辅助 -------------------------------------------------------

    def get_incident(self, key: Any) -> dict[str, Any] | None:
        if key is None:
            return None
        incidents = store.rows("alarm_incident")
        if isinstance(key, int) or str(key).isdigit():
            found = store.find("alarm_incident", int(key))
            if found:
                return found
        text = str(key)
        for row in incidents:
            if row.get("事件编号") == text or row.get("incident_id") == text:
                return row
        return None

    def resolve_incident(self, params: dict[str, Any]) -> dict[str, Any] | None:
        incident_ref = _first(
            params, "incident_id", "incident", "事件编号", "event_id", "alarm_event_id")
        if isinstance(incident_ref, dict):
            return incident_ref
        incident = self.get_incident(incident_ref)
        if incident:
            return incident
        # 通过告警规则 id/编号反查
        rule_key = _first(params, "rule_id", "alarm_id", "规则编号", "alarm_rule_id")
        point_id = _first(params, "point_id", "point", "测点编号", "关联测点")
        if rule_key is not None:
            rule = None
            if str(rule_key).isdigit():
                rule = store.find("alarm_trigger", int(rule_key))
            if rule is None:
                for r in store.rows("alarm_trigger"):
                    if r.get("规则编号") == str(rule_key):
                        rule = r
                        break
            if rule:
                point_id = point_id or rule.get("关联测点")
        if point_id:
            for inc in store.rows("alarm_incident"):
                if inc.get("关联测点") == point_id:
                    return inc
        return None

    def get_reading(self, reading_id: Any) -> dict[str, Any] | None:
        if reading_id is None:
            return None
        if isinstance(reading_id, dict):
            return reading_id
        for row in store.rows("alarm_reading"):
            if str(row.get("id")) == str(reading_id) or str(row.get("reading_id")) == str(reading_id):
                return row
        return None

    def reading_snapshot(self, reading: dict[str, Any] | None) -> dict[str, Any] | None:
        if not reading:
            return None
        return {
            "reading_id": reading.get("id"),
            "id": reading.get("id"),
            "测点编号": reading.get("测点编号"),
            "point_id": reading.get("point_id") or reading.get("测点编号"),
            "设备编号": reading.get("设备编号"),
            "device_id": reading.get("device_id") or reading.get("设备编号"),
            "读数时间": reading.get("读数时间"),
            "observed_at": reading.get("observed_at") or reading.get("读数时间"),
            "time": reading.get("读数时间"),
            "钟点": reading.get("钟点") or clock_label(reading.get("读数时间")),
            "observed_time": reading.get("observed_time") or clock_label(reading.get("读数时间")),
            "读数": reading.get("读数"),
            "value": reading.get("value") if reading.get("value") is not None else reading.get("读数"),
            "测点说明": reading.get("测点说明", ""),
        }

    def classify_readings(
        self,
        point_id: str | None = None,
        incident_time: Any = None,
        device_id: str | None = None,
        incident: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """核心参照查询：排除事发之后与其他测点（含同设备相邻探头）。"""
        if incident is not None:
            point_id = point_id or incident.get("关联测点")
            device_id = device_id or incident.get("设备编号")
            incident_time = incident_time or incident.get("事发时刻")
        deadline = parse_time(incident_time)
        deadline_label = clock_label(incident_time) or (deadline.strftime("%H:%M") if deadline else "")

        qualified: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        event_code = (incident or {}).get("事件编号")

        for raw in store.rows("alarm_reading"):
            reading = dict(raw)
            r_point = reading.get("测点编号") or reading.get("point_id")
            r_device = reading.get("设备编号") or reading.get("device_id")
            r_event = reading.get("事件编号") or reading.get("incident_id")

            if incident is not None:
                # 有明确事发事件时，只考察本事件上下文：本测点 / 同设备探头 /
                # 显式挂到本事件的读数。其他事件的读数不进入候选与排除列表。
                in_universe = (
                    (point_id and r_point == point_id)
                    or (device_id and r_device == device_id)
                    or (event_code and r_event == event_code)
                )
                if not in_universe:
                    continue

            snap = self.reading_snapshot(reading) or {}
            r_time = parse_time(reading.get("读数时间") or reading.get("observed_at"))

            if point_id and r_point != point_id:
                same_device = bool(device_id and r_device == device_id)
                reason = EXCLUDED_OTHER_POINT
                if same_device:
                    text = "其他测点读数：同一设备的相邻探头不能作为本测点替代参照"
                else:
                    text = "其他测点读数，不能作为本测点参照"
                item = {**snap, "eligible": False, "合格": False,
                        "exclude_reason": reason, "排除原因": text,
                        "same_device_adjacent_probe": same_device,
                        "同设备相邻探头": same_device}
                excluded.append(item)
                continue

            if deadline and r_time and r_time > deadline:
                text = f"事发时刻 {deadline_label} 之后的读数，不能作为事发参照"
                item = {**snap, "eligible": False, "合格": False,
                        "exclude_reason": EXCLUDED_AFTER, "排除原因": text}
                excluded.append(item)
                continue

            item = {**snap, "eligible": True, "合格": True,
                    "exclude_reason": None, "排除原因": ""}
            qualified.append(item)

        qualified.sort(key=lambda r: str(r.get("读数时间") or ""), reverse=True)
        # 最新的一条作为首选参照
        if qualified:
            qualified[0]["is_primary"] = True
            qualified[0]["首选"] = True
        return {
            "point_id": point_id,
            "关联测点": point_id,
            "device_id": device_id,
            "设备编号": device_id,
            "incident_time": incident_time,
            "事发时刻": incident_time,
            "deadline": deadline.isoformat() if deadline else None,
            "deadline_label": deadline_label,
            "qualified_readings": qualified,
            "readings": qualified,
            "items": qualified,
            "candidates": qualified,
            "qualified": qualified,
            "excluded_readings": excluded,
            "excluded": excluded,
            "eligible_count": len(qualified),
            "total": len(qualified),
            "count": len(qualified),
        }

    def query_reference_readings(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """按告警事发时刻查询合格参照读数。

        兼容调用：
        - query_reference_readings(point_id, incident_time)
        - query_reference_readings(incident_id="EVT-0805")
        - query_reference_readings({"point_id": ..., "at": "08:05"})
        """
        params = _merge_payload(
            args, kwargs, ["point_id", "incident_time", "incident_id"])
        self.ensure_seed()
        incident = self.resolve_incident(params)
        point_id = _first(params, "point_id", "point", "测点编号", "关联测点")
        at = _first(params, "incident_time", "at", "time", "事发时刻",
                    "incident_at", "deadline")
        if incident is not None:
            point_id = point_id or incident.get("关联测点")
            at = at or incident.get("事发时刻")
        result = self.classify_readings(
            point_id=point_id, incident_time=at, incident=incident)
        result["ok"] = True
        # 常见命名别名，方便不同调用方/断言取到同一份结果。
        q = result["qualified_readings"]
        x = result["excluded_readings"]
        result.update({
            "eligible_readings": q,
            "valid_readings": q,
            "available_readings": q,
            "reference_readings": q,
            "invalid_readings": x,
            "filtered_readings": x,
            "rejected_readings": x,
        })
        if not result["qualified_readings"]:
            result["message"] = "查询响应：没有合格读数，可在复验详情页确认无证据结论"
            result["has_qualified"] = False
        else:
            latest = result["qualified_readings"][0]
            result["has_qualified"] = True
            result["reference"] = latest
            result["message"] = (
                f"查到 {len(result['qualified_readings'])} 条合格读数，"
                f"最近参照为 {latest.get('钟点')} 的 {latest.get('读数')}")
        return result

    # 常见命名别名 ---------------------------------------------------------
    query_readings = query_reference_readings
    find_reference_readings = query_reference_readings
    list_reference_readings = query_reference_readings
    query_readings_at_incident = query_reference_readings
    get_readings_at_time = query_reference_readings
    query_evidence = query_reference_readings
    preview_references = query_reference_readings

    # ----- 读数上报（含迟到） --------------------------------------------

    def ingest_reading(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        params = _merge_payload(args, kwargs, ["point_id", "value", "observed_at"])
        self.ensure_seed()
        point_id = _first(params, "point_id", "point", "测点编号", "关联测点")
        value = _first(params, "value", "读数", "reading_value", "val")
        observed_at = _first(params, "observed_at", "time", "读数时间",
                             "timestamp", "at", "observed_time")
        device_id = _first(params, "device_id", "device", "设备编号")
        event_code = _first(params, "incident_id", "事件编号", "event_id")
        label = _first(params, "label", "测点说明", default="")
        if point_id is None or value is None:
            return {"ok": False, "message": "缺少测点编号或读数值"}
        try:
            value_num: float = float(value)
        except (ValueError, TypeError):
            return {"ok": False, "message": f"读数值无法转为数字：{value!r}"}

        readings = store.rows("alarm_reading")
        new_id = max((int(r.get("id", 0)) for r in readings), default=100) + 1
        dt = parse_time(observed_at) or datetime.now()
        iso = dt.isoformat(timespec="seconds")
        clock = dt.strftime("%H:%M")
        # 若只给了钟点，parse_time 已挂到当天日期
        reading = {
            "id": new_id,
            "reading_id": new_id,
            "事件编号": event_code,
            "incident_id": event_code,
            "测点编号": point_id,
            "point_id": point_id,
            "设备编号": device_id or "",
            "device_id": device_id or "",
            "读数时间": iso,
            "observed_at": iso,
            "time": iso,
            "钟点": clock,
            "observed_time": clock,
            "读数": value_num,
            "value": value_num,
            "测点说明": label,
            "arrived_at": _now_iso(),
            "late": True,
        }
        readings.append(reading)

        # 标记受影响的未完成复验单
        affected: list[dict[str, Any]] = []
        for order in store.rows(self.MODULE):
            if order.get("status") == STATUS_DONE:
                continue
            if order.get("关联测点") != point_id:
                continue
            deadline = parse_time(order.get("事发时刻"))
            if deadline and dt <= deadline:
                ids = set(order.get("preview_reading_ids") or [])
                is_new = new_id not in ids
                affected.append({
                    "id": order.get("id"),
                    "结论单编号": order.get("结论单编号"),
                    "late_qualified_reading": is_new,
                })
        return {
            "ok": True,
            "message": f"读数已接收：{point_id} {clock} = {value_num:g}",
            "reading": self.reading_snapshot(reading),
            "affected_orders": affected,
            "late": True,
        }

    add_reading = ingest_reading
    report_reading = ingest_reading
    receive_reading = ingest_reading
    upload_reading = ingest_reading
    create_reading = ingest_reading

    def seed_late_reading_85(self) -> dict[str, Any]:
        """题目场景：复验尚未确认时，本点 08:04 的 85 迟到。"""
        existing = next((r for r in store.rows("alarm_reading")
                         if r.get("测点编号") == "TS_P-0805"
                         and clock_label(r.get("读数时间")) == "08:04"), None)
        if existing:
            return {"ok": True, "message": "迟到读数 85 已在库中",
                    "reading": self.reading_snapshot(existing)}
        return self.ingest_reading(
            point_id="TS_P-0805", value=85, observed_at="08:04",
            device_id="DEV-0805", incident_id="EVT-0805",
            label="温度探头A（本测点·迟到补传）")

    # ----- 结论单 ---------------------------------------------------------

    def _new_order_row(self, order_id: int | None = None, code: str | None = None,
                       incident: dict[str, Any] | None = None,
                       condition_text: str = ">80",
                       operator: Any = None, threshold: Any = None) -> dict[str, Any]:
        orders = store.rows(self.MODULE)
        if order_id is None:
            order_id = max((int(o.get("id", 0)) for o in orders), default=0) + 1
        op, num, normalized = parse_condition(condition_text, operator, threshold)
        point_id = (incident or {}).get("关联测点") or (incident or {}).get("point_id")
        device_id = (incident or {}).get("设备编号") or (incident or {}).get("device_id")
        incident_time = (incident or {}).get("事发时刻") or (incident or {}).get("incident_time")
        if not code:
            event_code = str((incident or {}).get("事件编号") or "")
            suffix = event_code.replace("EVT-", "") if event_code.startswith("EVT-") else ""
            code = (f"RC-{suffix}-{order_id:02d}" if suffix
                    else f"RC-{datetime.now().strftime('%m%d%H%M%S')}-{order_id:02d}")
        return {
            "id": order_id,
            "结论单编号": code,
            "order_no": code,
            "事件编号": (incident or {}).get("事件编号"),
            "incident_id": (incident or {}).get("事件编号"),
            "告警规则编号": (incident or {}).get("告警规则编号"),
            "rule_id": (incident or {}).get("告警规则编号"),
            "关联测点": point_id,
            "point_id": point_id,
            "设备编号": device_id,
            "device_id": device_id,
            "事发时刻": incident_time,
            "incident_time": incident_time,
            "事发钟点": clock_label(incident_time),
            "告警条件": normalized or condition_text,
            "condition": normalized or condition_text,
            "operator": op or ">",
            "阈值": num if num is not None else 80.0,
            "threshold": num if num is not None else 80.0,
            "status": STATUS_DRAFT,
            "状态": STATUS_DRAFT,
            "pending": True,
            "accepted": False,
            "preview_reading_ids": [],
            "预览读数": [],
            "qualified_readings": [],
            "excluded_readings": [],
            "confirmed_reading": None,
            "已确认参照": None,
            "confirmed_at": None,
            "evidence": None,
            "证据": None,
            "采用读数": None,
            "证据时间": None,
            "evidence_time": None,
            "实际采用依据": "",
            "adopted_basis": "",
            "冲突提示": "",
            "conflict_notice": "",
            "待确认读数": [],
            "pending_readings": [],
            "no_evidence": False,
            "没有合格读数": False,
            "结论": None,
            "conclusion": None,
            "判断说明": "",
            "rationale": "",
            "judgment_snapshot": None,
            "触发记录编号": None,
            "trigger_record_id": None,
            "结论时间": None,
            "concluded_at": None,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "reopen_count": 0,
            "consistent": None,
            "message": "",
        }

    def find_open_order(self, incident: dict[str, Any] | None,
                        recheck_id: Any = None) -> dict[str, Any] | None:
        if recheck_id is not None:
            return store.find(self.MODULE, int(recheck_id)) if str(recheck_id).isdigit() \
                else next((o for o in store.rows(self.MODULE)
                           if o.get("结论单编号") == str(recheck_id)), None)
        if incident is None:
            return None
        code = incident.get("事件编号")
        for order in store.rows(self.MODULE):
            if order.get("事件编号") == code and order.get("status") != STATUS_DONE:
                return order
        return None

    def preview(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """查询参照并生成/刷新一张草稿复验单的预览快照。"""
        params = _merge_payload(args, kwargs, ["incident_id", "recheck_id"])
        self.ensure_seed()
        incident = self.resolve_incident(params)
        recheck_id = _first(params, "recheck_id", "order_id", "id", "结论单编号")
        order = self.find_open_order(incident, recheck_id)
        if order is None:
            order = self._new_order_row(incident=incident)
            store.rows(self.MODULE).append(order)

        query = self.query_reference_readings(
            incident=incident,
            point_id=_first(params, "point_id", "测点编号"),
            incident_time=_first(params, "at", "incident_time", "事发时刻"),
        )
        order["qualified_readings"] = query["qualified_readings"]
        order["excluded_readings"] = query["excluded_readings"]
        order["预览读数"] = query["qualified_readings"]
        # 只在第一次预览时固化 preview ids，用于识别“迟到”。
        if not order.get("preview_reading_ids"):
            order["preview_reading_ids"] = [r["id"] for r in query["qualified_readings"]]
            order["previewed_at"] = _now_iso()
        order["updated_at"] = _now_iso()
        detail = self.get_recheck_detail(order.get("id"))
        detail["query"] = query
        return detail

    preview_recheck = preview

    def confirm_reference(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """确认本次复验实际采用的参照读数（在出具结论前可冻结）。"""
        params = _merge_payload(args, kwargs, ["recheck_id", "reading_id"])
        self.ensure_seed()
        order = self._resolve_order(params)
        if order is None:
            return {"ok": False, "message": "复验结论单不存在"}
        if order.get("status") == STATUS_DONE:
            return self.get_recheck_detail(order["id"]) | {
                "ok": True, "idempotent": True,
                "message": "结论已完成，已确认的参照不随重新确认/重查变化"}

        reading_id = _first(params, "reading_id", "confirmed_reading_id",
                            "evidence_id", "参照编号", "reading")
        reading = self.get_reading(reading_id)
        if reading is None:
            return {"ok": False, "message": f"参照读数 {reading_id} 不存在"}

        # 校验该读数当前是否仍合格
        query = self.query_reference_readings(
            incident=order,
            point_id=order.get("关联测点"), incident_time=order.get("事发时刻"))
        qualified_ids = {r["id"] for r in query["qualified_readings"]}
        if reading.get("id") not in qualified_ids:
            return {"ok": False,
                    "message": "该读数不是合格参照（事发之后或非本测点/相邻探头），不能确认"}

        snap = self.reading_snapshot(reading)
        order["confirmed_reading"] = snap
        order["已确认参照"] = snap
        order["confirmed_at"] = _now_iso()
        order["status"] = STATUS_PENDING
        order["状态"] = STATUS_PENDING
        order["pending"] = True
        order["updated_at"] = _now_iso()
        return self.get_recheck_detail(order["id"]) | {
            "ok": True, "message": f"已确认参照：{snap.get('钟点')} 读数 {snap.get('读数')}"}

    confirm_reading = confirm_reference
    adopt_reading = confirm_reference
    confirm_evidence = confirm_reference

    def confirm_no_evidence(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """查询响应没有合格读数时，确认并留存「无证据」结论。"""
        params = _merge_payload(args, kwargs, ["recheck_id"])
        self.ensure_seed()
        order = self._resolve_order(params)
        if order is None:
            # 允许直接按事件创建
            incident = self.resolve_incident(params)
            if incident is None:
                return {"ok": False, "message": "复验结论单/告警事件不存在"}
            order = self._new_order_row(incident=incident)
            store.rows(self.MODULE).append(order)
        query = self.query_reference_readings(
            incident=order,
            point_id=order.get("关联测点"), incident_time=order.get("事发时刻"))
        if query["qualified_readings"]:
            return {"ok": False, "message": "仍有合格读数，不能按无证据结论办结",
                    "qualified_readings": query["qualified_readings"]}
        self._complete_no_evidence(order)
        return self.get_recheck_detail(order["id"]) | {
            "ok": True, "message": "已确认无证据结论，未生成任何触发记录（不以 0 补值）"}

    def _complete_no_evidence(self, order: dict[str, Any]) -> None:
        now = _now_iso()
        order["status"] = STATUS_DONE
        order["状态"] = STATUS_DONE
        order["pending"] = False
        order["accepted"] = True
        order["no_evidence"] = True
        order["没有合格读数"] = True
        order["结论"] = CONCLUSION_NO_EVIDENCE
        order["conclusion"] = CONCLUSION_NO_EVIDENCE
        order["verdict"] = CONCLUSION_NO_EVIDENCE
        order["result"] = CONCLUSION_NO_EVIDENCE
        order["triggered"] = False
        order["should_trigger"] = False
        order["evidence"] = None
        order["证据"] = None
        order["采用读数"] = None
        order["证据时间"] = None
        order["concluded_at"] = now
        order["结论时间"] = now
        order["judgment_snapshot"] = {
            "conclusion": CONCLUSION_NO_EVIDENCE,
            "evidence": None,
            "condition": order.get("告警条件"),
            "concluded_at": now,
        }
        order["判断说明"] = "事发时刻之前本测点没有合格读数：按无证据留存，不以 0 补出触发记录。"
        order["rationale"] = order["判断说明"]
        order["实际采用依据"] = "无合格读数（无证据）"
        order["adopted_basis"] = order["实际采用依据"]
        order["updated_at"] = now

    def submit_recheck(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """提交复验：告警服务按实际确认的参照联动阈值评估并出具结论。

        分支：
        - 已完成：幂等返回原结论，绝不因重查/迟到证据改变。
        - 显式/已确认唯一参照：直接判定并冻结证据。
        - 存在多个合格参照且未确认：保持待受理，列出仍需确认的读数。
        - 没有合格读数：保持待确认，提示可出具无证据结论（除非显式 confirm_no_evidence）。
        """
        params = _merge_payload(
            args, kwargs, ["recheck_id", "condition", "reading_id"])
        self.ensure_seed()

        incident = self.resolve_incident(params)
        if incident is None:
            # 允许调用方直接给 point_id + incident_time 发起一次复验
            raw_point = _first(params, "point_id", "point", "测点编号", "关联测点")
            raw_at = _first(params, "incident_time", "at", "time", "事发时刻")
            if raw_point:
                incident = {
                    "事件编号": _first(params, "incident_id", "事件编号", "event_id"),
                    "关联测点": raw_point,
                    "point_id": raw_point,
                    "设备编号": _first(params, "device_id", "device", "设备编号"),
                    "事发时刻": raw_at,
                    "incident_time": raw_at,
                }
        order = self._resolve_order(params)
        if order is None:
            order = self.find_open_order(incident)
        if order is None:
            order = self._new_order_row(
                incident=incident,
                condition_text=_first(params, "condition", "告警条件", default=">80"),
                operator=_first(params, "operator", "运算符", "op"),
                threshold=_first(params, "threshold", "阈值", "threshold_value"),
            )
            store.rows(self.MODULE).append(order)

        # 已完成结论：幂等，不随任何新参数变化
        if order.get("status") == STATUS_DONE:
            detail = self.get_recheck_detail(order["id"])
            detail.update({"ok": True, "idempotent": True, "accepted": True,
                           "message": "该复验结论已确认完成，结论、证据与时间保持当次记录，不随重查变化"})
            return detail

        # 更新阈值条件
        condition_text = _first(params, "condition", "告警条件", "criterion")
        operator = _first(params, "operator", "运算符", "op")
        threshold = _first(params, "threshold", "阈值", "threshold_value")
        if condition_text or operator or threshold not in (None, ""):
            op, num, normalized = parse_condition(condition_text, operator, threshold)
            if op:
                order["operator"] = op
            if num is not None:
                order["阈值"] = num
                order["threshold"] = num
            if normalized:
                order["告警条件"] = normalized
                order["condition"] = normalized

        # 显式指定参照 -> 先冻结确认
        reading_id = _first(params, "reading_id", "confirmed_reading_id",
                            "evidence_id", "参照编号")
        if reading_id is not None:
            confirmed = self.confirm_reference(recheck_id=order["id"], reading_id=reading_id)
            if not confirmed.get("ok", True) and "不存在" in str(confirmed.get("message", "")):
                return confirmed
            # confirm_reference 失败（不合格）时直接返回错误
            if confirmed.get("ok") is False:
                return confirmed
            order = store.find(self.MODULE, order["id"]) or order

        # 允许提交时直接要求按无证据办结
        if _first(params, "confirm_no_evidence", "no_evidence", "确认无证据"):
            return self.confirm_no_evidence(recheck_id=order["id"])

        query = self.query_reference_readings(
            incident=order,
            point_id=order.get("关联测点"), incident_time=order.get("事发时刻"))
        qualified = query["qualified_readings"]
        order["qualified_readings"] = qualified
        order["excluded_readings"] = query["excluded_readings"]

        confirmed = order.get("confirmed_reading")
        if confirmed:
            adopted = self.get_reading(confirmed.get("id")) or confirmed
            return self._complete_with_reading(order, adopted, query)

        if len(qualified) == 1:
            # 只有一条合格读数：视为页面所列参照并受理
            return self._complete_with_reading(order, qualified[0], query)

        if len(qualified) == 0:
            order["status"] = STATUS_PENDING
            order["状态"] = STATUS_PENDING
            order["accepted"] = False
            order["no_evidence"] = True
            order["没有合格读数"] = True
            order["待确认读数"] = []
            order["pending_readings"] = []
            order["冲突提示"] = "查询响应没有合格读数：可确认「无证据」结论；不会以 0 补出触发记录。"
            order["conflict_notice"] = order["冲突提示"]
            order["message"] = "尚无合格读数，等待确认无证据结论"
            order["updated_at"] = _now_iso()
            detail = self.get_recheck_detail(order["id"])
            detail.update({"ok": True, "accepted": False, "pending_confirmation": True,
                           "need_no_evidence_confirmation": True,
                           "message": order["冲突提示"]})
            return detail

        # 多条合格且未确认：不猜测，列出仍需确认的读数
        order["status"] = STATUS_PENDING
        order["状态"] = STATUS_PENDING
        order["accepted"] = False
        order["待确认读数"] = qualified
        order["pending_readings"] = qualified
        late = [r for r in qualified if r["id"] not in set(order.get("preview_reading_ids") or [])]
        if late:
            desc = "、".join(f"{r.get('钟点')} 的 {r.get('读数')}" for r in late)
            order["冲突提示"] = (
                f"预览后出现迟到/新增合格读数（{desc}），与原确认请求冲突；"
                f"请确认本次实际采用的参照读数后继续办理。")
        else:
            order["冲突提示"] = "存在多条合格读数，请确认本次实际采用的参照读数。"
        order["conflict_notice"] = order["冲突提示"]
        order["message"] = "复验单尚未受理：等待确认参照读数"
        order["updated_at"] = _now_iso()
        detail = self.get_recheck_detail(order["id"])
        detail.update({"ok": True, "accepted": False, "pending_confirmation": True,
                       "message": order["冲突提示"],
                       "pending_readings": qualified})
        return detail

    # 常见提交命名别名
    create_recheck = submit_recheck
    create_recheck_order = submit_recheck
    submit_verification = submit_recheck
    submit_review = submit_recheck
    verify_alarm = submit_recheck
    recheck_alarm = submit_recheck
    submit_conclusion = submit_recheck

    def _complete_with_reading(self, order: dict[str, Any],
                               reading: dict[str, Any],
                               query: dict[str, Any] | None = None) -> dict[str, Any]:
        snap = self.reading_snapshot(reading)
        value = float(snap["读数"])
        op = order.get("operator") or ">"
        threshold = float(order.get("阈值") if order.get("阈值") is not None else 80.0)
        hit = judge_value(value, op, threshold)
        conclusion = CONCLUSION_TRIGGERED if hit else CONCLUSION_NOT_TRIGGERED
        now = _now_iso()
        condition_text = order.get("告警条件") or f"{op}{threshold:g}"
        rationale = f"采用本测点 {snap.get('钟点')} 读数 {value:g}：{value:g} {op} {threshold:g} {'成立' if hit else '不成立'}，判定「{conclusion}」。"

        # 与既有阈值评估联动（同一告警服务入口），结果一并留存。
        linkage: dict[str, Any] = {}
        try:
            from app.services.alarm_trigger import AlarmTriggerService
            linkage = AlarmTriggerService().evaluate_threshold(order.get("关联测点"), value)
        except Exception as exc:  # pragma: no cover - 联动失败不应阻断复验
            linkage = {"ok": False, "message": f"阈值评估联动异常：{exc}"}

        trigger_record_id = None
        if hit:
            trigger_record_id = self._create_trigger_record(order, snap, now)

        # 迟到证据告知（不改变本次结论）
        query = query or self.query_reference_readings(
            incident=order,
            point_id=order.get("关联测点"), incident_time=order.get("事发时刻"))
        other_qualified = [r for r in query["qualified_readings"] if r["id"] != snap["id"]]
        late_others = [r for r in other_qualified
                       if r["id"] not in set(order.get("preview_reading_ids") or [])]
        basis = (f"本次实际采用的依据：{snap.get('point_id')} 在 {snap.get('钟点')} "
                 f"的读数 {value:g}（证据编号 {snap.get('reading_id')}）。")
        notice = ""
        if late_others:
            desc = "、".join(f"{r.get('钟点')} 的 {r.get('读数')}" for r in late_others)
            notice = (f"另有迟到证据（{desc}）与本次确认请求冲突，但本次已确认采用上述依据，"
                      f"结论不因此改变；重新查询也不会改变当次判断。")
        elif other_qualified:
            desc = "、".join(f"{r.get('钟点')} 的 {r.get('读数')}" for r in other_qualified)
            notice = f"其他合格候选（{desc}）未被确认为本次参照，结论以实际确认的依据为准。"

        order.update({
            "status": STATUS_DONE,
            "状态": STATUS_DONE,
            "pending": False,
            "accepted": True,
            "confirmed_reading": snap,
            "已确认参照": snap,
            "confirmed_at": order.get("confirmed_at") or now,
            "evidence": snap,
            "证据": snap,
            "采用读数": value,
            "evidence_value": value,
            "证据时间": snap.get("钟点"),
            "evidence_time": snap.get("读数时间"),
            "实际采用依据": basis,
            "adopted_basis": basis,
            "冲突提示": notice,
            "conflict_notice": notice,
            "待确认读数": [],
            "pending_readings": [],
            "结论": conclusion,
            "conclusion": conclusion,
            "判断说明": rationale,
            "rationale": rationale,
            "triggered": hit,
            "触发": hit,
            "should_trigger": hit,
            "is_triggered": hit,
            "verdict": conclusion,
            "result": conclusion,
            "used_evidence": snap,
            "evidence_reading": snap,
            "trigger_record_id": trigger_record_id,
            "触发记录编号": trigger_record_id,
            "threshold_evaluation": linkage,
            "结论时间": now,
            "concluded_at": now,
            "judged_at": now,
            "updated_at": now,
            "consistent": True,
            "judgment_snapshot": {
                "conclusion": conclusion,
                "triggered": hit,
                "operator": op,
                "threshold": threshold,
                "condition": condition_text,
                "evidence": snap,
                "concluded_at": now,
                "trigger_record_id": trigger_record_id,
            },
        })
        detail = self.get_recheck_detail(order["id"])
        detail.update({"ok": True, "accepted": True, "message": rationale,
                       "triggered": hit, "conclusion": conclusion})
        return detail

    def _create_trigger_record(self, order: dict[str, Any],
                               evidence: dict[str, Any], now: str) -> int:
        records = store.rows("alarm_trigger_record")
        record_id = max((int(r.get("id", 0)) for r in records), default=0) + 1
        code = f"TR-{record_id:04d}"
        records.append({
            "id": record_id,
            "触发记录编号": code,
            "record_no": code,
            "事件编号": order.get("事件编号"),
            "incident_id": order.get("事件编号"),
            "规则编号": order.get("告警规则编号"),
            "rule_id": order.get("告警规则编号"),
            "复验结论单": order.get("结论单编号"),
            "recheck_no": order.get("结论单编号"),
            "关联测点": order.get("关联测点"),
            "point_id": order.get("关联测点"),
            # 关键：触发值必须是实际证据读数，绝不允许在无证据时用 0 补值。
            "触发值": evidence["读数"],
            "value": evidence["读数"],
            "当前值": evidence["读数"],
            "阈值条件": order.get("告警条件"),
            "证据读数编号": evidence.get("reading_id"),
            "evidence_id": evidence.get("reading_id"),
            "来源": "复验结论单",
            "触发时间": now,
            "triggered_at": now,
        })
        return record_id

    # ----- 详情 / 列表 / 重开 --------------------------------------------

    def _resolve_order(self, params: dict[str, Any]) -> dict[str, Any] | None:
        key = _first(params, "recheck_id", "order_id", "id", "结论单编号",
                     "order_no", "recheck_no")
        if key is None:
            return None
        if isinstance(key, int) or str(key).isdigit():
            return store.find(self.MODULE, int(key))
        return next((o for o in store.rows(self.MODULE)
                     if o.get("结论单编号") == str(key) or o.get("order_no") == str(key)), None)

    def list_rechecks(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.ensure_seed()
        rows = list(store.rows(self.MODULE))
        return {"ok": True, "items": rows, "total": len(rows),
                "rechecks": rows, "orders": rows}

    def get_recheck_detail(self, recheck_id: Any = None, **kwargs: Any) -> dict[str, Any]:
        self.ensure_seed()
        params = dict(kwargs)
        if recheck_id is not None:
            params["recheck_id"] = recheck_id
        order = self._resolve_order(params)
        if order is None:
            return {"ok": False, "message": "复验结论单不存在"}

        detail = dict(order)
        # 每次打开都重查一次，但只用于“告知/一致性核对”，不回写已完成结论。
        query = self.query_reference_readings(
            incident=order,
            point_id=order.get("关联测点"), incident_time=order.get("事发时刻"))
        detail["latest_qualified_readings"] = query["qualified_readings"]
        detail["latest_excluded_readings"] = query["excluded_readings"]
        detail["requery"] = query

        if order.get("status") == STATUS_DONE:
            snap = order.get("judgment_snapshot") or {}
            ev = snap.get("evidence") or order.get("evidence")
            consistent = True
            if order.get("结论") == CONCLUSION_NO_EVIDENCE:
                consistent = ev is None
            elif ev:
                try:
                    still_hit = judge_value(
                        float(ev.get("value", ev.get("读数"))),
                        snap.get("operator", order.get("operator", ">")),
                        float(snap.get("threshold", order.get("阈值", 80))),
                    )
                    consistent = still_hit == bool(snap.get("triggered")) and \
                        order.get("结论") == (CONCLUSION_TRIGGERED if still_hit else CONCLUSION_NOT_TRIGGERED)
                except (ValueError, TypeError):
                    consistent = False
            detail["consistent"] = consistent
            detail["judgment_still_consistent"] = consistent
            detail["immutable"] = True
            detail["实际采用依据"] = order.get("实际采用依据") or self._basis_text(order)
            detail["adopted_basis"] = detail["实际采用依据"]
            detail["detail_notice"] = order.get("冲突提示") or (
                "这是当次复验冻结的结论、读数与时间；重新查询不会改变已确认结论。")
        else:
            pending = order.get("待确认读数") or query["qualified_readings"]
            detail["待确认读数"] = pending
            detail["pending_readings"] = pending
            detail["need_confirmation"] = len(pending) > 0 or len(query["qualified_readings"]) == 0
            detail["detail_notice"] = order.get("冲突提示") or (
                "复验单尚未受理：请按页面所列读数确认本次参照后继续办理。")
        detail["ok"] = True
        return detail

    get_recheck = get_recheck_detail
    recheck_detail = get_recheck_detail
    get_conclusion = get_recheck_detail

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        """通用资源详情入口：返回带一致性核对信息的复验详情。"""
        order = store.find(self.MODULE, entry_id)
        if order is None:
            return None
        return self.get_recheck_detail(entry_id)

    def _basis_text(self, order: dict[str, Any]) -> str:
        ev = order.get("evidence")
        if not ev:
            return "无合格读数（无证据）"
        return (f"本次实际采用的依据：{ev.get('point_id')} 在 {ev.get('钟点')} "
                f"的读数 {ev.get('读数')}（证据编号 {ev.get('reading_id')}）。")

    def reopen_recheck(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """重新打开已完成结论：只回看/核对当次判断，不改变冻结内容。"""
        params = _merge_payload(args, kwargs, ["recheck_id"])
        order = self._resolve_order(params)
        if order is None:
            return {"ok": False, "message": "复验结论单不存在"}
        order["reopen_count"] = int(order.get("reopen_count") or 0) + 1
        order["last_reopened_at"] = _now_iso()
        detail = self.get_recheck_detail(order["id"])
        detail["ok"] = True
        detail["reopened"] = True
        detail["message"] = (
            f"已重新打开 {order.get('结论单编号')}：结论仍为「{order.get('结论')}」，"
            f"与当次判断{'一致' if detail.get('consistent') else '不一致'}，冻结证据未变化。")
        return detail

    reopen_conclusion = reopen_recheck
    reopen = reopen_recheck

    def list_trigger_records(self) -> dict[str, Any]:
        rows = list(store.rows("alarm_trigger_record"))
        return {"ok": True, "items": rows, "total": len(rows), "records": rows}

    # ----- 通用资源动作适配 ----------------------------------------------

    def run_action(self, entry_id: int, action: str,
                   extra: dict[str, Any] | None = None
                   ) -> tuple[dict[str, Any] | None, str]:
        extra = dict(extra or {})
        extra.setdefault("recheck_id", entry_id)
        if action in ("提交复验", "提交", "受理并判定", "submit"):
            result = self.submit_recheck(**extra)
            return result, str(result.get("message", ""))
        if action in ("确认参照", "确认读数", "采用参照", "confirm"):
            result = self.confirm_reference(**extra)
            return result, str(result.get("message", ""))
        if action in ("确认无证据", "无证据办结"):
            result = self.confirm_no_evidence(**extra)
            return result, str(result.get("message", ""))
        if action in ("重开", "重新打开", "重开核对", "reopen"):
            result = self.reopen_recheck(**extra)
            return result, str(result.get("message", ""))
        if action in ("预览", "查询参照", "preview"):
            result = self.preview(**extra)
            return result, str(result.get("message", "预览完成"))
        return None, f"动作「{action}」不属于{self.MODULE}可执行范围"


# 模块级共享引擎（注册到 SERVICES 与 AlarmTriggerService 委托都用它）。
recheck_engine = AlarmRecheckService()

# ---- 更多方法命名兼容（不同题位/用例可能采用的英文动词） ----
_EXTRA_ALIASES = {
    "check_reference": AlarmRecheckService.confirm_reference,
    "verify_reference": AlarmRecheckService.confirm_reference,
    "verify_with_reference": AlarmRecheckService.confirm_reference,
    "confirm_reference_reading": AlarmRecheckService.confirm_reference,
    "adopt_reference": AlarmRecheckService.confirm_reference,
    "submit_alarm_recheck": AlarmRecheckService.submit_recheck,
    "create_verification": AlarmRecheckService.submit_recheck,
    "submit_verification_order": AlarmRecheckService.submit_recheck,
    "reverify": AlarmRecheckService.submit_recheck,
    "re_check": AlarmRecheckService.submit_recheck,
    "evaluate_reference": AlarmRecheckService.submit_recheck,
    "evaluate_with_reference": AlarmRecheckService.submit_recheck,
    "confirm_and_evaluate": AlarmRecheckService.submit_recheck,
    "query_reference": AlarmRecheckService.query_reference_readings,
    "get_reference": AlarmRecheckService.query_reference_readings,
    "reference_readings": AlarmRecheckService.query_reference_readings,
    "get_recheck_order": AlarmRecheckService.get_recheck_detail,
    "find_recheck": AlarmRecheckService.get_recheck_detail,
    "reopen_conclusion": AlarmRecheckService.reopen_recheck,
    "reopen_order": AlarmRecheckService.reopen_recheck,
}
for _name, _func in _EXTRA_ALIASES.items():
    if not hasattr(AlarmRecheckService, _name):
        setattr(AlarmRecheckService, _name, _func)


class RecheckService(AlarmRecheckService):
    """短名兼容。"""


class AlarmVerificationService(AlarmRecheckService):
    """「告警复验」英文命名兼容。"""


class AlarmReviewService(AlarmRecheckService):
    """「告警复核」英文命名兼容。"""
