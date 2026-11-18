"""RunningHub 积分消耗参考费率与任务估算。

当前积分版 API 文档没有公布通用工作流的按秒费率。此处数值是旧费率的项目参考值，
仅用于粗略估算，不保证与新积分账单一致。实际消耗以 API 返回值或 RunningHub 账单为准。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console


PRICING_SOURCE = "https://www.runninghub.cn/runninghub-api-doc-cn/doc-8287334"


@dataclass(frozen=True, slots=True)
class MachineTier:
    """消费级 / 共享机的历史按秒参考档位。"""

    key: str
    label: str
    vram: str
    coins_per_sec: float  # 旧版字段名；作为积分的历史参考值，不是官方新费率。


# 历史估算值（积分/秒）。API 新账单的实际积分口径需以 RunningHub 返回为准。
MACHINE_TIERS: tuple[MachineTier, ...] = (
    MachineTier("standard", "Standard 标准", "24G", 0.2),
    MachineTier("plus", "Plus", "48G", 0.4),
)

# 企业级专享机历史报价（¥/小时）—— 按秒结算、并发任务累加计费，仅供参考。
ENTERPRISE_HOURLY_YUAN: tuple[tuple[str, float], ...] = (
    ("Lite", 0.4),
    ("Standard 24G", 4.0),
    ("Plus 48G", 6.0),
)

DEFAULT_TIER = "standard"

_TIER_BY_KEY: dict[str, MachineTier] = {t.key: t for t in MACHINE_TIERS}


def get_tier(key: str) -> MachineTier | None:
    return _TIER_BY_KEY.get(key.strip().lower())


def estimate_coins(seconds: float | str | int | None, tier: str = DEFAULT_TIER) -> float | None:
    """按历史参考值估算某档位积分消耗；无法计算时返回 None。"""
    t = get_tier(tier)
    if t is None:
        return None
    try:
        secs = float(seconds)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    if secs <= 0:
        return None
    return round(secs * t.coins_per_sec, 2)


def pricing_dict(seconds: float | None = None) -> dict[str, Any]:
    """机器可读的费率表（供 --json 使用）。"""
    tiers: list[dict[str, Any]] = []
    for t in MACHINE_TIERS:
        entry: dict[str, Any] = {
            "key": t.key,
            "label": t.label,
            "vram": t.vram,
            "points_per_sec": t.coins_per_sec,
            "points_per_min": round(t.coins_per_sec * 60, 2),
            "points_per_hour": round(t.coins_per_sec * 3600, 2),
            "coins_per_sec": t.coins_per_sec,
            "coins_per_min": round(t.coins_per_sec * 60, 2),
            "coins_per_hour": round(t.coins_per_sec * 3600, 2),
        }
        if seconds is not None:
            entry["estimate_points"] = estimate_coins(seconds, t.key)
            entry["estimate_coins"] = entry["estimate_points"]
        tiers.append(entry)
    return {
        "unit": "积分 (points)",
        "billing": "旧费率参考估算，不代表当前积分版官方费率；仅任务执行时可能产生消耗",
        "seconds": seconds,
        "tiers": tiers,
        "enterprise_hourly_yuan": [{"tier": name, "yuan_per_hour": y} for name, y in ENTERPRISE_HOURLY_YUAN],
        "source": PRICING_SOURCE,
    }


def render_pricing(console: "Console", seconds: float | None = None) -> None:
    """以表格形式打印费率；给了 seconds 则附上各档位预估花费。"""
    from rich.table import Table

    title = "RunningHub 积分消耗参考估算"
    if seconds is not None:
        title += f" · 按 {seconds:g}s 预估"
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("机型")
    table.add_column("显存")
    table.add_column("积分/秒", justify="right")
    table.add_column("积分/分", justify="right")
    table.add_column("积分/时", justify="right")
    if seconds is not None:
        table.add_column(f"预估({seconds:g}s)", justify="right", style="green")

    for t in MACHINE_TIERS:
        row = [
            t.label,
            t.vram,
            f"{t.coins_per_sec:g}",
            f"{t.coins_per_sec * 60:g}",
            f"{t.coins_per_sec * 3600:g}",
        ]
        if seconds is not None:
            est = estimate_coins(seconds, t.key)
            row.append(f"{est:g} 积分" if est is not None else "—")
        table.add_row(*row)

    console.print(table)

    hourly = "，".join(f"{name} ¥{y:g}" for name, y in ENTERPRISE_HOURLY_YUAN)
    console.print("[dim]· 表中是旧版费率的项目参考值，当前积分版消耗可能不同；请以 API 返回值或账单为准。[/dim]")
    console.print(f"[dim]· 企业级专享机历史人民币报价（仅参考）：{hourly} / 小时。[/dim]")
    console.print(f"[dim]· RunningHub API 文档：{PRICING_SOURCE}[/dim]")
