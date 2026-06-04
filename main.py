#!/usr/bin/env python3
"""
每日投融资日报 - 公众号兼容版 V5
基于烯牛数据真实字段名重写
数据来源：烯牛创投数据 MCP API
"""
import json
import os
import sys
import smtplib
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import urllib.request
import urllib.error

# ============ 配置 ============
XINIU_API_KEY = os.environ.get("XINIU_API_KEY", "")
XINIU_MCP_URL = f"http://vip.xiniudata.com/mcp?api_key={XINIU_API_KEY}"

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "liuchenghao@xiniudata.com")

# ============ MCP 客户端 ============
class XiniuMCPClient:
    def __init__(self, url):
        self.url = url
        self._request_id = 0

    def _next_id(self):
        self._request_id += 1
        return self._request_id

    def _call(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": self._next_id()}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                result = self._parse_sse(body)
                if result and "error" in result:
                    print(f"[MCP Error] {result['error']}", file=sys.stderr)
                    return None
                return result.get("result") if result else None
        except Exception as e:
            print(f"[MCP Error] {e}", file=sys.stderr)
            return None

    def _parse_sse(self, body):
        for line in body.split("\n"):
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:].strip())
                except json.JSONDecodeError:
                    continue
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def initialize(self):
        return self._call("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "daily-report-bot", "version": "5.0.0"}
        })

    def call_tool(self, tool_name, arguments):
        return self._call("tools/call", {"name": tool_name, "arguments": arguments})

    def get_data(self, req_params, limit=200):
        result = self.call_tool("get_data", {"req_params": req_params, "limit": limit})
        if result and isinstance(result, dict):
            content = result.get("content", [])
            if content:
                text = content[0].get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"rows": [], "count": 0, "raw": text}
        return None

# ============ 数据表 & 字段（真实字段名）============
INVEST_TABLE = "entity_invest_event.e_investor_entity_invest_firm"
FUND_TABLE = "entity_investor.e_fund"
LP_TABLE = "ai_chat.v_lp_invest_fund"

# 投融资事件 - 查询字段（英文key是查询用的）
INVEST_QUERY_COLUMNS = [
    "company_gs_name", "project_name", "invest_date",
    "fund_com_entity_gs_name", "share_percent", "fund_type_desc",
    "industry_name_select", "xn_industry_select",
    "company_province", "company_city",
    "company_brief_intro"
]

# 投融资事件 - 返回数据中文key映射
KEY_MAP = {
    "被投公司工商名称": "company_gs_name",
    "被投公司项目名称": "project_name",
    "投资日期": "invest_date",
    "公司类投资实体和基金工商名称": "fund_com_entity_gs_name",
    "出资比例(%)": "share_percent",
    "投资实体类型": "fund_type_desc",
    "被投公司所属赛道": "industry",
    "烯牛行业": "industry2",
    "公司地区(省)": "province",
    "公司地区(市)": "city",
    "公司一句话简介": "company_desc",
}

BASIC_QUERY_COLUMNS = [
    "company_gs_name", "project_name", "invest_date",
    "fund_com_entity_gs_name", "share_percent", "fund_type_desc"
]

# 基金表字段
FUND_QUERY_COLUMNS = [
    "firm_name", "investor_name", "fund_establish_date",
    "register_capital", "amac_status", "money_type",
    "fund_location_province", "fund_location_city",
    "xiniu_fund_type_select", "status"
]

# 基金表中文key映射
FUND_KEY_MAP = {
    "基金名称": "fund_name",
    "所属机构": "investor_name",
    "基金成立日期": "setup_date",
    "基金管理规模(元)": "target_size",
    "基金备案状态": "amac_status",
    "币种": "currency",
    "基金注册地址(省)": "province",
    "基金注册地址(市)": "city",
    "烯牛基金类型": "fund_type",
    "经营状态": "status",
}

# LP表字段
LP_QUERY_COLUMNS = [
    "lp_name", "fund_name", "invest_date",
    "lp_invest_amount", "lp_type_select", "lp_type_main",
    "investor_name", "fund_capi"
]

LP_KEY_MAP = {
    "LP名称": "lp_name",
    "基金名称": "fund_name",
    "出资日期": "invest_date",
    "出资金额(元)": "invest_amount",
    "LP类型": "lp_type",
    "LP类型(主要)": "lp_type_main",
    "基金所属机构": "investor_name",
    "基金规模(元)": "fund_capi",
}

