import base64
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import os
import requests


DATABASE_PATH = Path(__file__).resolve().with_name("shop.db")


@st.cache_data
def load_orders() -> pd.DataFrame:
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"找不到数据库文件：{DATABASE_PATH}")

    query = """
        SELECT
            orders.order_id,
            orders.user_id,
            users.name,
            users.city,
            orders.product_name,
            orders.category,
            orders.amount,
            orders.order_date,
            COALESCE(refunds.refund_amount, 0) AS refund_amount,
            refunds.reason AS refund_reason,
            refunds.order_id IS NOT NULL AS has_refund
        FROM orders
        LEFT JOIN users ON orders.user_id = users.user_id
        LEFT JOIN refunds ON orders.order_id = refunds.order_id
    """
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return pd.read_sql_query(query, connection)


@st.cache_data(ttl=60)
def load_price_monitors() -> tuple[pd.DataFrame, pd.DataFrame]:
  """从 shop.db 读取黄金行情与牛奶巡检记录"""
  with closing(sqlite3.connect(DATABASE_PATH)) as conn:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    gold_df = (
        pd.read_sql_query(
            "SELECT * FROM gold_prices ORDER BY record_date", conn
        )
        if "gold_prices" in tables
        else pd.DataFrame()
    )
    milk_df = (
        pd.read_sql_query(
            "SELECT * FROM milk_prices ORDER BY record_date", conn
        )
        if "milk_prices" in tables
        else pd.DataFrame()
    )
  return gold_df, milk_df


def save_milk_record_and_sync(
    record_date: str, product_name: str, is_delisted: bool, price: float, feedback: str
) -> tuple[bool, str]:
  """将网页提交的牛奶采价或下架记录写入本地 shop.db，并通过 GitHub API 同步持久化到仓库"""
  now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  if is_delisted:
    status_val = "已下架"
    price_val = None
    # 精简冗余前缀，避免表格列宽溢出（日期和状态在独立列中已有清晰展示）
    fb_val = feedback.strip() if feedback.strip() else "货架已清空"
  else:
    status_val = "在售"
    price_val = round(float(price), 2)
    fb_val = feedback.strip() if feedback.strip() else "正常在售"

  # 1. 写入本地 SQLite 数据库（含自动字段迁移，兼容旧版表结构）
  with closing(sqlite3.connect(DATABASE_PATH)) as conn:
    conn.execute("""
          CREATE TABLE IF NOT EXISTS milk_prices (
              record_date TEXT PRIMARY KEY,
              product_name TEXT,
              price REAL,
              status TEXT,
              feedback TEXT,
              updated_at TEXT
          )
      """)
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(milk_prices)").fetchall()
    }
    if "product_name" not in existing_cols:
      conn.execute(
          "ALTER TABLE milk_prices ADD COLUMN product_name TEXT DEFAULT"
          " '某品牌鲜牛奶 950ml'"
      )
    if "updated_at" not in existing_cols:
      conn.execute("ALTER TABLE milk_prices ADD COLUMN updated_at TEXT")

    conn.execute(
        """
          INSERT OR REPLACE INTO milk_prices
          (record_date, product_name, price, status, feedback, updated_at)
          VALUES (?, ?, ?, ?, ?, ?)
          """,
        (record_date, product_name, price_val, status_val, fb_val, now_str),
    )
    conn.commit()

  # 2. 如果配置了 GITHUB_TOKEN（云端环境），自动将最新 shop.db 推送回 GitHub 仓库防止休眠丢失
  gh_token = ""
  gh_repo = "Yscjc2019/ecommerce-dashboard"
  try:
    gh_token = st.secrets.get("GITHUB_TOKEN", "")
    gh_repo = st.secrets.get("GITHUB_REPO", gh_repo)
  except Exception:
    # 本地无 secrets.toml 或云端未配置时静默降级为仅本地保存
    pass

  if not gh_token:
    return True, "已保存到本地数据库（未检测到云端 GITHUB_TOKEN，仅本地生效）。"

  try:
    api_url = f"https://api.github.com/repos/{gh_repo}/contents/shop.db"
    headers = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
    }
    # 获取当前仓库中 shop.db 的最新 sha 值（GitHub API 更新文件必需）
    get_resp = requests.get(api_url, headers=headers, timeout=15)
    get_resp.raise_for_status()
    file_sha = get_resp.json()["sha"]

    # 读取刚更新完的本地 shop.db 并编码为 base64 上传
    db_bytes = DATABASE_PATH.read_bytes()
    encoded_content = base64.b64encode(db_bytes).decode("utf-8")

    put_payload = {
        "message": f"chore: 在线录入 {record_date} 牛奶采价状态 ({status_val})",
        "content": encoded_content,
        "sha": file_sha,
        "branch": "main",
    }
    put_resp = requests.put(api_url, headers=headers, json=put_payload, timeout=20)
    put_resp.raise_for_status()
    return True, "✅ 已成功入库并自动同步备份至 GitHub 云端仓库！"
  except Exception as e:
    return False, f"⚠️ 本地已写入，但同步 GitHub 失败：{e}"


