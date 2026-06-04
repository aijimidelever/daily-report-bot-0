#!/usr/bin/env python3
"""
每日投融资日报 - 公众号兼容版 V4
升级：多表数据 + 7天趋势 + 交叉分析 + 重点企业画像 + 市场观察 + LP出资 + 基金动态
数据来源：烯牛创投数据 MCP API
"""
import json
import os
import sys
import smtplib
import datetime
import math
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
            "clientInfo": {"name": "daily-report-bot", "version": "4.0.0"}
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

    def get_company_info(self, firm_name, aspect="basic"):
        """获取公司画像信息"""
        result = self.call_tool("get_company_info", {"firm_name": firm_name, "aspect": aspect})
        if result and isinstance(result, dict):
            content = result.get("content", [])
            if content:
                return content[0].get("text", "")
        return None

    def get_entity_profile(self, entity_type, firm_name):
        """获取投资机构/基金画像"""
        result = self.call_tool("get_entity_profile", {"entity_type": entity_type, "firm_name": firm_name})
        if result and isinstance(result, dict):
            content = result.get("content", [])
            if content:
                return content[0].get("text", "")
        return None

# ============ 数据表配置 ============
INVEST_TABLE = "entity_invest_event.e_investor_entity_invest_firm"
FUND_TABLE = "entity_investor.e_fund"
LP_TABLE = "ai_chat.v_lp_invest_fund"
COMPANY_TABLE = "tsb_v2.company"
FUNDING_TABLE = "tsb_v2.funding"

INVEST_COLUMNS = [
    "company_gs_name", "project_name", "invest_date",
    "fund_com_entity_gs_name", "share_percent", "fund_type_desc",
    "industry", "sub_industry", "inv_round", "invest_amount",
    "currency", "province", "city", "company_desc", "inv_round_desc",
    "invest_amount_cny"
]

FUND_COLUMNS = [
    "fund_name", "fund_com_entity_gs_name", "setup_date",
    "fund_type_desc", "target_size", "actual_size", "currency",
    "province", "city", "management_form", "fund_status"
]

LP_COLUMNS = [
    "lp_name", "fund_name", "invest_date", "invest_amount",
    "currency", "lp_type_desc", "province", "city"
]

BASIC_COLUMNS = [
    "company_gs_name", "project_name", "invest_date",
    "fund_com_entity_gs_name", "share_percent", "fund_type_desc"
]

# ============ 数据获取函数 ============
def _date_range(start, end):
    return [f"{start.strftime('%Y-%m-%d')} 00:00:00", f"{end.strftime('%Y-%m-%d')} 23:59:59"]

def get_events_in_range(client, start_date, end_date, columns=None, limit=200):
    cols = columns or BASIC_COLUMNS
    result = client.get_data(req_params=[{
        "table": INVEST_TABLE,
        "selected_columns": cols,
        "filters": [{"field": "invest_date", "type": "range", "value": _date_range(start_date, end_date)}]
    }], limit=limit)
    # 如果扩展字段查询失败，回退到基础字段
    if not result or (isinstance(result, dict) and result.get("count", 0) == 0 and cols != BASIC_COLUMNS):
        print(f"  扩展字段查询为空，回退到基础字段...")
        result = client.get_data(req_params=[{
            "table": INVEST_TABLE,
            "selected_columns": BASIC_COLUMNS,
            "filters": [{"field": "invest_date", "type": "range", "value": _date_range(start_date, end_date)}]
        }], limit=limit)
    return result

def get_yesterday_events(client):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    return get_events_in_range(client, yesterday, yesterday)

def get_recent_events(client, days=3):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=days)
    return get_events_in_range(client, start_date, yesterday)

def get_previous_day_events(client):
    """获取前天数据用于趋势对比"""
    day_before = datetime.date.today() - datetime.timedelta(days=2)
    return get_events_in_range(client, day_before, day_before, columns=BASIC_COLUMNS)

def get_7day_events(client):
    """获取7天数据用于趋势分析"""
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=6)
    return get_events_in_range(client, start_date, yesterday, columns=[
        "invest_date", "company_gs_name", "fund_com_entity_gs_name",
        "invest_amount_cny", "invest_amount", "industry", "inv_round_desc"
    ], limit=500)

def get_new_funds(client, days=7):
    """获取新设基金"""
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=days)
    return client.get_data(req_params=[{
        "table": FUND_TABLE,
        "selected_columns": FUND_COLUMNS,
        "filters": [{"field": "setup_date", "type": "range", "value": _date_range(start_date, yesterday)}]
    }], limit=50)