# ============ 数据规范化 ============
def normalize_row(row, key_map):
    """将中文key的行数据转为英文key"""
    result = {}
    for cn_key, en_key in key_map.items():
        if cn_key in row:
            val = row[cn_key]
            if val is not None and val != "" and val != "None":
                result[en_key] = val
    return result

def normalize_rows(rows, key_map):
    """批量规范化行数据"""
    return [normalize_row(row, key_map) for row in rows]

# ============ 数据获取 ============
def _date_range(start, end):
    return [f"{start.strftime('%Y-%m-%d')} 00:00:00", f"{end.strftime('%Y-%m-%d')} 23:59:59"]

def get_invest_events(client, start_date, end_date, columns=None, limit=200):
    cols = columns or INVEST_QUERY_COLUMNS
    data = client.get_data(req_params=[{
        "table": INVEST_TABLE,
        "selected_columns": cols,
        "filters": [{"field": "invest_date", "type": "range", "value": _date_range(start_date, end_date)}]
    }], limit=limit)
    if not data or not isinstance(data, dict):
        return [], 0
    rows = normalize_rows(data.get("rows", []), KEY_MAP)
    return rows, data.get("count", 0)

def get_yesterday_events(client):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    return get_invest_events(client, yesterday, yesterday)

def get_recent_events(client, days=7):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=days)
    return get_invest_events(client, start_date, yesterday)

def get_previous_day_events(client):
    day_before = datetime.date.today() - datetime.timedelta(days=2)
    return get_invest_events(client, day_before, day_before, columns=BASIC_QUERY_COLUMNS)

def get_7day_trend(client):
    """获取7天趋势数据"""
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=6)
    return get_invest_events(client, start_date, yesterday, columns=[
        "invest_date", "company_gs_name", "fund_com_entity_gs_name"
    ], limit=500)

def get_new_funds(client, days=7):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=days)
    data = client.get_data(req_params=[{
        "table": FUND_TABLE,
        "selected_columns": FUND_QUERY_COLUMNS,
        "filters": [{"field": "fund_establish_date", "type": "range", "value": _date_range(start_date, yesterday)}]
    }], limit=50)
    if not data or not isinstance(data, dict):
        return [], 0
    rows = normalize_rows(data.get("rows", []), FUND_KEY_MAP)
    return rows, data.get("count", 0)

def get_lp_events(client, days=7):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=days)
    data = client.get_data(req_params=[{
        "table": LP_TABLE,
        "selected_columns": LP_QUERY_COLUMNS,
        "filters": [{"field": "invest_date", "type": "range", "value": _date_range(start_date, yesterday)}]
    }], limit=50)
    if not data or not isinstance(data, dict):
        return [], 0
    rows = normalize_rows(data.get("rows", []), LP_KEY_MAP)
    return rows, data.get("count", 0)

# ============ 辅助函数 ============
def safe_get(event, *keys, default="-"):
    for key in keys:
        if isinstance(event, dict) and key in event:
            val = event[key]
            if val is not None and val != "" and val != "None":
                return val
    return default

def format_amount(amount_str):
    if not amount_str or amount_str in ["-", "None", "", "0", "0.0", 0]:
        return "未披露"
    try:
        val = float(str(amount_str).replace(",", ""))
        if val >= 100000000:  # 元转亿
            return f"{val/100000000:.1f}亿"
        elif val >= 10000:  # 元转万
            return f"{val/10000:.0f}万"
        elif val >= 1:
            return f"{val:.0f}元"
        else:
            return str(amount_str)
    except (ValueError, TypeError):
        return str(amount_str)

def trend_text(current, previous):
    if previous == 0 and current > 0:
        return "↑新增"
    elif previous == 0:
        return "—"
    pct = (current - previous) / previous * 100
    if pct > 5:
        return f"↑+{pct:.0f}%"
    elif pct < -5:
        return f"↓{pct:.0f}%"
    else:
        return "→持平"

