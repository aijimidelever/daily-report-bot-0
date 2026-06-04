#!/usr/bin/env python3
"""
每日投融资日报自动生成 + 邮件推送
数据来源：烯牛创投数据 MCP API
优化版本 - 更多维分析、更丰富内容
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
            "clientInfo": {"name": "daily-report-bot", "version": "2.0.0"}
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

# ============ 数据获取 ============
INVEST_TABLE = "entity_invest_event.e_investor_entity_invest_firm"

EXPANDED_COLUMNS = [
    "company_gs_name", "project_name", "invest_date",
    "fund_com_entity_gs_name", "share_percent", "fund_type_desc",
    "industry", "sub_industry", "inv_round", "invest_amount",
    "currency", "province", "city", "company_desc", "inv_round_desc",
    "invest_amount_cny"
]

BASIC_COLUMNS = [
    "company_gs_name", "project_name", "invest_date",
    "fund_com_entity_gs_name", "share_percent", "fund_type_desc"
]

def _date_range(start, end):
    return [f"{start.strftime('%Y-%m-%d')} 00:00:00", f"{end.strftime('%Y-%m-%d')} 23:59:59"]

def get_events_in_range(client, start_date, end_date, columns=None, limit=200):
    cols = columns or EXPANDED_COLUMNS
    return client.get_data(req_params=[{
        "table": INVEST_TABLE,
        "selected_columns": cols,
        "filters": [{"field": "invest_date", "type": "range", "value": _date_range(start_date, end_date)}]
    }], limit=limit)

def get_yesterday_events(client):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    return get_events_in_range(client, yesterday, yesterday)

def get_recent_events(client, days=3):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=days)
    return get_events_in_range(client, start_date, yesterday)

def get_previous_day_events(client):
    day_before = datetime.date.today() - datetime.timedelta(days=2)
    return get_events_in_range(client, day_before, day_before, columns=BASIC_COLUMNS)

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

def trend_icon(current, previous):
    if previous == 0 and current > 0:
        return '<span style="color:#27ae60;">▲ 新增</span>'
    elif previous == 0:
        return '<span style="color:#999;">—</span>'
    pct = (current - previous) / previous * 100
    if pct > 5:
        return f'<span style="color:#27ae60;">▲ +{pct:.0f}%</span>'
    elif pct < -5:
        return f'<span style="color:#e74c3c;">▼ {pct:.0f}%</span>'
    else:
        return f'<span style="color:#f39c12;">● 持平</span>'

# ============ 报告生成 ============
def generate_report(events_data, prev_events_data=None, date_str=None):
    if date_str is None:
        date_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y年%m月%d日")

    rows, count = parse_rows(events_data)
    prev_rows, prev_count = parse_rows(prev_events_data)

    if not rows:
        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:sans-serif;">
<div style="max-width:650px;margin:0 auto;background:#fff;padding:40px 20px;text-align:center;">
<h1 style="color:#0f3460;font-size:22px;">📊 投融资日报</h1>
<p style="color:#666;font-size:14px;">{date_str}</p>
<div style="padding:30px;color:#999;font-size:15px;">昨日暂无融资事件数据</div>
</div></body></html>"""

    investor_dist, type_dist, industry_dist, round_dist, region_dist = {}, {}, {}, {}, {}
    company_investors = {}
    major_deals = []

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

        city = safe_get(event, "city", "province", default="未披露")
        if city == "-":
            city = safe_get(event, "province", default="未披露")
        region_dist[city] = region_dist.get(city, 0) + 1

        comp = safe_get(event, "project_name", "company_gs_name", default="-")
        if comp not in company_investors:
            company_investors[comp] = {
                "investors": [],
                "industry": safe_get(event, "sub_industry", "industry", default="-"),
                "round": safe_get(event, "inv_round_desc", "inv_round", default="-"),
                "amount": safe_get(event, "invest_amount_cny", "invest_amount", default="-"),
                "city": safe_get(event, "city", default="-"),
                "desc": safe_get(event, "company_desc", default="-")
            }
        company_investors[comp]["investors"].append({
            "investor": inv, "type": inv_type,
            "share": event.get("share_percent"), "date": event.get("invest_date", "")
        })

        amt_str = safe_get(event, "invest_amount_cny", "invest_amount", default="-")
        if amt_str not in ["-", "未披露", "", "0", "0.0"]:
            try:
                amt = float(str(amt_str).replace(",", "").replace("万", ""))
                if amt >= 1000:
                    major_deals.append({"company": comp, "amount": amt_str, "round": rnd, "industry": industry, "investors": inv, "city": city})
            except (ValueError, TypeError):
                pass

    investor_sorted = sorted(investor_dist.items(), key=lambda x: x[1], reverse=True)[:15]
    type_sorted = sorted(type_dist.items(), key=lambda x: x[1], reverse=True)
    industry_sorted = sorted(industry_dist.items(), key=lambda x: x[1], reverse=True)[:10]
    round_sorted = sorted(round_dist.items(), key=lambda x: x[1], reverse=True)
    region_sorted = sorted(region_dist.items(), key=lambda x: x[1], reverse=True)[:10]

    def deal_sort_key(d):
        try:
            return float(str(d["amount"]).replace(",", "").replace("万", ""))
        except:
            return 0
    major_deals_sorted = sorted(major_deals, key=deal_sort_key, reverse=True)[:5]

    prev_investor_count = len(set(safe_get(e, "fund_com_entity_gs_name", default="x") for e in prev_rows)) if prev_rows else 0
    prev_company_count = len(set(safe_get(e, "project_name", "company_gs_name", default="x") for e in prev_rows)) if prev_rows else 0

    def bars(data, color1, color2, max_items=10):
        mx = data[0][1] if data else 1
        html = ""
        for name, cnt in data[:max_items]:
            short = name[:14] + "..." if len(name) > 14 else name
            pct = max(int(cnt / mx * 100), 5)
            html += f'''<div style="margin-bottom:10px;">
                <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px;">
                    <span style="color:#333;">{short}</span><span style="color:#888;">{cnt}起</span>
                </div>
                <div style="background:#f0f0f0;border-radius:6px;height:22px;overflow:hidden;">
                    <div style="background:linear-gradient(90deg,{color1},{color2});width:{pct}%;height:100%;border-radius:6px;"></div>
                </div>
            </div>'''
        return html

    def pie_items(data, colors):
        html = '<div style="display:flex;flex-wrap:wrap;gap:8px;">'
        for i, (name, cnt) in enumerate(data[:8]):
            c = colors[i % len(colors)]
            short = name[:8] + ".." if len(name) > 8 else name
            html += f'<div style="background:{c}15;border:1px solid {c}40;border-radius:8px;padding:6px 12px;font-size:12px;">'
            html += f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{c};margin-right:4px;"></span>'
            html += f'{short} <b>{cnt}</b></div>'
        html += '</div>'
        return html

    inv_bars = bars(investor_sorted, "#667eea", "#764ba2", 15)
    industry_bars = bars(industry_sorted, "#00b894", "#00cec9", 10)
    round_colors = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#3498db", "#9b59b6", "#1abc9c", "#34495e", "#e91e63", "#ff9800"]
    round_html = pie_items(round_sorted, round_colors)
    region_bars = bars(region_sorted, "#fd79a8", "#e17055", 10)
    type_bars_html = bars(type_sorted, "#f093fb", "#f5576c", 10)

    major_html = ""
    for d in major_deals_sorted:
        amt_display = format_amount(d["amount"])
        major_html += f'''<div style="background:linear-gradient(135deg,#fff5f5,#fff0f0);border-left:4px solid #e74c3c;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:10px;">
            <div><b style="font-size:14px;color:#2d3436;">{d["company"]}</b>
            <span style="background:#e74c3c;color:#fff;border-radius:4px;padding:2px 8px;font-size:11px;margin-left:8px;">{amt_display}</span>
            <span style="background:#dfe6e9;color:#2d3436;border-radius:4px;padding:2px 8px;font-size:11px;margin-left:4px;">{d["round"]}</span></div>
            <div style="font-size:12px;color:#636e72;margin-top:4px;">{d["industry"]} · {d["city"]} · {d["investors"]}</div>
        </div>'''
    if not major_html:
        major_html = '<div style="text-align:center;color:#b2bec3;padding:20px;font-size:13px;">暂无千万级以上大额融资事件</div>'

    comp_rows = ""
    for i, (cn, info) in enumerate(sorted(company_investors.items(), key=lambda x: len(x[1]["investors"]), reverse=True), 1):
        ivs = info["investors"]
        inv_names = "、".join([v["investor"][:8] for v in ivs[:3]])
        if len(ivs) > 3:
            inv_names += f"等{len(ivs)}家"
        amt_display = format_amount(info["amount"])
        comp_rows += f'''<tr>
            <td style="padding:10px 8px;border-bottom:1px solid #eee;font-size:12px;color:#999;text-align:center;">{i}</td>
            <td style="padding:10px 8px;border-bottom:1px solid #eee;font-size:13px;">
                <b style="color:#2d3436;">{cn}</b>
                <div style="font-size:11px;color:#999;margin-top:2px;">{info["industry"]} · {info["round"]}</div>
            </td>
            <td style="padding:10px 8px;border-bottom:1px solid #eee;font-size:13px;font-weight:bold;color:#e74c3c;text-align:center;">{amt_display}</td>
            <td style="padding:10px 8px;border-bottom:1px solid #eee;font-size:12px;text-align:center;">{len(ivs)}家</td>
            <td style="padding:10px 8px;border-bottom:1px solid #eee;font-size:11px;color:#636e72;">{inv_names}</td>
        </tr>'''

    type_summary = "、".join([f"{k}({v})" for k, v in type_sorted[:5]])
    round_summary = "、".join([f"{k}({v})" for k, v in round_sorted[:5]])

    trend_events = trend_icon(count, prev_count)
    trend_companies = trend_icon(len(company_investors), prev_company_count)
    trend_investors = trend_icon(len(investor_dist), prev_investor_count)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>投融资日报 | {date_str}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;">