def get_lp_events(client, days=7):
    """获取LP出资事件"""
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=days)
    return client.get_data(req_params=[{
        "table": LP_TABLE,
        "selected_columns": LP_COLUMNS,
        "filters": [{"field": "invest_date", "type": "range", "value": _date_range(start_date, yesterday)}]
    }], limit=50)

def parse_rows(events_data):
    if not events_data:
        return [], 0
    if isinstance(events_data, dict):
        return events_data.get("rows", []), events_data.get("count", 0)
    if isinstance(events_data, list):
        return events_data, len(events_data)
    return [], 0

# ============ 辅助函数 ============
def safe_get(event, *keys, default="-"):
    for key in keys:
        if isinstance(event, dict) and key in event:
            val = event[key]
            if val is not None and val != "" and val != "None":
                return val
    return default

def format_amount(amount_str):
    if not amount_str or amount_str in ["-", "None", "", "0", "0.0"]:
        return "未披露"
    try:
        val = float(str(amount_str).replace(",", "").replace("万", ""))
        if val >= 10000:
            return f"{val/10000:.1f}亿"
        elif val >= 1:
            return f"{val:.0f}万"
        else:
            return amount_str
    except (ValueError, TypeError):
        return str(amount_str)

def parse_amount_num(amount_str):
    """将金额字符串解析为数字（万元）"""
    if not amount_str or amount_str in ["-", "None", "", "0", "0.0"]:
        return 0
    try:
        return float(str(amount_str).replace(",", "").replace("万", ""))
    except (ValueError, TypeError):
        return 0

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
def analyze_7day_trend(rows_7day):
    """分析7天趋势，返回每日统计"""
    daily = {}
    for event in rows_7day:
        date_str = safe_get(event, "invest_date", default="")
        if date_str and len(date_str) >= 10:
            day = date_str[:10]
            if day not in daily:
                daily[day] = {"events": 0, "companies": set(), "amounts": [], "investors": set()}
            daily[day]["events"] += 1
            comp = safe_get(event, "company_gs_name", default="")
            if comp and comp != "-":
                daily[day]["companies"].add(comp)
            inv = safe_get(event, "fund_com_entity_gs_name", default="")
            if inv and inv != "-":
                daily[day]["investors"].add(inv)
            amt = parse_amount_num(safe_get(event, "invest_amount_cny", "invest_amount", default="0"))
            if amt > 0:
                daily[day]["amounts"].append(amt)

    # 排序
    sorted_days = sorted(daily.keys())
    result = []
    for day in sorted_days:
        d = daily[day]
        total_amt = sum(d["amounts"])
        result.append({
            "date": day,
            "events": d["events"],
            "companies": len(d["companies"]),
            "investors": len(d["investors"]),
            "total_amount": total_amt,
            "avg_amount": total_amt / len(d["amounts"]) if d["amounts"] else 0
        })
    return result

# ============ 市场观察生成 ============
def generate_market_observation(count, company_count, investor_count, industry_sorted, round_sorted, region_sorted, trend_data, major_deals):
    """基于数据自动生成市场观察文字"""
    observations = []

    # 1. 总体热度
    if len(trend_data) >= 2:
        yesterday = trend_data[-1]
        day_before = trend_data[-2]
        if yesterday["events"] > day_before["events"] * 1.2:
            observations.append(f"市场活跃度明显提升，投资事件数较前日增长{int((yesterday['events']/day_before['events']-1)*100)}%。")
        elif yesterday["events"] < day_before["events"] * 0.8:
            observations.append(f"市场活跃度有所回落，投资事件数较前日下降{int((1-yesterday['events']/day_before['events'])*100)}%。")
        else:
            observations.append("市场整体保持平稳运行，投资活跃度与前日基本持平。")

    # 2. 融资规模
    if major_deals:
        total_major = len(major_deals)
        observations.append(f"千万级以上大额融资{total_major}起，大额资金持续流向优质项目。")

    # 3. 行业热点
    if len(industry_sorted) >= 3:
        top3 = "、".join([k for k, v in industry_sorted[:3]])
        observations.append(f"行业热度集中在{top3}领域，资金向头部赛道集中趋势明显。")

    # 4. 地域分布
    if len(region_sorted) >= 2:
        top_city = region_sorted[0][0] if region_sorted[0][0] != "未披露" else (region_sorted[1][0] if len(region_sorted) > 1 else "")
        if top_city:
            observations.append(f"地域方面，{top_city}地区融资事件最为集中，区域集聚效应显著。")

    # 5. 轮次特征
    if round_sorted:
        early_rounds = sum(v for k, v in round_sorted if any(w in k for w in ["天使", "种子", "Pre-A", "A轮"]))
        late_rounds = sum(v for k, v in round_sorted if any(w in k for w in ["C轮", "D轮", "E轮", "Pre-IPO", "战略"]))
        if early_rounds > late_rounds * 2:
            observations.append("融资轮次以早期为主，市场对初创项目保持较高热情。")
        elif late_rounds > early_rounds:
            observations.append("中后期融资占比较高，成熟项目更受资本青睐。")

    return " ".join(observations) if observations else "市场整体运行平稳，投融资活动保持正常节奏。"