# ============ 7天趋势分析 ============
def analyze_7day_trend(rows):
    daily = {}
    for event in rows:
        date_str = safe_get(event, "invest_date", default="")
        if date_str and len(str(date_str)) >= 10:
            day = str(date_str)[:10]
            if day not in daily:
                daily[day] = {"events": 0, "companies": set(), "investors": set()}
            daily[day]["events"] += 1
            comp = safe_get(event, "company_gs_name", "project_name", default="")
            if comp and comp != "-":
                daily[day]["companies"].add(comp)
            inv = safe_get(event, "fund_com_entity_gs_name", default="")
            if inv and inv != "-":
                daily[day]["investors"].add(inv)
    result = []
    for day in sorted(daily.keys()):
        d = daily[day]
        result.append({
            "date": day, "events": d["events"],
            "companies": len(d["companies"]),
            "investors": len(d["investors"]),
        })
    return result

# ============ 市场观察 ============
def generate_market_observation(count, company_count, investor_count, industry_sorted, round_sorted, region_sorted, trend_data):
    obs = []
    if len(trend_data) >= 2:
        y = trend_data[-1]
        b = trend_data[-2]
        if y["events"] > b["events"] * 1.2:
            obs.append(f"市场活跃度明显提升，投资事件数较前日增长{int((y['events']/b['events']-1)*100)}%。")
        elif y["events"] < b["events"] * 0.8:
            obs.append(f"市场活跃度有所回落，投资事件数较前日下降{int((1-y['events']/b['events'])*100)}%。")
        else:
            obs.append("市场整体保持平稳运行。")
    if len(industry_sorted) >= 3:
        top3 = "、".join([k for k, v in industry_sorted[:3]])
        obs.append(f"行业热度集中在{top3}领域。")
    if len(region_sorted) >= 2:
        top_city = region_sorted[0][0] if region_sorted[0][0] != "未披露" else (region_sorted[1][0] if len(region_sorted) > 1 else "")
        if top_city:
            obs.append(f"{top_city}地区融资事件最为集中。")
    return " ".join(obs) if obs else "市场整体运行平稳。"