def retrieve_rag_context(
    question: str,
    orders_df: pd.DataFrame,
    rfm_df: pd.DataFrame,
    gold_df: pd.DataFrame,
    milk_df: pd.DataFrame,
) -> dict:
    """增强版 RAG 检索器：从数据库动态提取多维度业务切片，同时返回文本上下文与结构化指标"""
    total_sales = orders_df["amount"].sum()
    total_refund = orders_df["refund_amount"].sum()
    net_sales = total_sales - total_refund
    refund_rate = (total_refund / total_sales * 100) if total_sales else 0

    # 1. 品类与城市切片
    cat_stats = (
        orders_df.groupby("category", as_index=False)
        .agg(销售额=("amount", "sum"), 退款额=("refund_amount", "sum"))
        .assign(净销售额=lambda d: d["销售额"] - d["退款额"])
        .sort_values("净销售额", ascending=False)
    )
    city_stats = (
        orders_df.groupby("city", as_index=False)
        .agg(销售额=("amount", "sum"), 退款额=("refund_amount", "sum"))
        .assign(退款率=lambda d: (d["退款额"] / d["销售额"] * 100).round(1))
        .sort_values("销售额", ascending=False)
    )

    # 2. RFM 客户分层切片
    seg_counts = rfm_df["客户分层"].value_counts().to_dict()
    top_vips = (
        rfm_df[rfm_df["客户分层"] == "重要价值客户（核心金主）"]
        .sort_values("Monetary", ascending=False)
        .head(3)
    )
    vip_names = (
        "、".join([f"{r['name']}({r['city']}, ¥{r['Monetary']:,.0f})" for _, r in top_vips.iterrows()])
        if not top_vips.empty
        else "暂无"
    )

    # 3. 黄金与牛奶实时切片
    gold_text = "暂无黄金行情数据。"
    if not gold_df.empty:
        max_row = gold_df.loc[gold_df["price_cny_g"].idxmax()]
        min_row = gold_df.loc[gold_df["price_cny_g"].idxmin()]
        latest_row = gold_df.iloc[-1]
        gold_text = (
            f"最新({latest_row['record_date']}) ¥{latest_row['price_cny_g']:.2f}/克；"
            f"近30日最高 ¥{max_row['price_cny_g']:.2f}/克({max_row['record_date']})，"
            f"最低 ¥{min_row['price_cny_g']:.2f}/克({min_row['record_date']})。"
        )

    milk_text = "暂无牛奶采价记录。"
    if not milk_df.empty:
        valid_p = milk_df.dropna(subset=["price"])
        avg_p = f"在售均价 ¥{valid_p['price'].mean():.2f}" if not valid_p.empty else "暂无有效报价"
        delisted_days = milk_df[milk_df["status"] == "已下架"]["record_date"].tolist()
        delisted_str = f"曾于 {', '.join(delisted_days)} 发生下架" if delisted_days else "近期无下架记录"
        detail_items = []
        for _, r in milk_df.iterrows():
          price_str = f"¥{r['price']:.2f}" if pd.notna(r["price"]) else "无报价"
          detail_items.append(
              f"{r['record_date']}[{r['status']}]{price_str}({r['feedback']})"
          )
        details = " | ".join(detail_items)
        milk_text = f"{avg_p}；{delisted_str}。完整记录：{details}"

    context_str = (
        f"【1. 经营总览】总销售额 ¥{total_sales:,.2f}，总退款 ¥{total_refund:,.2f}（退款率 {refund_rate:.1f}%），净销售额 ¥{net_sales:,.2f}。\n"
        f"【2. 品类表现】" + "；".join([f"{r['category']}: 净销¥{r['净销售额']:,.0f}" for _, r in cat_stats.iterrows()]) + "。\n"
        f"【3. 城市表现】Top3城市为 " + "；".join([f"{r['city']}(销售¥{r['销售额']:,.0f}, 退款率{r['退款率']}%)" for _, r in city_stats.head(3).iterrows()]) + "。\n"
        f"【4. 客户分层】总客户 {len(rfm_df)} 人，分布：{seg_counts}。Top3核心金主：{vip_names}。\n"
        f"【5. 黄金行情】{gold_text}\n"
        f"【6. 牛奶监控】{milk_text}"
    )

    return {
        "context_str": context_str,
        "gold_text": gold_text,
        "milk_text": milk_text,
        "vip_names": vip_names,
        "seg_counts": seg_counts,
        "cat_stats": cat_stats,
        "city_stats": city_stats,
        "net_sales": net_sales,
        "refund_rate": refund_rate,
    }