# ============ 公众号报告生成 ============
def generate_wx_report(events_data, prev_events_data=None, trend_7day=None,
                       funds_data=None, lp_data=None, date_str=None):
    if date_str is None:
        date_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y年%m月%d日")

    rows, count = parse_rows(events_data)
    prev_rows, prev_count = parse_rows(prev_events_data)
    trend_rows_7day, _ = parse_rows(trend_7day)
    fund_rows, fund_count = parse_rows(funds_data)
    lp_rows, lp_count = parse_rows(lp_data)

    if not rows:
        return f"""<section style="padding:20px;text-align:center;">
<p style="font-size:20px;font-weight:bold;color:#1a1a2e;">📊 投融资日报</p>
<p style="font-size:14px;color:#666;">{date_str}</p>
<p style="font-size:14px;color:#999;padding:30px 0;">昨日暂无融资事件数据</p>
</section>"""

    # ---- 统计 ----
    investor_dist, type_dist, industry_dist, round_dist, region_dist = {}, {}, {}, {}, {}
    company_investors = {}
    major_deals = []
    total_amount = 0
    amount_count = 0
    industry_round_cross = {}  # 行业×轮次交叉
    amount_buckets = {"1亿以上": 0, "5000万-1亿": 0, "1000万-5000万": 0, "1000万以下": 0, "未披露": 0}

    for event in rows:
        inv = safe_get(event, "fund_com_entity_gs_name", default="未披露")
        investor_dist[inv] = investor_dist.get(inv, 0) + 1

        inv_type = safe_get(event, "fund_type_desc", default="未披露")
        type_dist[inv_type] = type_dist.get(inv_type, 0) + 1

        industry = safe_get(event, "sub_industry", "industry", default="未披露")
        if industry == "-":
            industry = safe_get(event, "industry", default="未披露")
        industry_dist[industry] = industry_dist.get(industry, 0) + 1

        rnd = safe_get(event, "inv_round_desc", "inv_round", default="未披露")
        round_dist[rnd] = round_dist.get(rnd, 0) + 1

        # 行业×轮次交叉
        ind_short = industry[:6] if industry != "未披露" else "其他"
        rnd_short = rnd[:6] if rnd != "未披露" else "其他"
        key = (ind_short, rnd_short)
        industry_round_cross[key] = industry_round_cross.get(key, 0) + 1

        city = safe_get(event, "city", "province", default="未披露")
        if city == "-":
            city = safe_get(event, "province", default="未披露")
        region_dist[city] = region_dist.get(city, 0) + 1

        # 金额统计
        amt_str = safe_get(event, "invest_amount_cny", "invest_amount", default="-")
        amt_num = parse_amount_num(amt_str)
        if amt_num > 0:
            total_amount += amt_num
            amount_count += 1
            if amt_num >= 10000:
                amount_buckets["1亿以上"] += 1
            elif amt_num >= 5000:
                amount_buckets["5000万-1亿"] += 1
            elif amt_num >= 1000:
                amount_buckets["1000万-5000万"] += 1
            else:
                amount_buckets["1000万以下"] += 1
        else:
            amount_buckets["未披露"] += 1

        comp = safe_get(event, "project_name", "company_gs_name", default="-")
        if comp not in company_investors:
            company_investors[comp] = {
                "investors": [],
                "industry": safe_get(event, "sub_industry", "industry", default="-"),
                "round": safe_get(event, "inv_round_desc", "inv_round", default="-"),
                "amount": safe_get(event, "invest_amount_cny", "invest_amount", default="-"),
                "city": safe_get(event, "city", default="-"),
                "desc": safe_get(event, "company_desc", default="-"),
            }
        company_investors[comp]["investors"].append({
            "investor": inv, "type": inv_type,
            "share": event.get("share_percent"), "date": event.get("invest_date", "")
        })

        if amt_num >= 1000:
            major_deals.append({"company": comp, "amount": amt_str, "round": rnd,
                               "industry": industry, "investors": inv, "city": city})

    # 排序
    investor_sorted = sorted(investor_dist.items(), key=lambda x: x[1], reverse=True)[:15]
    type_sorted = sorted(type_dist.items(), key=lambda x: x[1], reverse=True)
    industry_sorted = sorted(industry_dist.items(), key=lambda x: x[1], reverse=True)[:10]
    round_sorted = sorted(round_dist.items(), key=lambda x: x[1], reverse=True)
    region_sorted = sorted(region_dist.items(), key=lambda x: x[1], reverse=True)[:10]

    def deal_sort_key(d):
        return parse_amount_num(d["amount"])
    major_deals_sorted = sorted(major_deals, key=deal_sort_key, reverse=True)[:8]

    # 趋势
    prev_investor_count = len(set(safe_get(e, "fund_com_entity_gs_name", default="x") for e in prev_rows)) if prev_rows else 0
    prev_company_count = len(set(safe_get(e, "project_name", "company_gs_name", default="x") for e in prev_rows)) if prev_rows else 0

    t_events = trend_text(count, prev_count)
    t_companies = trend_text(len(company_investors), prev_company_count)
    t_investors = trend_text(len(investor_dist), prev_investor_count)

    # 7天趋势
    trend_analysis = analyze_7day_trend(trend_rows_7day)

    # 市场观察
    market_obs = generate_market_observation(
        count, len(company_investors), len(investor_dist),
        industry_sorted, round_sorted, region_sorted, trend_analysis, major_deals
    )

    # 平均金额
    avg_amount = total_amount / amount_count if amount_count > 0 else 0

    # ========= 公众号兼容 HTML =========

    # --- 标题区 ---
    html = f"""<section style="max-width:677px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;color:#333;line-height:1.8;font-size:15px;">

<section style="background:#1a1a2e;color:#fff;text-align:center;padding:30px 15px;border-radius:8px 8px 0 0;">
<p style="font-size:22px;font-weight:bold;margin:0;letter-spacing:2px;">📊 投融资日报</p>
<p style="font-size:13px;margin:8px 0 0;color:rgba(255,255,255,0.7);">{date_str}</p>
</section>

<!-- 核心指标 -->
<section style="background:#fff;border:1px solid #eee;border-top:none;">
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
<tr>
<td style="text-align:center;padding:18px 8px;border-right:1px solid #f0f0f0;width:25%;">
<p style="font-size:28px;font-weight:bold;color:#1a1a2e;margin:0;">{count}</p>
<p style="font-size:11px;color:#999;margin:4px 0 0;">投资事件</p>
<p style="font-size:11px;color:#27ae60;margin:2px 0 0;">{t_events}</p>
</td>
<td style="text-align:center;padding:18px 8px;border-right:1px solid #f0f0f0;width:25%;">
<p style="font-size:28px;font-weight:bold;color:#e74c3c;margin:0;">{len(company_investors)}</p>
<p style="font-size:11px;color:#999;margin:4px 0 0;">获投企业</p>
<p style="font-size:11px;color:#27ae60;margin:2px 0 0;">{t_companies}</p>
</td>
<td style="text-align:center;padding:18px 8px;border-right:1px solid #f0f0f0;width:25%;">
<p style="font-size:28px;font-weight:bold;color:#533483;margin:0;">{len(investor_dist)}</p>
<p style="font-size:11px;color:#999;margin:4px 0 0;">投资方</p>
<p style="font-size:11px;color:#27ae60;margin:2px 0 0;">{t_investors}</p>
</td>
<td style="text-align:center;padding:18px 8px;width:25%;">
<p style="font-size:28px;font-weight:bold;color:#f39c12;margin:0;">{format_amount(str(total_amount)) if total_amount > 0 else "-"}</p>
<p style="font-size:11px;color:#999;margin:4px 0 0;">披露总金额</p>
<p style="font-size:11px;color:#666;margin:2px 0 0;">均值{format_amount(str(round(avg_amount))) if avg_amount > 0 else "-"}</p>
</td>
</tr>
</table>
</section>"""

    # --- 📝 市场观察 ---
    html += f"""<section style="padding:16px 15px;background:linear-gradient(135deg,#f8f9ff,#f0f4ff);border:1px solid #e8ecff;border-top:none;margin-top:2px;">
<p style="font-size:15px;font-weight:bold;color:#1a1a2e;margin:0 0 8px;">📝 市场观察</p>
<p style="font-size:14px;color:#444;margin:0;line-height:1.9;">{market_obs}</p>
</section>"""

    # --- 📈 7天趋势 ---
    if len(trend_analysis) >= 3:
        html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #667eea;padding-left:10px;margin:0 0 12px;">📈 近7日融资趋势</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px;">