# ============ 公众号报告生成 ============
def generate_wx_report(rows, count, prev_count, prev_investor_count, prev_company_count,
                       trend_data, fund_rows, lp_rows, date_str=None):
    if date_str is None:
        date_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y年%m月%d日")

    if not rows:
        return f"""<section style="padding:20px;text-align:center;">
<p style="font-size:20px;font-weight:bold;color:#1a1a2e;">📊 投融资日报</p>
<p style="font-size:14px;color:#666;">{date_str}</p>
<p style="font-size:14px;color:#999;padding:30px 0;">昨日暂无融资事件数据</p>
</section>"""

    # ---- 统计 ----
    investor_dist, type_dist, industry_dist, region_dist = {}, {}, {}, {}
    company_investors = {}

    for event in rows:
        inv = safe_get(event, "fund_com_entity_gs_name", default="未披露")
        investor_dist[inv] = investor_dist.get(inv, 0) + 1
        inv_type = safe_get(event, "fund_type_desc", default="未披露")
        type_dist[inv_type] = type_dist.get(inv_type, 0) + 1
        industry = safe_get(event, "industry", "industry2", default="未披露")
        industry_dist[industry] = industry_dist.get(industry, 0) + 1
        city = safe_get(event, "city", "province", default="未披露")
        if city == "-":
            city = safe_get(event, "province", default="未披露")
        region_dist[city] = region_dist.get(city, 0) + 1

        comp = safe_get(event, "project_name", "company_gs_name", default="-")
        if comp not in company_investors:
            company_investors[comp] = {
                "investors": [],
                "industry": industry,
                "city": city,
                "desc": safe_get(event, "company_desc", default="-"),
            }
        company_investors[comp]["investors"].append({
            "investor": inv, "type": inv_type,
            "share": event.get("share_percent"), "date": event.get("invest_date", "")
        })

    investor_sorted = sorted(investor_dist.items(), key=lambda x: x[1], reverse=True)[:15]
    type_sorted = sorted(type_dist.items(), key=lambda x: x[1], reverse=True)
    industry_sorted = sorted(industry_dist.items(), key=lambda x: x[1], reverse=True)[:10]
    region_sorted = sorted(region_dist.items(), key=lambda x: x[1], reverse=True)[:10]

    t_events = trend_text(count, prev_count)
    t_companies = trend_text(len(company_investors), prev_company_count)
    t_investors = trend_text(len(investor_dist), prev_investor_count)

    # 7天趋势
    trend_analysis = analyze_7day_trend(trend_data) if trend_data else []

    # 市场观察
    market_obs = generate_market_observation(
        count, len(company_investors), len(investor_dist),
        industry_sorted, type_sorted, region_sorted, trend_analysis
    )

    # ========= 公众号 HTML =========
    html = f"""<section style="max-width:677px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;color:#333;line-height:1.8;font-size:15px;">

<section style="background:#1a1a2e;color:#fff;text-align:center;padding:30px 15px;border-radius:8px 8px 0 0;">
<p style="font-size:22px;font-weight:bold;margin:0;letter-spacing:2px;">📊 投融资日报</p>
<p style="font-size:13px;margin:8px 0 0;color:rgba(255,255,255,0.7);">{date_str}</p>
</section>

<section style="background:#fff;border:1px solid #eee;border-top:none;">
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
<tr>
<td style="text-align:center;padding:18px 8px;border-right:1px solid #f0f0f0;width:33.3%;">
<p style="font-size:30px;font-weight:bold;color:#1a1a2e;margin:0;">{count}</p>
<p style="font-size:12px;color:#999;margin:4px 0 0;">投资事件</p>
<p style="font-size:11px;color:#27ae60;margin:2px 0 0;">{t_events}</p>
</td>
<td style="text-align:center;padding:18px 8px;border-right:1px solid #f0f0f0;width:33.3%;">
<p style="font-size:30px;font-weight:bold;color:#e74c3c;margin:0;">{len(company_investors)}</p>
<p style="font-size:12px;color:#999;margin:4px 0 0;">获投企业</p>
<p style="font-size:11px;color:#27ae60;margin:2px 0 0;">{t_companies}</p>
</td>
<td style="text-align:center;padding:18px 8px;width:33.3%;">
<p style="font-size:30px;font-weight:bold;color:#533483;margin:0;">{len(investor_dist)}</p>
<p style="font-size:12px;color:#999;margin:4px 0 0;">投资方</p>
<p style="font-size:11px;color:#27ae60;margin:2px 0 0;">{t_investors}</p>
</td>
</tr>
</table>
</section>"""

    # --- 市场观察 ---
    html += f"""<section style="padding:16px 15px;background:#f8f9ff;border:1px solid #e8ecff;border-top:none;margin-top:2px;">
<p style="font-size:15px;font-weight:bold;color:#1a1a2e;margin:0 0 8px;">📝 市场观察</p>
<p style="font-size:14px;color:#444;margin:0;line-height:1.9;">{market_obs}</p>
</section>"""

    # --- 7天趋势 ---
    if len(trend_analysis) >= 3:
        html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #667eea;padding-left:10px;margin:0 0 12px;">📈 近7日融资趋势</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px;">
<tr style="background:#f8f9fa;">
<td style="padding:6px 8px;font-weight:bold;color:#666;">日期</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">事件</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">企业</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">投资方</td>
</tr>"""
        for i, day_data in enumerate(trend_analysis):
            bg = "#fafafa" if i % 2 == 0 else "#fff"
            date_short = day_data["date"][5:]
            html += f"""<tr style="background:{bg};">