def generate_rag_answer(
    question: str,
    chat_history: list[dict],
    rag_bundle: dict,
    api_key: str,
    base_url: str,
    model_name: str,
) -> str:
    """增强版生成引擎：支持多轮对话上下文与智能规则推理"""
    context = rag_bundle["context_str"]
    if api_key.strip():
        try:
            headers = {
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            }
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一位资深商业数据分析师。请严格基于以下【私有数据库实时检索切片】回答用户提问，"
                        "结合具体数字给出简洁专业的结论与行动建议，切勿编造不存在的数据：\n\n" + context
                    ),
                }
            ]
            # 携带最近 4 轮对话记忆，支持连续追问
            for msg in chat_history[-4:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
            messages.append({"role": "user", "content": question})

            resp = requests.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": model_name.strip() or "deepseek-chat",
                    "messages": messages,
                    "temperature": 0.3,
                },
                timeout=25,
            )
            resp.raise_for_status()
            return resp.json()["choices"]["message"]["content"]
        except Exception as e:
            return f"⚠️ 大模型接口调用异常（`{e}`），已为您切换至本地实时检索结果：\n\n{context}"

    # 零 API Key 时的本地多意图智能分析引擎
    q = question.lower()
    if any(k in q for k in ["牛奶", "下架", "采价"]):
        return f"🥛 **牛奶采价与状态诊断**\n\n- **核心结论**：{rag_bundle['milk_text']}\n- **运营建议**：建议重点关注下架后的补货恢复时间，若连续 3 天缺货需及时排查供应链或切换竞品监控。"
    if any(k in q for k in ["黄金", "金价", "贵金属"]):
        return f"🟡 **黄金行情趋势分析**\n\n- **区间表现**：{rag_bundle['gold_text']}\n- **观察提示**：当前数据已接入 GitHub Actions 每日自动更新管道，可结合近 30 日低点评估阶段性配置窗口。"
    if any(k in q for k in ["客户", "金主", "rfm", "流失", "用户"]):
        return (
            f"💎 **RFM 客户价值分层洞察**\n\n"
            f"- **高价值核心金主 Top 3**：{rag_bundle['vip_names']}\n"
            f"- **人群结构分布**：{rag_bundle['seg_counts']}\n"
            f"- **行动建议**：建议优先针对“重要唤回客户”与“重要挽留客户”导出名单发放定向唤醒券。"
        )
    if any(k in q for k in ["城市", "品类", "销售", "退款", "退货", "经营"]):
        top_city = rag_bundle["city_stats"].iloc[0]
        top_cat = rag_bundle["cat_stats"].iloc[0]
        return (
            f"📊 **全链路经营与退货诊断**\n\n"
            f"- **整体健康度**：当前累计净销售额 **¥{rag_bundle['net_sales']:,.2f}**，整体退款率为 **{rag_bundle['refund_rate']:.1f}%**。\n"
            f"- **主力增长引擎**：【{top_cat['category']}】品类净贡献最高（¥{top_cat['净销售额']:,.0f}）；【{top_city['city']}】是销售额最高城市（退款率 {top_city['退款率']}%）。"
        )
    return f"📊 **已为您检索全库最新经营切片：**\n\n{context}\n\n💡 *你可以继续追问具体的**品类表现、城市退货率、核心金主名单、黄金高低点或牛奶下架记录**！*"