<div style="max-width:650px;margin:0 auto;background:#fff;">

<!-- 头部 -->
<div style="background:linear-gradient(135deg,#0f3460,#16213e,#1a1a2e);padding:35px 20px;text-align:center;">
    <h1 style="color:#fff;margin:0;font-size:24px;letter-spacing:3px;">📊 投融资日报</h1>
    <p style="color:rgba(255,255,255,0.7);margin:8px 0 0;font-size:14px;">{date_str}</p>
    <p style="color:rgba(255,255,255,0.5);margin:4px 0 0;font-size:11px;">数据来源：烯牛创投数据</p>
</div>

<!-- 核心指标卡片 -->
<div style="display:flex;padding:0;background:#fff;">
    <div style="flex:1;text-align:center;padding:20px 10px;border-right:1px solid #f0f0f0;">
        <div style="font-size:36px;font-weight:bold;color:#0f3460;">{count}</div>
        <div style="font-size:12px;color:#999;margin-top:4px;">投资事件</div>
        <div style="font-size:11px;margin-top:4px;">{trend_events}</div>
    </div>
    <div style="flex:1;text-align:center;padding:20px 10px;border-right:1px solid #f0f0f0;">
        <div style="font-size:36px;font-weight:bold;color:#e94560;">{len(company_investors)}</div>
        <div style="font-size:12px;color:#999;margin-top:4px;">获投企业</div>
        <div style="font-size:11px;margin-top:4px;">{trend_companies}</div>
    </div>
    <div style="flex:1;text-align:center;padding:20px 10px;">
        <div style="font-size:36px;font-weight:bold;color:#533483;">{len(investor_dist)}</div>
        <div style="font-size:12px;color:#999;margin-top:4px;">投资方</div>
        <div style="font-size:11px;margin-top:4px;">{trend_investors}</div>
    </div>