<td style="padding:6px 8px;">{date_short}</td>
<td style="padding:6px 8px;text-align:center;font-weight:bold;color:#1a1a2e;">{day_data["events"]}</td>
<td style="padding:6px 8px;text-align:center;color:#e74c3c;">{day_data["companies"]}</td>
<td style="padding:6px 8px;text-align:center;color:#533483;">{day_data["investors"]}</td>
</tr>"""
        html += "</table></section>"

    # --- 活跃投资机构 TOP15 ---
    html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #667eea;padding-left:10px;margin:0 0 12px;">🏛 活跃投资机构 TOP15</p>"""
    mx_inv = investor_sorted[0][1] if investor_sorted else 1
    for name, cnt in investor_sorted:
        short = name[:12] + "..." if len(name) > 12 else name
        pct = max(int(cnt / mx_inv * 100), 8)
        html += f"""<section style="margin-bottom:8px;">
<table cellpadding="0" cellspacing="0" style="width:100%;"><tr>
<td style="font-size:13px;color:#333;width:70%;">{short}</td>
<td style="font-size:13px;color:#888;text-align:right;width:30%;">{cnt}起</td>
</tr></table>
<table cellpadding="0" cellspacing="0" style="width:100%;margin-top:3px;"><tr>
<td style="background:#f0f0f0;border-radius:4px;height:18px;width:100%;">
<table cellpadding="0" cellspacing="0" style="width:{pct}%;height:18px;border-radius:4px;background:#667eea;"><tr><td></td></tr></table>
</td></tr></table>
</section>"""
    html += "</section>"

    # --- 行业分布 TOP10 ---
    html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #00b894;padding-left:10px;margin:0 0 12px;">🏭 行业分布 TOP10</p>"""
    mx_ind = industry_sorted[0][1] if industry_sorted else 1
    for name, cnt in industry_sorted:
        short = name[:12] + "..." if len(name) > 12 else name
        pct = max(int(cnt / mx_ind * 100), 8)
        html += f"""<section style="margin-bottom:8px;">
<table cellpadding="0" cellspacing="0" style="width:100%;"><tr>
<td style="font-size:13px;color:#333;width:70%;">{short}</td>
<td style="font-size:13px;color:#888;text-align:right;width:30%;">{cnt}起</td>
</tr></table>
<table cellpadding="0" cellspacing="0" style="width:100%;margin-top:3px;"><tr>
<td style="background:#f0f0f0;border-radius:4px;height:18px;width:100%;">
<table cellpadding="0" cellspacing="0" style="width:{pct}%;height:18px;border-radius:4px;background:#00b894;"><tr><td></td></tr></table>
</td></tr></table>
</section>"""
    html += "</section>"

    # --- 投资类型分布 ---
    html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #f5576c;padding-left:10px;margin:0 0 12px;">💰 投资类型分布</p>"""
    mx_type = type_sorted[0][1] if type_sorted else 1
    for name, cnt in type_sorted[:10]:
        short = name[:12] + "..." if len(name) > 12 else name
        pct = max(int(cnt / mx_type * 100), 8)
        html += f"""<section style="margin-bottom:8px;">
<table cellpadding="0" cellspacing="0" style="width:100%;"><tr>
<td style="font-size:13px;color:#333;width:70%;">{short}</td>
<td style="font-size:13px;color:#888;text-align:right;width:30%;">{cnt}起</td>
</tr></table>
<table cellpadding="0" cellspacing="0" style="width:100%;margin-top:3px;"><tr>
<td style="background:#f0f0f0;border-radius:4px;height:18px;width:100%;">
<table cellpadding="0" cellspacing="0" style="width:{pct}%;height:18px;border-radius:4px;background:#f5576c;"><tr><td></td></tr></table>
</td></tr></table>
</section>"""
    html += "</section>"

    # --- 地区分布 TOP10 ---
    html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #fd79a8;padding-left:10px;margin:0 0 12px;">📍 地区分布 TOP10</p>"""
    mx_reg = region_sorted[0][1] if region_sorted else 1
    for name, cnt in region_sorted:
        short = name[:12] + "..." if len(name) > 12 else name
        pct = max(int(cnt / mx_reg * 100), 8)
        html += f"""<section style="margin-bottom:8px;">
<table cellpadding="0" cellspacing="0" style="width:100%;"><tr>
<td style="font-size:13px;color:#333;width:70%;">{short}</td>
<td style="font-size:13px;color:#888;text-align:right;width:30%;">{cnt}起</td>
</tr></table>
<table cellpadding="0" cellspacing="0" style="width:100%;margin-top:3px;"><tr>
<td style="background:#f0f0f0;border-radius:4px;height:18px;width:100%;">
<table cellpadding="0" cellspacing="0" style="width:{pct}%;height:18px;border-radius:4px;background:#fd79a8;"><tr><td></td></tr></table>
</td></tr></table>
</section>"""
    html += "</section>"

    # --- 新设基金动态 ---
    if fund_rows:
        html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #2ecc71;padding-left:10px;margin:0 0 12px;">🏦 近期新设基金</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px;">