def calculate_rfm(orders: pd.DataFrame) -> pd.DataFrame:
    customer_orders = orders.copy()
    customer_orders["order_date"] = pd.to_datetime(customer_orders["order_date"])
    customer_orders["net_amount"] = (
        customer_orders["amount"] - customer_orders["refund_amount"]
    )
    snapshot_date = customer_orders["order_date"].max() + pd.Timedelta(days=1)

    rfm = customer_orders.groupby(
        ["user_id", "name", "city"], as_index=False
    ).agg(
        Recency=("order_date", lambda dates: (snapshot_date - dates.max()).days),
        Frequency=("order_id", "count"),
        Monetary=("net_amount", "sum"),
    )
    rfm["Monetary"] = rfm["Monetary"].round(2)
    rfm["R_Score"] = pd.qcut(
        rfm["Recency"], q=5, labels=[5, 4, 3, 2, 1]
    ).astype(int)
    rfm["F_Score"] = pd.qcut(
        rfm["Frequency"].rank(method="first"), q=5, labels=[1, 2, 3, 4, 5]
    ).astype(int)
    rfm["M_Score"] = pd.qcut(
        rfm["Monetary"], q=5, labels=[1, 2, 3, 4, 5]
    ).astype(int)

    def segment_customer(row: pd.Series) -> str:
        r_high = row["R_Score"] >= 3
        f_high = row["F_Score"] >= 3
        m_high = row["M_Score"] >= 3

        if r_high and f_high and m_high:
            return "重要价值客户（核心金主）"
        if not r_high and f_high and m_high:
            return "重要唤回客户（高消费流失预警）"
        if r_high and not f_high and m_high:
            return "重要深耕客户（新晋大单客）"
        if not r_high and not f_high and m_high:
            return "重要挽留客户（曾买大单已沉睡）"
        if r_high and f_high and not m_high:
            return "潜力客户（高频活跃小额客）"
        if not r_high and f_high and not m_high:
            return "一般维持客户"
        if r_high and not f_high and not m_high:
            return "新客户（近期首单）"
        return "流失客户"

    rfm["客户分层"] = rfm.apply(segment_customer, axis=1)
    return rfm


st.set_page_config(page_title="电商数据分析看板", page_icon="📊", layout="wide")
st.title("电商数据分析看板")

orders = load_orders()
categories = sorted(orders["category"].dropna().unique().tolist())
cities = sorted(orders["city"].dropna().unique().tolist())

with st.sidebar:
    st.header("数据筛选")
    selected_categories = st.multiselect("商品类别", categories, default=categories)
    selected_cities = st.multiselect(
        "城市",
        cities,
        default=[],
        placeholder="留空表示全部城市",
        help="不选择城市时展示全部城市的数据。",
    )

filtered_orders = orders[orders["category"].isin(selected_categories)].copy()
if selected_cities:
    filtered_orders = filtered_orders[filtered_orders["city"].isin(selected_cities)]

rfm_customers = calculate_rfm(orders)
if selected_cities:
    rfm_customers = rfm_customers[rfm_customers["city"].isin(selected_cities)].copy()

gold_prices, milk_prices = load_price_monitors()
overview_tab, rfm_tab, monitor_tab = st.tabs(
    ["📊 销售与退款总览", "💎 客户价值分层 (RFM)", "📈 价格监控与 AI 助手 (RAG)"]
)

