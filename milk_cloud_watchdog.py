from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.header import Header
import os
from pathlib import Path
import smtplib
import sqlite3

DATABASE_PATH = Path(__file__).resolve().with_name("shop.db")
PRODUCT_NAME = "某品牌鲜牛奶 950ml"


def send_reminder_email(today_str: str):
    """通过 SMTP 发送跨平台提醒邮件"""
    smtp_server = os.environ.get("SMTP_SERVER", "://qq.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    sender_email = os.environ.get("SENDER_EMAIL", "")
    sender_pass = os.environ.get("SENDER_PASS", "")
    receiver_email = os.environ.get("RECEIVER_EMAIL", sender_email)
    dashboard_url = os.environ.get("DASHBOARD_URL", "https://streamlit.io")

    if not sender_email or not sender_pass:
        print("⚠️ 未配置邮箱环境变量（SENDER_EMAIL / SENDER_PASS），跳过发送邮件。")
        return

    subject = f"🥛【采价提醒】今日（{today_str}）{PRODUCT_NAME} 价格尚未记录"
    body = (
        f"您好！\n\n"
        f"系统在例行 3 小时巡检中发现，今日（{today_str}）尚未收到【{PRODUCT_NAME}】的价格或下架状态反馈。\n\n"
        f"👉 请点击下方链接，在手机或电脑浏览器中直接打开看板并展开“📝 在线录入今日牛奶价格”完成填报（若已下架请勾选已下架）：\n"
        f"{dashboard_url}\n\n"
        f"（一旦完成填报或标记已下架，今日将自动停止后续提醒。）"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = Header(f"BI 采价巡检助手 <{sender_email}>", "utf-8")
    msg["To"] = Header(receiver_email, "utf-8")
    msg["Subject"] = Header(subject, "utf-8")

    with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=20) as server:
        server.login(sender_email, sender_pass)
        server.sendmail(sender_email, [receiver_email], msg.as_string())
    print(f"📧 已成功向 {receiver_email} 发送采价提醒邮件！")


def check_today_milk_status():
    # 使用东八区/东京时间或 UTC+8 作为业务日期基准（可根据你所在时区调整 hours=8 或 hours=0）
    biz_now = datetime.now(timezone(timedelta(hours=8)))
    today_str = biz_now.strftime("%Y-%m-%d")
    print(f"开始执行云端采价巡检（业务日期：{today_str}）...")

    if not DATABASE_PATH.exists():
        print("数据库文件不存在，触发提醒。")
        send_reminder_email(today_str)
        return

    with sqlite3.connect(DATABASE_PATH) as conn:
        row = conn.execute(
            "SELECT status, price, feedback FROM milk_prices WHERE record_date = ?",
            (today_str,),
        ).fetchone()

    if row:
        print(f"✅ 今日（{today_str}）已有记录：状态=[{row[0]}], 价格=[{row[1]}], 备注=[{row[2]}]，无需打扰！")
    else:
        print(f"🔔 今日（{today_str}）尚未检测到记录，准备发送邮件提醒...")
        send_reminder_email(today_str)


if __name__ == "__main__":
    check_today_milk_status()