<tr style="background:#f8f9fa;">
<td style="padding:6px 8px;font-weight:bold;color:#666;">基金名称</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;">所属机构</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">管理规模</td>
</tr>"""
        for i, fund in enumerate(fund_rows[:10]):
            bg = "#fafafa" if i % 2 == 0 else "#fff"
            fname = safe_get(fund, "fund_name", default="-")[:16]
            inv_name = safe_get(fund, "investor_name", default="-")[:12]
            target = format_amount(safe_get(fund, "target_size", default="-"))
            html += f"""<tr style="background:{bg};">
<td style="padding:6px 8px;">{fname}</td>
<td style="padding:6px 8px;color:#666;">{inv_name}</td>
<td style="padding:6px 8px;text-align:center;color:#2ecc71;font-weight:bold;">{target}</td>
</tr>"""
        html += "</table></section>"

    # --- LP出资动态 ---
    if lp_rows:
        html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #9b59b6;padding-left:10px;margin:0 0 12px;">💎 LP出资动态</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px;">
<tr style="background:#f8f9fa;">
<td style="padding:6px 8px;font-weight:bold;color:#666;">LP</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;">出资基金</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">金额</td>
</tr>"""
        for i, lp in enumerate(lp_rows[:8]):
            bg = "#fafafa" if i % 2 == 0 else "#fff"
            lp_name = safe_get(lp, "lp_name", default="-")[:14]
            fund = safe_get(lp, "fund_name", default="-")[:14]
            amt = format_amount(safe_get(lp, "invest_amount", default="-"))
            html += f"""<tr style="background:{bg};">
<td style="padding:6px 8px;">{lp_name}</td>
<td style="padding:6px 8px;color:#666;">{fund}</td>
<td style="padding:6px 8px;text-align:center;color:#9b59b6;font-weight:bold;">{amt}</td>
</tr>"""
        html += "</table></section>"

    # --- 获投企业列表 ---
    html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #4ECDC4;padding-left:10px;margin:0 0 12px;">🏢 获投企业列表</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px;">
<tr style="background:#1a1a2e;color:#fff;">
<td style="padding:8px;text-align:center;font-size:12px;width:30px;">#</td>
<td style="padding:8px;font-size:12px;">企业</td>
<td style="padding:8px;text-align:center;font-size:12px;width:50px;">投资方</td>
</tr>"""
    for i, (cn, info) in enumerate(sorted(company_investors.items(), key=lambda x: len(x[1]["investors"]), reverse=True), 1):
        ivs = info["investors"]
        bg = "#fafafa" if i % 2 == 0 else "#fff"
        inv_names = "、".join([v["investor"][:8] for v in ivs[:3]])
        if len(ivs) > 3:
            inv_names += f"等{len(ivs)}家"
        html += f"""<tr style="background:{bg};">
<td style="padding:8px;text-align:center;color:#999;font-size:12px;">{i}</td>
<td style="padding:8px;font-size:13px;"><strong>{cn}</strong><br/><span style="font-size:11px;color:#999;">{info["industry"]} · {inv_names}</span></td>
<td style="padding:8px;text-align:center;font-size:12px;">{len(ivs)}家</td>
</tr>"""
    html += "</table></section>"

    # --- 底部 ---
    html += """<section style="padding:15px;text-align:center;color:#b2bec3;font-size:11px;border-top:1px solid #eee;margin-top:12px;">
