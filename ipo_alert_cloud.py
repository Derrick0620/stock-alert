# -*- coding: utf-8 -*-
"""
IPO 申购提醒 - GitHub Actions 版
数据源：东方财富公开 API（零第三方依赖）
环境变量：PUSHPLUS_TOKEN、PUSHPLUS_TOPIC
逻辑：当天有新股/新债申购才推送，没有则静默
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date, timedelta


EM_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
PUSHPLUS_URL = "http://www.pushplus.plus/send"

# 2026 年 A 股法定休市日（沪深交易所休市安排）
HOLIDAYS_2026 = {
    # 元旦
    "2026-01-01", "2026-01-02",
    # 春节
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-23", "2026-02-24",
    # 清明
    "2026-04-06",
    # 劳动节
    "2026-05-01", "2026-05-04", "2026-05-05",
    # 端午
    "2026-06-19",
    # 中秋 + 国庆
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
}


def is_trading_day(d=None):
    """判断是否为 A 股交易日（排除周末与法定节假日）"""
    if d is None:
        d = date.today()
    if d.weekday() >= 5:
        return False
    if d.strftime("%Y-%m-%d") in HOLIDAYS_2026:
        return False
    return True


def http_get_json(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post_json(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def get_ipo_data():
    """获取新股申购数据（取最近 100 条，避免被历史记录挤占）"""
    try:
        d = http_get_json(EM_API, {
            "reportName": "RPTA_APP_IPOAPPLY", "columns": "ALL",
            "sortColumns": "APPLY_DATE", "sortTypes": "-1",
            "pageSize": "100", "pageNumber": "1",
            "source": "WEB", "client": "WEB",
        })
        return d["result"]["data"] if d.get("success") else []
    except Exception as e:
        print("IPO data error:", e)
        return []


def get_cb_data():
    """获取可转债发行数据（取最近 100 条）"""
    try:
        d = http_get_json(EM_API, {
            "reportName": "RPT_BOND_CB_LIST", "columns": "ALL",
            "sortColumns": "PUBLIC_START_DATE", "sortTypes": "-1",
            "pageSize": "100", "pageNumber": "1",
            "source": "WEB", "client": "WEB",
        })
        return d["result"]["data"] if d.get("success") else []
    except Exception as e:
        print("CB data error:", e)
        return []


def fmt_market(code):
    c = str(code)
    if c.startswith("688"):
        return "科创板"
    if c.startswith("300") or c.startswith("301"):
        return "创业板"
    if c.startswith("920"):
        return "北交所"
    if c.startswith("60"):
        return "沪市主板"
    if c.startswith("00"):
        return "深市主板"
    return ""


def fmt_scale(s):
    try:
        v = float(s)
        return f"{v:.2f}亿" if v >= 1 else f"{v*10000:.0f}万"
    except Exception:
        return "-"


def build_msg(stocks, bonds):
    today = date.today()
    wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]
    lines = [f"<b>{today.strftime('%m月%d日')} {wd} IPO申购提醒</b>", ""]

    if stocks:
        lines.append("<b>🏢 新股申购</b>")
        for s in stocks:
            p = f"{s['price']}元" if s["price"] else "待定"
            lines.append(f"  {s['name']} | {s['market']}")
            lines.append(f"  代码: {s['code']} | 发行价: {p}")
        lines.append("")

    if bonds:
        lines.append("<b>💰 新债申购</b>")
        for b in bonds:
            lines.append(f"  {b['name']} | {b['rating']}")
            lines.append(f"  正股: {b['stock_name']} | 申购: {b['apply_code']}")
            lines.append(f"  规模: {b['scale']}")
        lines.append("")

    lines.append("—— 自动推送 · 仅供参考 ——")
    return "<br>".join(lines)


def norm_date(v):
    """统一日期格式为 YYYY-MM-DD，兼容 None / 时间戳字符串"""
    if not v:
        return ""
    s = str(v).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def main():
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    topic = os.environ.get("PUSHPLUS_TOPIC", "")
    if not token or not topic:
        print("[ERROR] 环境变量 PUSHPLUS_TOKEN / PUSHPLUS_TOPIC 未配置")
        sys.exit(1)

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    print(f"[INFO] 检查日期: {today_str} (周{'一二三四五六日'[today.weekday()]})")

    # 非交易日不推送（周末 + 法定节假日）
    if not is_trading_day(today):
        print("[INFO] 非交易日（周末或法定节假日），不推送")
        return

    # 同时匹配「今天」和「未来 3 个自然日」，避免数据更新时间差导致漏推
    watch_dates = {(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)}
    print(f"[INFO] 扫描申购日范围: {sorted(watch_dates)}")

    stocks = []
    for item in get_ipo_data():
        apply_date = norm_date(item.get("APPLY_DATE"))
        if apply_date in watch_dates:
            p = item.get("ISSUE_PRICE")
            try:
                price_ok = p and float(p) != 0
            except (TypeError, ValueError):
                price_ok = False
            stocks.append({
                "date": apply_date,
                "code": item.get("SECURITY_CODE", ""),
                "name": item.get("SECURITY_NAME", ""),
                "price": p if price_ok else None,
                "market": fmt_market(item.get("SECURITY_CODE", "")),
            })

    bonds = []
    for item in get_cb_data():
        pub_date = norm_date(item.get("PUBLIC_START_DATE"))
        if pub_date in watch_dates:
            bonds.append({
                "date": pub_date,
                "code": item.get("SECURITY_CODE", ""),
                "name": item.get("SECURITY_NAME_ABBR", ""),
                "apply_code": item.get("CORRECODE", ""),
                "stock_name": item.get("SECURITY_SHORT_NAME", "") or item.get("SECURITY_NAME_ABBR", ""),
                "scale": fmt_scale(item.get("ACTUAL_ISSUE_SCALE", 0)),
                "rating": item.get("RATING", ""),
            })

    # 只推送「今天」的标的；若今天为空但未来 3 天有，则不推送（避免周末/节前误推）
    today_stocks = [s for s in stocks if s["date"] == today_str]
    today_bonds = [b for b in bonds if b["date"] == today_str]

    if not today_stocks and not today_bonds:
        print(f"[INFO] 今日无申购（扫描范围内共 {len(stocks)} 新股 / {len(bonds)} 新债，均非今日），不推送")
        return

    try:
        http_post_json(PUSHPLUS_URL, {
            "token": token, "title": "IPO申购提醒",
            "content": build_msg(today_stocks, today_bonds),
            "template": "html", "topic": topic,
        })
        print(f"[OK] 推送成功: 新股{len(today_stocks)} 新债{len(today_bonds)}")
    except Exception as e:
        print(f"[ERROR] 推送失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