<tr style="background:#f8f9fa;">
<td style="padding:6px 8px;font-weight:bold;color:#666;">日期</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">事件</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">企业</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">投资方</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">披露金额</td>
</tr>"""
        for i, day_data in enumerate(trend_analysis):
            bg = "#fafafa" if i % 2 == 0 else "#fff"
            date_short = day_data["date"][5:]  # MM-DD
            amt_display = format_amount(str(day_data["total_amount"])) if day_data["total_amount"] > 0 else "-"
            html += f"""<tr style="background:{bg};">
<td style="padding:6px 8px;">{date_short}</td>
<td style="padding:6px 8px;text-align:center;font-weight:bold;color:#1a1a2e;">{day_data["events"]}</td>
<td style="padding:6px 8px;text-align:center;color:#e74c3c;">{day_data["companies"]}</td>
<td style="padding:6px 8px;text-align:center;color:#533483;">{day_data["investors"]}</td>
<td style="padding:6px 8px;text-align:center;color:#f39c12;">{amt_display}</td>
</tr>"""
        html += "</table></section>"

    # --- 🔥 大额融资亮点 ---
    if major_deals_sorted:
        html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#e74c3c;border-left:4px solid #e74c3c;padding-left:10px;margin:0 0 12px;">🔥 大额融资亮点</p>"""
        for d in major_deals_sorted:
            amt_display = format_amount(d["amount"])
            html += f"""<section style="background:#fff5f5;border-left:4px solid #e74c3c;padding:10px 14px;margin-bottom:8px;border-radius:0 6px 6px 0;">
<p style="margin:0;font-size:14px;"><strong>{d["company"]}</strong>
<span style="display:inline-block;background:#e74c3c;color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;margin-left:6px;">{amt_display}</span>
<span style="display:inline-block;background:#dfe6e9;color:#333;border-radius:3px;padding:1px 6px;font-size:11px;margin-left:4px;">{d["round"]}</span></p>
<p style="margin:4px 0 0;font-size:12px;color:#636e72;">{d["industry"]} · {d["city"]} · {d["investors"]}</p>
</section>"""
        html += "</section>"

    # --- 🏛 活跃投资机构 TOP15 ---
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

    # --- 🏭 行业分布 TOP10 ---
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

    # --- 🔄 融资轮次分布 ---
    html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #f39c12;padding-left:10px;margin:0 0 12px;">🔄 融资轮次分布</p>