</div>

<!-- 大额融资亮点 -->
<div style="padding:20px;border-top:8px solid #f0f2f5;">
    <h2 style="font-size:16px;color:#e74c3c;border-left:4px solid #e74c3c;padding-left:10px;margin:0 0 15px;">🔥 大额融资亮点</h2>
    {major_html}
</div>

<!-- 活跃投资机构TOP15 -->
<div style="padding:20px;border-top:8px solid #f0f2f5;">
    <h2 style="font-size:16px;color:#0f3460;border-left:4px solid #667eea;padding-left:10px;margin:0 0 15px;">🏛 活跃投资机构 TOP15</h2>
    {inv_bars}
</div>

<!-- 行业分布 -->
<div style="padding:20px;border-top:8px solid #f0f2f5;">
    <h2 style="font-size:16px;color:#0f3460;border-left:4px solid #00b894;padding-left:10px;margin:0 0 15px;">🏭 行业分布 TOP10</h2>
    {industry_bars}
</div>

<!-- 融资轮次分布 -->
<div style="padding:20px;border-top:8px solid #f0f2f5;">
    <h2 style="font-size:16px;color:#0f3460;border-left:4px solid #f1c40f;padding-left:10px;margin:0 0 15px;">🔄 融资轮次分布</h2>
    {round_html}
    <div style="font-size:12px;color:#999;margin-top:10px;">{round_summary}</div>