with overview_tab:
    total_sales = filtered_orders["amount"].sum()
    order_count = len(filtered_orders)
    average_order_value = total_sales / order_count if order_count else 0
    total_refunds = filtered_orders["refund_amount"].sum()
    net_sales = total_sales - total_refunds

    metric_columns = st.columns(5)
    metric_columns[0].metric("总销售额", f"¥{total_sales:,.2f}")
    metric_columns[1].metric("总订单数", f"{order_count:,}")
    metric_columns[2].metric("平均客单价", f"¥{average_order_value:,.2f}")
    metric_columns[3].metric("总退款金额", f"¥{total_refunds:,.2f}")
    metric_columns[4].metric("净销售额", f"¥{net_sales:,.2f}")

    chart_columns = st.columns(2)
    with chart_columns[0]:
        st.subheader("各商品类别销售额")
        if filtered_orders.empty:
            st.info("当前筛选条件下没有订单。")
        else:
            category_sales = (
                filtered_orders.groupby("category", as_index=False)["amount"]
                .sum()
                .sort_values("amount", ascending=False)
            )
            category_chart = px.bar(
                category_sales,
                x="category",
                y="amount",
                labels={"category": "商品类别", "amount": "销售额"},
                text_auto=".2f",
            )
            category_chart.update_layout(xaxis_title=None, yaxis_title="销售额")
            st.plotly_chart(category_chart, width="stretch")

    with chart_columns[1]:
        st.subheader("按周统计的销售额趋势")
        if filtered_orders.empty:
            st.info("当前筛选条件下没有订单。")
        else:
            weekly_sales = (
                filtered_orders.assign(
                    order_date=pd.to_datetime(filtered_orders["order_date"])
                )
                .assign(
                    week_start=lambda frame: frame["order_date"]
                    .dt.to_period("W-SUN")
                    .dt.start_time
                )
                .groupby("week_start", as_index=False)["amount"]
                .sum()
                .sort_values("week_start")
            )
            weekly_chart = px.line(
                weekly_sales,
                x="week_start",
                y="amount",
                markers=True,
                labels={"week_start": "周起始日期", "amount": "销售额"},
            )
            weekly_chart.update_traces(
                hovertemplate="周起始日期：%{x|%Y-%m-%d}<br>销售额：¥%{y:,.2f}<extra></extra>"
            )
            weekly_chart.update_layout(xaxis_title=None, yaxis_title="销售额")
            st.plotly_chart(weekly_chart, width="stretch")

    st.subheader("退款原因分布")
    refunded_orders = filtered_orders[
        filtered_orders["has_refund"].astype(bool)
    ].copy()
    if refunded_orders.empty:
        st.info("当前筛选条件下没有退款记录。")
    else:
        refunded_orders["refund_reason"] = refunded_orders["refund_reason"].fillna(
            "未填写原因"
        )
        refund_reasons = (
            refunded_orders.groupby("refund_reason", as_index=False)
            .size()
            .rename(columns={"size": "refund_count"})
            .sort_values("refund_count", ascending=False)
        )
        refund_chart = px.pie(
            refund_reasons,
            names="refund_reason",
            values="refund_count",
            labels={"refund_reason": "退款原因", "refund_count": "退款订单数"},
            hole=0.35,
        )
        refund_chart.update_traces(
            hovertemplate="%{label}<br>退款订单数：%{value}<extra></extra>"
        )
        st.plotly_chart(refund_chart, width="stretch")

    st.subheader("消费总额 Top 10 用户排行榜")
    if filtered_orders.empty:
        st.info("当前筛选条件下没有订单。")
    else:
        top_users = (
            filtered_orders.groupby(["user_id", "name"], dropna=False, as_index=False)[
                "amount"
            ]
            .sum()
            .sort_values("amount", ascending=False)
            .head(10)
        )
        top_users["user_name"] = top_users["name"].fillna("未知用户")
        top_users["user_label"] = (
            top_users["user_name"]
            + "（用户 "
            + top_users["user_id"].astype(str)
            + "）"
        )
        user_chart = px.bar(
            top_users.sort_values("amount"),
            x="amount",
            y="user_label",
            orientation="h",
            labels={"user_label": "用户", "amount": "消费总额"},
            text_auto=".2f",
        )
        user_chart.update_layout(xaxis_title="消费总额", yaxis_title=None)
        st.plotly_chart(user_chart, width="stretch")

    st.subheader("筛选后的详细订单数据")
    st.download_button(
        "下载筛选后的 CSV 数据",
        data=filtered_orders.to_csv(index=False).encode("utf-8-sig"),
        file_name="filtered_orders.csv",
        mime="text/csv",
    )
    display_orders = filtered_orders.copy()
    display_orders["refund_reason"] = display_orders["refund_reason"].fillna("-")
    st.dataframe(
        display_orders,
        width="stretch",
        hide_index=True,
        column_config={
            "order_id": st.column_config.NumberColumn("订单编号", width="small"),
            "user_id": st.column_config.NumberColumn("用户编号", width="small"),
            "name": "用户姓名",
            "city": "城市",
            "product_name": "商品名称",
            "category": "商品类别",
            "amount": st.column_config.NumberColumn("订单金额", format="¥%.2f"),
            "order_date": "下单日期",
            "refund_amount": st.column_config.NumberColumn(
                "退款金额", format="¥%.2f"
            ),
            "refund_reason": st.column_config.TextColumn("退款原因", width="large"),
            "has_refund": st.column_config.NumberColumn("是否退款", width="small"),
        },
    )