<table cellpadding="0" cellspacing="0" style="width:100%;">"""
    round_colors = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#3498db", "#9b59b6", "#1abc9c", "#34495e", "#e91e63", "#ff9800"]
    for i in range(0, min(len(round_sorted), 9), 3):
        html += "<tr>"
        for j in range(3):
            if i + j < len(round_sorted):
                name, cnt = round_sorted[i + j]
                c = round_colors[(i + j) % len(round_colors)]
                short = name[:6] + ".." if len(name) > 6 else name
                html += f'<td style="padding:4px 6px;"><section style="background:{c}18;border:1px solid {c}50;border-radius:6px;padding:5px 10px;text-align:center;font-size:12px;"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{c};margin-right:3px;"></span>{short} <strong>{cnt}</strong></section></td>'
            else:
                html += '<td></td>'
        html += "</tr>"
    html += "</table></section>"

    # --- 💵 融资规模分布 ---
    html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #e67e22;padding-left:10px;margin:0 0 12px;">💵 融资规模分布</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px;">"""
    scale_colors = {"1亿以上": "#e74c3c", "5000万-1亿": "#e67e22", "1000万-5000万": "#f1c40f", "1000万以下": "#3498db", "未披露": "#b2bec3"}
    total_known = sum(v for k, v in amount_buckets.items() if k != "未披露")
    for scale_name, scale_cnt in amount_buckets.items():
        c = scale_colors.get(scale_name, "#999")
        pct = int(scale_cnt / count * 100) if count > 0 else 0
        bar_pct = int(scale_cnt / max(amount_buckets.values()) * 100) if max(amount_buckets.values()) > 0 else 0
        html += f"""<tr>
<td style="padding:6px 0;font-size:13px;color:#333;width:120px;">{scale_name}</td>
<td style="padding:6px 4px;width:60%;">
<table cellpadding="0" cellspacing="0" style="width:100%;"><tr>
<td style="background:#f0f0f0;border-radius:4px;height:16px;width:100%;">
<table cellpadding="0" cellspacing="0" style="width:{bar_pct}%;height:16px;border-radius:4px;background:{c};"><tr><td></td></tr></table>
</td></tr></table>
</td>
<td style="padding:6px 8px;font-size:13px;text-align:right;width:60px;">{scale_cnt}起({pct}%)</td>
</tr>"""
    html += "</table></section>"

    # --- 📍 地区分布 TOP10 ---
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

    # --- 💰 投资类型分布 ---
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

    # --- 🔗 行业×轮次交叉分析 ---
    if industry_round_cross:
        # 取TOP5行业 × 轮次
        top_industries = [k for k, v in industry_sorted[:5]]
        top_rounds = [k for k, v in round_sorted[:5]]
        cross_header = "".join([f'<td style="padding:6px 4px;font-size:11px;font-weight:bold;text-align:center;color:#fff;background:#533483;">{r[:5]}</td>' for r in top_rounds])
        cross_rows = ""
        for ind in top_industries:
            cells = ""
            for rnd in top_rounds:
                val = industry_round_cross.get((ind[:6], rnd[:6]), 0)
                bg = "#e8f5e9" if val > 0 else "#fafafa"
                color = "#2e7d32" if val > 0 else "#ccc"
                cells += f'<td style="padding:6px 4px;font-size:12px;text-align:center;background:{bg};color:{color};">{val if val > 0 else "-"}</td>'
            cross_rows += f'<tr><td style="padding:6px 4px;font-size:11px;font-weight:bold;color:#333;background:#f8f9fa;">{ind[:8]}</td>{cells}</tr>'

        html += f"""<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #533483;padding-left:10px;margin:0 0 12px;">🔗 行业×轮次交叉分析</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;border:1px solid #eee;">
<tr style="background:#533483;color:#fff;"><td style="padding:6px 4px;font-size:11px;font-weight:bold;color:#fff;"></td>{cross_header}</tr>
{cross_rows}
</table>
</section>"""

    # --- 🏦 新设基金动态 ---
    if fund_rows:
        html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #2ecc71;padding-left:10px;margin:0 0 12px;">🏦 近期新设基金</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px;">