</div>

<!-- 地区分布 -->
<div style="padding:20px;border-top:8px solid #f0f2f5;">
    <h2 style="font-size:16px;color:#0f3460;border-left:4px solid #fd79a8;padding-left:10px;margin:0 0 15px;">📍 地区分布 TOP10</h2>
    {region_bars}
</div>

<!-- 投资类型分布 -->
<div style="padding:20px;border-top:8px solid #f0f2f5;">
    <h2 style="font-size:16px;color:#0f3460;border-left:4px solid #f5576c;padding-left:10px;margin:0 0 15px;">💰 投资类型分布</h2>
    {type_bars_html}
</div>

<!-- 获投企业列表 -->
<div style="padding:20px;border-top:8px solid #f0f2f5;">
    <h2 style="font-size:16px;color:#0f3460;border-left:4px solid #4ECDC4;padding-left:10px;margin:0 0 15px;">🏢 获投企业列表（共{len(company_investors)}家）</h2>
    <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="background:#0f3460;color:#fff;">
                <th style="padding:10px 8px;text-align:left;font-size:12px;width:30px;">#</th>
                <th style="padding:10px 8px;text-align:left;font-size:12px;">企业</th>
                <th style="padding:10px 8px;text-align:center;font-size:12px;">金额</th>
                <th style="padding:10px 8px;text-align:center;font-size:12px;">投资方数</th>
                <th style="padding:10px 8px;text-align:left;font-size:12px;">主要投资方</th>
            </tr></thead>
            <tbody>{comp_rows}</tbody>
        </table>
    </div>
</div>

<!-- 底部 -->
<div style="padding:20px;border-top:8px solid #f0f2f5;text-align:center;color:#b2bec3;font-size:11px;">
    <p>投资类型：{type_summary}</p>
    <p style="margin-top:5px;">本报告由 AI 自动生成，仅供参考，不构成投资建议</p>
</div>

</div>
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

    events_data = get_yesterday_events(client)

    prev_events_data = get_previous_day_events(client)
    prev_rows, prev_count = parse_rows(prev_events_data)
    print(f"  前日数据: {prev_count}条")

    if not events_data or (isinstance(events_data, dict) and events_data.get("count", 0) == 0):
        print("  昨日无数据，获取最近3天...")
        events_data = get_recent_events(client, days=3)

    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y年%m月%d日")

    html = generate_report(events_data, prev_events_data, date_str)

    output_dir = os.environ.get("GITHUB_OUTPUT_DIR", "")
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, f"report_{yesterday.strftime('%Y%m%d')}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  报告已保存至 {output_dir}")

    success = send_email(html, date_str)
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
