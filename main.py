#!/usr/bin/env python3
"""
每日投融资日报自动生成 + 邮件推送
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
            "clientInfo": {"name": "daily-report-bot", "version": "1.0.0"}
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

def get_yesterday_events(client):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    date_range = [f"{yesterday.strftime('%Y-%m-%d')} 00:00:00", f"{yesterday.strftime('%Y-%m-%d')} 23:59:59"]
    return client.get_data(req_params=[{"table": INVEST_TABLE, "selected_columns": [
        "company_gs_name", "project_name", "invest_date",
        "fund_com_entity_gs_name", "share_percent", "fund_type_desc"
    ], "filters": [{"field": "invest_date", "type": "range", "value": date_range}]}], limit=200)

def get_recent_events(client, days=3):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    start_date = yesterday - datetime.timedelta(days=days)
    date_range = [f"{start_date.strftime('%Y-%m-%d')} 00:00:00", f"{yesterday.strftime('%Y-%m-%d')} 23:59:59"]
    return client.get_data(req_params=[{"table": INVEST_TABLE, "selected_columns": [
        "company_gs_name", "project_name", "invest_date",
        "fund_com_entity_gs_name", "share_percent", "fund_type_desc"
    ], "filters": [{"field": "invest_date", "type": "range", "value": date_range}]}], limit=200)

# ============ 报告生成 ============
def generate_report(events_data, date_str=None):
    if date_str is None:
        date_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y年%m月%d日")
    rows, count = [], 0
    if events_data and isinstance(events_data, dict):
        rows = events_data.get("rows", [])
        count = events_data.get("count", len(rows))
    elif events_data and isinstance(events_data, list):
        rows, count = events_data, len(events_data)
    if not rows:
        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="margin:0;padding:0;background:#f5f5f5;font-family:sans-serif;"><div style="max-width:600px;margin:0 auto;background:#fff;padding:40px 20px;text-align:center;"><h1 style="color:#0f3460;font-size:22px;">投融资日报</h1><p style="color:#666;font-size:14px;">{date_str}</p><div style="padding:30px;color:#999;font-size:15px;">昨日暂无融资事件数据</div></div></body></html>"""

    investor_dist, type_dist, company_investors = {}, {}, {}
    for event in rows:
        inv = event.get("fund_com_entity_gs_name", "未披露")
        investor_dist[inv] = investor_dist.get(inv, 0) + 1
        inv_type = event.get("fund_type_desc", "未披露")
        type_dist[inv_type] = type_dist.get(inv_type, 0) + 1
        comp = event.get("project_name", event.get("company_gs_name", "-"))
        if comp not in company_investors:
            company_investors[comp] = []
        company_investors[comp].append({"investor": inv, "type": inv_type, "share": event.get("share_percent"), "date": event.get("invest_date", "")})

    investor_sorted = sorted(investor_dist.items(), key=lambda x: x[1], reverse=True)[:15]
    type_sorted = sorted(type_dist.items(), key=lambda x: x[1], reverse=True)

    def bars(data, color1, color2):
        mx = data[0][1] if data else 1
        html = ""
        for name, cnt in data:
            short = name[:12] + "..." if len(name) > 12 else name
            pct = int(cnt / mx * 100)
            html += f'<div style="margin-bottom:8px;"><div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:2px;"><span>{short}</span><span>{cnt}起</span></div><div style="background:#f0f0f0;border-radius:4px;height:20px;"><div style="background:linear-gradient(90deg,{color1},{color2});width:{pct}%;height:100%;border-radius:4px;"></div></div></div>'
        return html

    inv_bars = bars(investor_sorted, "#667eea", "#764ba2")
    type_bars_html = bars(type_sorted, "#f093fb", "#f5576c")

    comp_rows = ""
    for i, (cn, ivs) in enumerate(sorted(company_investors.items(), key=lambda x: len(x[1]), reverse=True), 1):
        inv_names = "、".join([v["investor"][:8] for v in ivs[:4]])
        if len(ivs) > 4: inv_names += f"等{len(ivs)}家"
        shares = [v["share"] for v in ivs if v.get("share")]
        share_str = ", ".join([f"{s}%" for s in shares[:3]]) if shares else "-"
        comp_rows += f'<tr><td style="padding:8px;border-bottom:1px solid #eee;font-size:12px;">{i}</td><td style="padding:8px;border-bottom:1px solid #eee;font-size:12px;font-weight:bold;">{cn}</td><td style="padding:8px;border-bottom:1px solid #eee;font-size:12px;">{len(ivs)}家</td><td style="padding:8px;border-bottom:1px solid #eee;font-size:12px;">{share_str}</td><td style="padding:8px;border-bottom:1px solid #eee;font-size:11px;">{inv_names}</td></tr>'

    type_summary = "、".join([f"{k}({v})" for k, v in type_sorted])

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>投融资日报 | {date_str}</title></head><body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;"><div style="max-width:600px;margin:0 auto;background:#fff;"><div style="background:linear-gradient(135deg,#0f3460,#16213e);padding:30px 20px;text-align:center;"><h1 style="color:#fff;margin:0;font-size:22px;letter-spacing:2px;">投融资日报</h1><p style="color:rgba(255,255,255,0.8);margin:8px 0 0;font-size:14px;">{date_str}</p></div><div style="display:flex;padding:15px 10px;background:#f8f9fa;"><div style="flex:1;text-align:center;padding:10px;"><div style="font-size:28px;font-weight:bold;color:#0f3460;">{count}</div><div style="font-size:12px;color:#666;margin-top:4px;">投资事件</div></div><div style="flex:1;text-align:center;padding:10px;border-left:1px solid #e0e0e0;border-right:1px solid #e0e0e0;"><div style="font-size:28px;font-weight:bold;color:#e94560;">{len(company_investors)}</div><div style="font-size:12px;color:#666;margin-top:4px;">获投企业</div></div><div style="flex:1;text-align:center;padding:10px;"><div style="font-size:28px;font-weight:bold;color:#533483;">{len(investor_dist)}</div><div style="font-size:12px;color:#666;margin-top:4px;">投资方</div></div></div><div style="padding:20px;"><h2 style="font-size:16px;color:#0f3460;border-left:4px solid #667eea;padding-left:10px;margin:0 0 15px;">活跃投资机构TOP15</h2>{inv_bars}</div><div style="padding:20px;border-top:8px solid #f5f5f5;"><h2 style="font-size:16px;color:#0f3460;border-left:4px solid #f5576c;padding-left:10px;margin:0 0 15px;">投资类型分布</h2>{type_bars_html}</div><div style="padding:20px;border-top:8px solid #f5f5f5;"><h2 style="font-size:16px;color:#0f3460;border-left:4px solid #4ECDC4;padding-left:10px;margin:0 0 15px;">获投企业列表</h2><div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;"><thead><tr style="background:#0f3460;color:#fff;"><th style="padding:8px;text-align:left;font-size:12px;">#</th><th style="padding:8px;text-align:left;font-size:12px;">企业</th><th style="padding:8px;text-align:left;font-size:12px;">投资方数</th><th style="padding:8px;text-align:left;font-size:12px;">出资比例</th><th style="padding:8px;text-align:left;font-size:12px;">主要投资方</th></tr></thead><tbody>{comp_rows}</tbody></table></div></div><div style="padding:20px;border-top:8px solid #f5f5f5;text-align:center;color:#999;font-size:11px;"><p>数据来源：烯牛创投数据 | 投资类型：{type_summary}</p><p style="margin-top:5px;">本报告由 AI 自动生成，仅供参考，不构成投资建议</p></div></div></body></html>"""

# ============ 邮件发送 ============
def send_email(html_content, date_str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"投融资日报 | {date_str}"
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
        if SMTP_PORT != 465: server.starttls()
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
    if not events_data or (isinstance(events_data, dict) and events_data.get("count", 0) == 0):
        print("  昨日无数据，获取最近3天...")
        events_data = get_recent_events(client, days=3)
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y年%m月%d日")
    html = generate_report(events_data, date_str)
    output_dir = os.environ.get("GITHUB_OUTPUT_DIR", "")
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, f"report_{yesterday.strftime('%Y%m%d')}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    success = send_email(html, date_str)
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