<tr style="background:#f8f9fa;">
<td style="padding:6px 8px;font-weight:bold;color:#666;">基金名称</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;">管理人</td>
<td style="padding:6px 8px;font-weight:bold;color:#666;text-align:center;">目标规模</td>
</tr>"""
        for i, fund in enumerate(fund_rows[:10]):
            bg = "#fafafa" if i % 2 == 0 else "#fff"
            fname = safe_get(fund, "fund_name", default="-")[:16]
            gp = safe_get(fund, "fund_com_entity_gs_name", default="-")[:12]
            target = format_amount(safe_get(fund, "target_size", default="-"))
            html += f"""<tr style="background:{bg};">
<td style="padding:6px 8px;">{fname}</td>
<td style="padding:6px 8px;color:#666;">{gp}</td>
<td style="padding:6px 8px;text-align:center;color:#2ecc71;font-weight:bold;">{target}</td>
</tr>"""
        html += "</table></section>"

    # --- 💎 LP出资动态 ---
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

    # --- 🏢 获投企业列表 ---
    html += """<section style="padding:20px 15px;background:#fff;border:1px solid #eee;border-top:none;margin-top:12px;">
<p style="font-size:16px;font-weight:bold;color:#1a1a2e;border-left:4px solid #4ECDC4;padding-left:10px;margin:0 0 12px;">🏢 获投企业列表</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px;">
<tr style="background:#1a1a2e;color:#fff;">
<td style="padding:8px;text-align:center;font-size:12px;width:30px;">#</td>
<td style="padding:8px;font-size:12px;">企业</td>
<td style="padding:8px;text-align:center;font-size:12px;width:60px;">金额</td>
<td style="padding:8px;text-align:center;font-size:12px;width:50px;">投资方</td>
</tr>"""
    for i, (cn, info) in enumerate(sorted(company_investors.items(), key=lambda x: len(x[1]["investors"]), reverse=True), 1):
        amt_display = format_amount(info["amount"])
        ivs = info["investors"]
        bg = "#fafafa" if i % 2 == 0 else "#fff"
        html += f"""<tr style="background:{bg};">