with rfm_tab:
  st.subheader("客户价值分层分布")
  if rfm_customers.empty:
    st.info("当前筛选条件下没有客户数据。")
  else:
    available_segments = sorted(rfm_customers["客户分层"].unique().tolist())
    selected_segments = st.multiselect(
        "客户分层",
        available_segments,
        default=available_segments,
        placeholder="选择需要查看的客户分层",
    )

    filtered_rfm = rfm_customers[
        rfm_customers["客户分层"].isin(selected_segments)
    ].copy()

    if filtered_rfm.empty:
      st.info("当前筛选条件下没有符合的客户。")
    else:
      segment_summary = (
          filtered_rfm.groupby("客户分层", as_index=False)
          .agg(
              客户人数=("user_id", "count"),
              平均净消费=("Monetary", "mean"),
          )
          .sort_values("客户人数", ascending=True)  # 升序排列，让人数最多的排在最上方
      )
      total_rfm_users = segment_summary["客户人数"].sum()
      segment_summary["人数占比"] = (
          segment_summary["客户人数"] / total_rfm_users * 100
      )
      segment_summary["平均净消费"] = segment_summary["平均净消费"].round(2)

      # 组合一行简洁清晰的外部标签，适配电脑与手机屏幕
      segment_summary["展示标签"] = segment_summary.apply(
          lambda r: (
              f"{int(r['客户人数'])}人 ({r['人数占比']:.1f}%) | 均消"
              f" ¥{r['平均净消费']:,.0f}"
          ),
          axis=1,
      )

      bar_chart = px.bar(
          segment_summary,
          x="客户人数",
          y="客户分层",
          orientation="h",
          color="平均净消费",
          # 从浅蓝到深蓝的渐变，彻底告别“白底隐身”
          color_continuous_scale=["#93C5FD", "#3B82F6", "#1D4ED8", "#1E3A8A"],
          text="展示标签",
          custom_data=["人数占比", "平均净消费"],
          labels={
              "客户人数": "客户人数（人）",
              "客户分层": "",
              "平均净消费": "平均净消费（元）",
          },
      )

      max_count = segment_summary["客户人数"].max()
      # 只调用一次 update_traces，保留柱子的细边框与富文本标签
      bar_chart.update_traces(
          textposition="outside",
          cliponaxis=False,
          textfont=dict(size=13),
          marker_line=dict(
              color="#64748B", width=0.8
          ),  # 给柱子加上极细边框，边界更锐利
          hovertemplate=(
              "<b>%{y}</b><br>客户人数：%{x} 人"
              " (%{customdata:.1f}%)<br>平均净消费："
              " ¥%{customdata:,.2f}<extra></extra>"
          ),
      )
      bar_chart.update_layout(
          height=max(380, len(segment_summary) * 52),  # 根据类别数量动态调整高度，手机端不拥挤
          xaxis=dict(range=[0, max_count * 1.48], title="客户人数（人）"),
          yaxis=dict(tickfont=dict(size=13)),
          margin=dict(t=20, l=10, r=20, b=30),
          coloraxis_colorbar=dict(
              orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
          ),  # 色条放顶部水平展示，省出手机端左右宽度
      )
      st.plotly_chart(bar_chart, width="stretch")

      st.subheader("分层客户运营名单")
      export_rfm = filtered_rfm[[
          "user_id",
          "name",
          "city",
          "Recency",
          "Frequency",
          "Monetary",
          "客户分层",
      ]].sort_values(by="Monetary", ascending=False)

      st.download_button(
          "导出运营名单 CSV",
          data=export_rfm.to_csv(index=False).encode("utf-8-sig"),
          file_name="rfm_customers.csv",
          mime="text/csv",
      )
      st.dataframe(
          export_rfm,
          width="stretch",
          hide_index=True,
          column_config={
              "user_id": st.column_config.NumberColumn("用户编号", width="small"),
              "name": st.column_config.TextColumn("用户姓名", width="small"),
              "city": st.column_config.TextColumn("所在城市", width="small"),
              "Recency": st.column_config.NumberColumn(
                  "最近消费间隔(天)", width="small"
              ),
              "Frequency": st.column_config.NumberColumn(
                  "下单次数", width="small"
              ),
              "Monetary": st.column_config.NumberColumn(
                  "净消费总额", format="¥%.2f", width="small"
              ),
              "客户分层": st.column_config.TextColumn("客户分层", width="large"),
          },
      )

