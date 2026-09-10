"""天气取数能力（`agent.productivity.weather`）

## 数据源：Open-Meteo

选它的理由（对应"配置驱动、可替换"的设计原则）：

| 维度 | Open-Meteo | 商业天气 API |
|------|-----------|-------------|
| 密钥 | **不需要** | 需要申请 + 存进 config.yaml |
| 账号 | 不需要 | 需要 |
| 计费 | 免费额度内 | 按调用量 |

对一个桌面宠物来说，"为了查天气先让用户去注册一个 API key"是很差的体验，
而把密钥写进 `config.yaml` 又多一处泄漏面。

## 隐私取舍（有意为之）

**不做 IP 定位**：只把**城市名**发给天气服务，不把用户 IP 交给第三方。
代价是"今天天气怎么样"（没说城市）无法自动推断，此时如实返回 None 让工具追问，
或者由 `agent.weather.default_city` 配置一个常用城市。

## 契约

`(city) -> Optional[dict]`，dict 形如
`{"city": "上海", "desc": "多云", "temp": 18, "temp_range": "12~21度"}`
（字段名与 `agent/tools/productivity_tools.py` 的 `WeatherTool` 约定一致）。
**失败一律返回 None**，绝不抛异常 —— 查不到天气不该让用户的操作失败。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

#: 地理编码（城市名 → 经纬度）
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

#: 当前天气 + 当日温度区间
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: 请求超时（秒）
_TIMEOUT = 8

#: WMO 天气代码 → 中文描述（Open-Meteo 用 WMO 4677 编码）
WMO_CODES: Dict[int, str] = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}


def describe_code(code: Any) -> str:
    """WMO 代码 → 中文描述（未知代码如实说"未知"而不是猜一个）"""
    try:
        return WMO_CODES.get(int(code), "未知天气")
    except (TypeError, ValueError):
        return "未知天气"


def make_open_meteo_weather(default_city: str = "") -> Callable[[str], Optional[Dict[str, Any]]]:
    """构造天气取数函数

    Args:
        default_city: 用户没说城市时用的默认城市；空串表示"必须问清楚"

    Returns:
        `(city) -> Optional[dict]`，任何失败都返回 None
    """

    def weather(city: str) -> Optional[Dict[str, Any]]:
        name = str(city or "").strip() or str(default_city or "").strip()
        if not name:
            logger.info("[weather] 未给城市且无默认城市，交由工具追问")
            return None

        try:
            import requests
        except Exception as e:
            # 没有 pragma：这一支由测试用 `sys.modules["requests"] = None`
            # 逼出来（导入失败 → ImportError），而不是屏蔽掉它。
            logger.warning("[weather] requests 不可用: %s", e)
            return None

        try:
            geo = requests.get(
                _GEOCODE_URL,
                params={"name": name, "count": 1, "language": "zh", "format": "json"},
                timeout=_TIMEOUT,
            )
            if geo.status_code != 200:
                logger.warning("[weather] 地理编码 HTTP %s", geo.status_code)
                return None
            hits = (geo.json() or {}).get("results") or []
            if not hits:
                logger.info("[weather] 找不到城市: %s", name)
                return None

            place = hits[0]
            lat, lon = place.get("latitude"), place.get("longitude")
            if lat is None or lon is None:
                return None

            resp = requests.get(
                _FORECAST_URL,
                params={
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto", "forecast_days": 1,
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning("[weather] 取数 HTTP %s", resp.status_code)
                return None

            data = resp.json() or {}
            current = data.get("current") or {}
            daily = data.get("daily") or {}

            temp = current.get("temperature_2m")
            if temp is None:
                return None

            out: Dict[str, Any] = {
                "city": place.get("name") or name,
                "desc": describe_code(current.get("weather_code")),
                "temp": round(float(temp)),
            }

            highs = daily.get("temperature_2m_max") or []
            lows = daily.get("temperature_2m_min") or []
            if highs and lows:
                out["temp_range"] = f"{round(float(lows[0]))}~{round(float(highs[0]))}度"

            logger.info("[weather] %s → %s %s度", out["city"], out["desc"], out["temp"])
            return out

        except Exception as e:                          # 网络/JSON/结构异常统一兜底
            logger.warning("[weather] 查询失败: %s", e)
            return None

    return weather