<p style="margin:0;">数据来源：烯牛创投数据 | 报告由 AI 自动生成</p>
<p style="margin:4px 0 0;">仅供参考，不构成投资建议</p>
</section>
</section>"""

    return html


def generate_full_page(wx_content, date_str):
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>投融资日报 | {date_str} - 公众号版</title>
<style>
body {{ margin:0; padding:20px; background:#f0f2f5; font-family:sans-serif; }}
.toolbar {{ position:sticky; top:0; z-index:100; background:#fff; padding:15px 20px; box-shadow:0 2px 8px rgba(0,0,0,0.1); display:flex; align-items:center; justify-content:space-between; }}
.toolbar h1 {{ margin:0; font-size:18px; color:#1a1a2e; }}
.btn {{ padding:10px 24px; border:none; border-radius:6px; font-size:15px; cursor:pointer; font-weight:bold; }}
.btn-copy {{ background:#07c160; color:#fff; }}
.btn-copy:hover {{ background:#06ad56; }}
.preview {{ max-width:677px; margin:20px auto; background:#fff; border-radius:8px; box-shadow:0 2px 12px rgba(0,0,0,0.08); overflow:hidden; }}
.hint {{ max-width:677px; margin:0 auto 10px; font-size:13px; color:#999; text-align:center; }}
.toast {{ position:fixed; top:80px; left:50%; transform:translateX(-50%); background:rgba(0,0,0,0.75); color:#fff; padding:10px 24px; border-radius:6px; font-size:14px; display:none; z-index:999; }}
</style>
</head>
<body>
<div class="toolbar">
    <h1>📊 {date_str} 投融资日报</h1>
    <button class="btn btn-copy" onclick="copyForWechat()">📋 一键复制到公众号</button>
</div>
<p class="hint">↓ 下方是公众号预览效果，点击上方按钮复制后直接粘贴到公众号编辑器</p>
<div class="preview" id="wx-content">{wx_content}</div>
<div id="toast" class="toast"></div>
<script>
function copyForWechat() {{
    const el = document.getElementById('wx-content');
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    try {{
        const ok = document.execCommand('copy');
        showToast(ok ? '✅ 已复制！直接粘贴到公众号编辑器即可' : '⚠️ 复制失败，请手动选中内容复制');
    }} catch(e) {{ showToast('⚠️ 复制失败，请手动选中内容复制'); }}
    sel.removeAllRanges();
}}
function showToast(msg) {{
    const t = document.getElementById('toast');
    t.innerText = msg; t.style.display = 'block';
    setTimeout(() => {{ t.style.display = 'none'; }}, 2500);
}}
</script>
</body></html>"""


# ============ 邮件发送 ============
def send_email(html_content, date_str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 投融资日报 | {date_str}"
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(f"投融资日报 {date_str}", "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    att = MIMEBase("text", "html")
    att.set_payload(html_content.encode("utf-8"))
    encoders.encode_base64(att)
    att.add_header("Content-Disposition", "attachment", filename=f"daily_report_{yesterday.strftime('%Y%m%d')}.html")
    msg.attach(att)
    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) if SMTP_PORT == 465 else smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        if SMTP_PORT != 465:
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, EMAIL_TO.split(","), msg.as_string())
        server.quit()
        print(f"[OK] 邮件已发送至 {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[ERROR] 邮件发送失败: {e}", file=sys.stderr)
        return False

# ============ 主流程 ============
def main():
    print(f"投融资日报生成 - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    client = XiniuMCPClient(XINIU_MCP_URL)
    init_result = client.initialize()
    print(f"  MCP: {'OK' if init_result else 'FAIL'}")

    # 1. 昨日投融资事件
    rows, count = get_yesterday_events(client)
    print(f"  昨日事件: {count}条")

    # 2. 前日数据
    prev_rows, prev_count = get_previous_day_events(client)
    prev_investor_count = len(set(safe_get(e, "fund_com_entity_gs_name", default="x") for e in prev_rows)) if prev_rows else 0
    prev_company_count = len(set(safe_get(e, "project_name", "company_gs_name", default="x") for e in prev_rows)) if prev_rows else 0
    print(f"  前日事件: {prev_count}条")

    # 3. 7天趋势
    trend_rows, _ = get_7day_trend(client)
    print(f"  7天数据: {len(trend_rows)}条")

    # 昨日无数据则扩大范围
    if count == 0:
        print("  昨日无数据，获取最近7天...")
        rows, count = get_recent_events(client, days=7)
        print(f"  7天回退: {count}条")

    # 4. 新设基金
    fund_rows, fund_count = get_new_funds(client, days=7)
    print(f"  新设基金: {fund_count}条")

    # 5. LP出资
    lp_rows, lp_count = get_lp_events(client, days=7)
    print(f"  LP出资: {lp_count}条")

    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y年%m月%d日")

    # 生成报告
    wx_content = generate_wx_report(
        rows, count, prev_count, prev_investor_count, prev_company_count,
        trend_rows, fund_rows, lp_rows, date_str
    )
    full_html = generate_full_page(wx_content, date_str)

    # 保存文件
    output_dir = os.environ.get("GITHUB_OUTPUT_DIR", "")
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, f"report_{yesterday.strftime('%Y%m%d')}.html"), "w", encoding="utf-8") as f:
            f.write(full_html)
        print(f"  报告已保存至 {output_dir}")

    success = send_email(wx_content, date_str)
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