with monitor_tab:
  # 展示上一次提交成功后 rerun 保留的 flash 消息（展示一次即清空）
  flash_msg = st.session_state.pop("milk_flash_msg", None)
  if flash_msg:
    st.success(flash_msg)

  st.subheader("双轨商品价格与状态监控中心")
  p_col1, p_col2 = st.columns(2)

  with p_col1:
    st.markdown("#### 🟡 路线一：黄金行情近 30 天走势（全自动采集）")
    if gold_prices.empty:
      st.info("暂无黄金行情数据，请先运行 `gold_tracker.py`。")
    else:
      latest_gold = gold_prices.iloc[-1]["price_cny_g"]
      first_gold = gold_prices.iloc[0]["price_cny_g"]
      diff_gold = latest_gold - first_gold
      st.metric(
          "最新参考金价（元/克）",
          f"¥{latest_gold:,.2f}",
          f"{diff_gold:+.2f} 元（近30日）",
      )
      g_chart = px.line(
          gold_prices,
          x="record_date",
          y="price_cny_g",
          markers=True,
          labels={"record_date": "日期", "price_cny_g": "价格（元/克）"},
      )
      g_chart.update_layout(
          height=350,
          xaxis_title=None,
          yaxis_title="元/克",
          margin=dict(t=10, l=10, r=10, b=10),
      )
      st.plotly_chart(g_chart, width="stretch")

  with p_col2:
    st.markdown("#### 🥛 路线二：牛奶人工采价与下架状态闭环")

    with st.expander("📝 在线录入今日牛奶价格 / 下架反馈", expanded=False):
      with st.form("milk_entry_form", clear_on_submit=False):
        f_date = st.date_input("记录日期", value=datetime.now().date())
        f_product = st.text_input("商品名称", value="某品牌鲜牛奶 950ml")
        f_delisted = st.checkbox("🚨 该产品已下架（勾选后无需填写价格）")
        f_price = st.number_input(
            "当前售价（元）", min_value=0.0, value=19.9, step=0.1
        )
        f_feedback = st.text_input(
            "状态或下架原因备注",
            placeholder="例如：正常在售 / 周末促销 / 货架已清空",
        )
        submitted = st.form_submit_button("提交并同步至云端数据库")

        if submitted:
          ok, msg = save_milk_record_and_sync(
              f_date.strftime("%Y-%m-%d"),
              f_product,
              f_delisted,
              f_price,
              f_feedback,
          )
          if ok:
            st.session_state["milk_flash_msg"] = msg  # flash：rerun 后在 monitor_tab 顶部展示
            st.cache_data.clear()  # 清除缓存使页面图表立刻刷新出最新数据
            st.rerun()
          else:
            st.warning(msg)

    if milk_prices.empty:
      st.info("暂无牛奶采价记录，请先运行 `milk_watchdog.py`。")
    else:
      latest_milk = milk_prices.iloc[-1]
      status_str = latest_milk["status"]
      price_display = (
          f"¥{latest_milk['price']:.2f}"
          if pd.notna(latest_milk["price"])
          else "已下架（无报价）"
      )
      st.metric(
          f"最新状态（{latest_milk['record_date']}）",
          price_display,
          status_str,
          delta_color="normal" if status_str == "在售" else "inverse",
      )
      # 仅对有价格的日期绘制折线，并在表格中完整展示包括“已下架”在内的所有反馈闭环
      valid_milk = milk_prices.dropna(subset=["price"])
      if not valid_milk.empty:
        m_chart = px.line(
            valid_milk,
            x="record_date",
            y="price",
            markers=True,
            labels={"record_date": "日期", "price": "价格（元）"},
        )
        m_chart.update_layout(
            height=240,
            xaxis_title=None,
            yaxis_title="元",
            margin=dict(t=10, l=10, r=10, b=10),
        )
        st.plotly_chart(m_chart, width="stretch")

      milk_display = milk_prices[[
          "record_date",
          "status",
          "price",
          "feedback",
      ]].copy()
      # 自动精简历史遗留的冗余前缀，让旧数据也清爽显示
      milk_display["feedback"] = milk_display["feedback"].str.replace(
          r"^\d{4}年\d{1,2}月\d{1,2}日该产品已下架（(?:原)?备注：(.*)）$",
          r"\1",
          regex=True,
      )
      st.dataframe(
          milk_display,
          width="stretch",
          hide_index=True,
          column_config={
              "record_date": st.column_config.TextColumn(
                  "记录日期", width="small"
              ),
              "status": st.column_config.TextColumn("状态", width="small"),
              "price": st.column_config.NumberColumn(
                  "价格", format="¥%.2f", width="small"
              ),
              "feedback": st.column_config.TextColumn(
                  "状态/下架反馈记录", width="medium"
              ),
          },
      )

  st.divider()
  st.subheader("🤖 私有数据 RAG 智能分析助手")
  st.caption(
      "支持多轮连续追问与一键业务诊断（填 API Key 可调用大模型深度推理，不填也可使用内置多意图检索引擎）。"
  )

  with st.expander("⚙️ 大模型 API 配置（可选）", expanded=False):
    c1, c2, c3 = st.columns(3)
    rag_api_key = c1.text_input("API Key", type="password", placeholder="sk-...")
    rag_base_url = c2.text_input("Base URL", value="https://deepseek.com")
    rag_model = c3.text_input("模型名称", value="deepseek-chat")

  # 初始化对话历史
  if "rag_messages" not in st.session_state:
    st.session_state.rag_messages = [{
        "role": "assistant",
        "content": (
            "你好！我是你的专属商业数据分析助手。你可以点击下方快捷按钮，或直接输入问题向数据库提问："
        ),
    }]

  # 快捷提问按钮栏（点一下直接提问，不用手动打字）
  q_cols = st.columns(4)
  quick_prompts = [
      ("📊 整体经营与退货诊断", "请帮我诊断当前的整体销售、净销售额和退货表现"),
      ("💎 核心金主与客户分布", "目前有多少核心金主？客户分层分布如何？"),
      ("🟡 黄金近30日高低点", "近30天黄金价格最高和最低分别是多少？"),
      ("🥛 牛奶采价与下架复盘", "汇总一下牛奶的采价均价和下架记录"),
  ]
  triggered_prompt = None
  for idx, (btn_label, prompt_text) in enumerate(quick_prompts):
    if q_cols[idx].button(btn_label, use_container_width=True):
      triggered_prompt = prompt_text

  # 渲染历史对话气泡
  for msg in st.session_state.rag_messages:
    with st.chat_message(msg["role"]):
      st.markdown(msg["content"])

  # 接收快捷按钮或输入框提问
  chat_input_text = st.chat_input(
      "输入问题，例如：哪个城市销售额最高？牛奶最近下架过吗？"
  )
  final_question = triggered_prompt or chat_input_text

  if final_question:
    st.session_state.rag_messages.append(
        {"role": "user", "content": final_question}
    )
    with st.chat_message("user"):
      st.markdown(final_question)

    rag_bundle = retrieve_rag_context(
        final_question, orders, rfm_customers, gold_prices, milk_prices
    )
    with st.chat_message("assistant"):
      with st.spinner("正在检索多表数据并生成分析..."):
        reply = generate_rag_answer(
            final_question,
            st.session_state.rag_messages[:-1],
            rag_bundle,
            rag_api_key,
            rag_base_url,
            rag_model,
        )
      st.markdown(reply)
      with st.expander("🔍 查看底层检索到的数据库上下文"):
        st.text(rag_bundle["context_str"])

    st.session_state.rag_messages.append({"role": "assistant", "content": reply})