<td style="padding:8px;text-align:center;color:#999;font-size:12px;">{i}</td>
<td style="padding:8px;font-size:13px;"><strong>{cn}</strong><br/><span style="font-size:11px;color:#999;">{info["industry"]} · {info["round"]}</span></td>
<td style="padding:8px;text-align:center;color:#e74c3c;font-weight:bold;font-size:13px;">{amt_display}</td>
<td style="padding:8px;text-align:center;font-size:12px;">{len(ivs)}家</td>
</tr>"""
    html += "</table></section>"

    # --- 底部 ---
    html += f"""<section style="padding:15px;text-align:center;color:#b2bec3;font-size:11px;border-top:1px solid #eee;margin-top:12px;">
<p style="margin:0;">数据来源：烯牛创投数据 | 报告由 AI 自动生成</p>
<p style="margin:4px 0 0;">仅供参考，不构成投资建议</p>
</section>

</section>"""

    return html


def generate_full_page(wx_content, date_str):
    """生成完整HTML页面，包含一键复制按钮"""
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
    <div>
        <button class="btn btn-copy" onclick="copyForWechat()">📋 一键复制到公众号</button>
    </div>
</div>

<p class="hint">↓ 下方是公众号预览效果，点击上方按钮复制后直接粘贴到公众号编辑器</p>

<div class="preview" id="wx-content">
{wx_content}
</div>

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
        if (ok) {{
            showToast('✅ 已复制！直接粘贴到公众号编辑器即可');
        }} else {{
            showToast('⚠️ 复制失败，请手动选中内容复制');
        }}
    }} catch(e) {{
        showToast('⚠️ 复制失败，请手动选中内容复制');
    }}
    sel.removeAllRanges();
}}

function showToast(msg) {{
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.style.display = 'block';
    setTimeout(() => {{ t.style.display = 'none'; }}, 2500);
}}
</script>

</body>
</html>"""


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
    events_data = get_yesterday_events(client)
    ev_rows, ev_count = parse_rows(events_data)
    print(f"  昨日事件: {ev_count}条")

    # 2. 前日数据（趋势对比）
    prev_events_data = get_previous_day_events(client)
    prev_rows, prev_count = parse_rows(prev_events_data)
    print(f"  前日事件: {prev_count}条")

    # 3. 7天趋势数据
    trend_7day = get_7day_events(client)
    t_rows, t_count = parse_rows(trend_7day)
    print(f"  7天数据: {t_count}条")

    # 昨日无数据则扩大范围
    if not events_data or (isinstance(events_data, dict) and events_data.get("count", 0) == 0):
        print("  昨日无数据，获取最近3天...")
        events_data = get_recent_events(client, days=3)

    # 4. 新设基金
    funds_data = get_new_funds(client, days=7)
    f_rows, f_count = parse_rows(funds_data)
    print(f"  新设基金: {f_count}条")

    # 5. LP出资
    lp_data = get_lp_events(client, days=7)
    l_rows, l_count = parse_rows(lp_data)
    print(f"  LP出资: {l_count}条")

    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y年%m月%d日")

    # 生成报告
    wx_content = generate_wx_report(
        events_data, prev_events_data, trend_7day,
        funds_data, lp_data, date_str
    )
    full_html = generate_full_page(wx_content, date_str)

    # 保存文件
    output_dir = os.environ.get("GITHUB_OUTPUT_DIR", "")
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, f"wx_report_{yesterday.strftime('%Y%m%d')}.html"), "w", encoding="utf-8") as f:
            f.write(wx_content)
        with open(os.path.join(output_dir, f"report_{yesterday.strftime('%Y%m%d')}.html"), "w", encoding="utf-8") as f:
            f.write(full_html)
        print(f"  报告已保存至 {output_dir}")

    success = send_email(wx_content, date_str)
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
