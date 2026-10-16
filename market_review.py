# -*- coding: utf-8 -*-
"""
A股每日复盘 - GitHub Actions 版
数据源：新浪财经 + 东方财富公开 API（零第三方依赖）
环境变量：PUSHPLUS_TOKEN、PUSHPLUS_TOPIC
逻辑：仅工作日推送
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date


PUSHPLUS_URL = "http://www.pushplus.plus/send"
SINA_URL = "https://hq.sinajs.cn/list=sh000001,sz399001,sz399006,int_dji,int_nasdaq,int_sp500,hf_GC,hf_CL,hf_XAU,hf_XAG,fx_susdcnh"
EM_SECTOR = "http://push2.eastmoney.com/api/qt/clist/get"
SINA_NEWS = "https://feed.mix.sina.com.cn/api/roll/get"

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


def http_get_json(url, params=None, headers=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    h = {"User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def http_get_text(url, headers=None):
    h = {"User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("gbk", errors="ignore")


def http_post_json(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def pct(v):
    try:
        return f"{'+' if float(v) >= 0 else ''}{float(v):.2f}%"
    except Exception:
        return str(v)


def num(v):
    try:
        v = float(v)
        return f"{v/1e8:.0f}亿" if abs(v) >= 1e8 else f"{v/1e4:.0f}万" if abs(v) >= 1e4 else f"{v:.0f}"
    except Exception:
        return str(v)


def emoji(i):
    return ["🏅", "🥈", "🥉"][i] if i < 3 else "  "


def is_junk(name):
    return any(k in name for k in ["昨日连板", "涨停", "炸板", "跌停", "高换手"])


def get_indices():
    try:
        txt = http_get_text(SINA_URL, headers={"Referer": "https://finance.sina.com.cn"})
        results = {}
        for line in txt.strip().split("\n"):
            if "hq_str_" not in line or '="' not in line:
                continue
            code = line.split('="')[0].replace("var hq_str_", "")
            d = line.split('="')[1].rstrip('";').split(",")
            if len(d) < 6:
                continue
            price = float(d[3]); prev = float(d[2])
            results[code] = {
                "name": d[0], "price": price,
                "chg_pct": (price - prev) / prev * 100,
                "amount": float(d[8]) if len(d) > 8 and d[8] else 0,
            }
        return results
    except Exception as e:
        print("indices error:", e)
        return {}


def get_sectors(group, count, desc=True):
    try:
        d = http_get_json(EM_SECTOR, {
            "pn": "1", "pz": str(count), "po": "1" if desc else "0", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": f"m:90+t:{group}", "fields": "f14,f3",
        }, headers={"Referer": "https://quote.eastmoney.com/"})
        return d["data"]["diff"] if d.get("rc") == 0 else []
    except Exception as e:
        print("sector error:", e)
        return []


def get_news():
    try:
        all_news = []
        for lid in [2517, 2510, 2509]:
            d = http_get_json(SINA_NEWS, {
                "pageid": 153, "lid": lid, "k": "", "num": 5, "page": 1
            }, headers={"Referer": "https://finance.sina.com.cn"})
            for item in d.get("result", {}).get("data", []):
                t = item.get("title", "").strip()
                if len(t) < 10 or "彩票" in t or "胜负彩" in t:
                    continue
                all_news.append({"title": t, "time": int(item.get("ctime", 0))})
        seen = set(); uniq = []
        for n in sorted(all_news, key=lambda x: x["time"], reverse=True):
            if n["title"] not in seen:
                seen.add(n["title"]); uniq.append(n)
        return uniq[:5]
    except Exception as e:
        print("news error:", e)
        return []


def make_advice(indices, sectors_up, concepts_up):
    if not indices:
        return "⚠️ 数据异常，建议观望"
    sh = indices.get("sh000001", {}); sz = indices.get("sz399001", {}); cy = indices.get("sz399006", {})
    chgs = [sh.get("chg_pct", 0), sz.get("chg_pct", 0), cy.get("chg_pct", 0)]
    total = sum(v.get("amount", 0) for v in indices.values())
    up = sum(1 for c in chgs if c > 0.3); down = sum(1 for c in chgs if c < -0.3)
    top_avg = sum(float(s["f3"]) for s in sectors_up[:3]) / 3 if sectors_up else 0
    real_c = [c for c in concepts_up if not is_junk(c["f14"])]
    concept_avg = sum(float(c["f3"]) for c in real_c[:3]) / 3 if real_c else 0

    L = ["━━━ 📋 次日策略 ━━━", "", "<b>【市场诊断】</b>"]
    if up == 3: L.append("  趋势: 普涨，多头占优")
    elif up == 2: L.append("  趋势: 结构性行情，指数分化")
    elif down == 2: L.append("  趋势: 偏弱调整，防御为主")
    elif down == 3: L.append("  趋势: 全线下跌")
    else: L.append("  趋势: 横盘震荡")
    if total > 2e12: L.append("  量能: 放量(>2万亿)")
    elif total > 1e12: L.append("  量能: 适中(1-2万亿)")
    else: L.append("  量能: 缩量(<1万亿)")
    hot_s = ", ".join(s["f14"] for s in sectors_up[:3]) if sectors_up else "暂无"
    L.append(f"  主线: {hot_s}")

    L += ["", "<b>【长线策略】</b>"]
    if up == 3 and total > 2e12:
        L.append("  放量普涨，适合逢回调分批建仓")
        L.append(f"  关注: {hot_s} 等龙头")
    elif up >= 2:
        L.append("  结构性机会，精选强势板块布局")
        L.append(f"  优先: {hot_s}")
        L.append("  注意: 高位勿追，等回踩再进")
    elif down >= 2:
        L.append("  防御阶段，耐心等待企稳")
        L.append("  关注: 公用事业、医药、消费")
    else:
        L.append("  方向不明，不急于进场")

    L += ["", "<b>【短线参考】</b>"]
    hot_c = ", ".join(c["f14"] for c in real_c[:3]) if real_c else "暂无"
    if up == 3 and total > 2e12 and top_avg > 3:
        L.append("  强势+主线明确，短线机会较好")
        L.append(f"  方向: {hot_c}")
        L.append("  策略: 强势股回调至5日线可博弈")
    elif up >= 2 and concept_avg > 2:
        L.append("  局部热点活跃，可轻仓参与")
        L.append(f"  方向: {hot_c}")
    elif down >= 2:
        L.append("  市场走弱，短线建议休息")
    else:
        L.append("  震荡行情，多看少动")

    L += ["", "<b>【风控提示】</b>"]
    if down == 3:
        L.append("  极端行情暂不加仓，保留5成现金")
    elif abs(sum(chgs)/3) < 0.5:
        L.append("  窄幅震荡，控制仓位等变盘")
    else:
        L.append("  正常波动，做好止盈止损")

    L += ["", "  ⚠️ 仅供参考，投资有风险"]
    return "<br>".join(L)


def build():
    today = date.today()
    wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]

    indices = get_indices()
    sectors_up = get_sectors("2", 5, True)
    sectors_down = get_sectors("2", 3, False)
    concepts = get_sectors("3", 5, True)
    news = get_news()

    total_amt = sum(v.get("amount", 0) for v in indices.values())

    L = [f"<b>{today.strftime('%m月%d日')} {wd} A股收盘复盘</b>", ""]

    if indices:
        L.append("<b>▍三大指数</b>")
        for code, nm in [("sh000001", "上证"), ("sz399001", "深证"), ("sz399006", "创业")]:
            it = indices.get(code)
            if it:
                c = "🔴" if it["chg_pct"] > 0 else "🟢" if it["chg_pct"] < 0 else "⚪"
                L.append(f"  {c} {nm} {it['price']:.0f}  {pct(it['chg_pct'])}")
        L.append(f"  成交 {num(total_amt)}")
        L.append("")

    if sectors_up:
        L.append("<b>🏆 行业涨幅 TOP5</b>")
        for i, s in enumerate(sectors_up):
            L.append(f"  {emoji(i)} {s['f14']}  {pct(s['f3'])}")
        L.append("")

    if sectors_down:
        L.append("<b>📉 行业跌幅 TOP3</b>")
        for i, s in enumerate(sectors_down):
            L.append(f"  {i+1}. {s['f14']}  {pct(s['f3'])}")
        L.append("")

    real_c = [c for c in concepts if not is_junk(c["f14"])]
    if real_c:
        L.append("<b>💡 热门概念</b>")
        for i, s in enumerate(real_c[:5]):
            L.append(f"  {emoji(i)} {s['f14']}  {pct(s['f3'])}")
        L.append("")

    # 国际视野
    intl = []
    us_map = {"道琼斯": "道指", "纳斯达克": "纳指", "标普指数": "标普"}
    us_parts = []
    for name, v in indices.items():
        if name in us_map:
            short = us_map[name]
            c = "🔴" if v["chg_pct"] > 0 else "🟢" if v["chg_pct"] < 0 else "⚪"
            us_parts.append(f"{c} {short} {v['price']:.0f} {pct(v['chg_pct'])}")
    if us_parts:
        intl.append("  ".join(us_parts))

    comm_map = {"纽约黄金": "黄金期货", "纽约原油": "原油", "伦敦金（现货黄金）": "现货金", "伦敦银（现货白银）": "现货银"}
    for name, v in indices.items():
        if name in comm_map:
            c = "🔴" if v["chg_pct"] > 0 else "🟢" if v["chg_pct"] < 0 else "⚪"
            intl.append(f"  {c} {comm_map[name]} ${v['price']:.1f} {pct(v['chg_pct'])}")
    if "离岸人民币（香港）" in indices:
        v = indices["离岸人民币（香港）"]
        c = "🔴" if v["chg_pct"] > 0 else "🟢" if v["chg_pct"] < 0 else "⚪"
        intl.append(f"  {c} 人民币 {v['price']:.4f} {pct(v['chg_pct'])}")
    if intl:
        L.append("<b>🌍 国际视野</b>")
        L.extend(intl)
        L.append("")

    if news:
        L.append("<b>📰 今日要闻</b>")
        for i, n in enumerate(news):
            L.append(f"  {i+1}. {n['title']}")
        L.append("")

    L.append(make_advice(indices, sectors_up, real_c if real_c else concepts))

    return "<br>".join(L)


def main():
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    topic = os.environ.get("PUSHPLUS_TOPIC", "")
    if not token or not topic:
        print("[ERROR] 环境变量 PUSHPLUS_TOKEN / PUSHPLUS_TOPIC 未配置")
        sys.exit(1)

    today = date.today()
    if not is_trading_day(today):
        print(f"[INFO] {today.strftime('%Y-%m-%d')} 非交易日（周末或法定节假日），不推送")
        return

    try:
        http_post_json(PUSHPLUS_URL, {
            "token": token, "title": "今日复盘",
            "content": build(), "template": "html", "topic": topic,
        })
        print("[OK] 推送成功")
    except Exception as e:
        print(f"[ERROR] 推送失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
