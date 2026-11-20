import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


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

overview_tab, rfm_tab = st.tabs(
    ["📊 销售与退款总览", "💎 客户价值分层 (RFM)"]
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
          .sort_values("客户人数", ascending=False)
      )
      total_rfm_users = segment_summary["客户人数"].sum()
      segment_summary["人数占比"] = (
          segment_summary["客户人数"] / total_rfm_users * 100
      )
      segment_summary["平均净消费"] = segment_summary["平均净消费"].round(2)

      # 优化点：使用 uniformtext 统一字号，防止小格子文字缩成蚂蚁大小；同时优化鼠标悬停提示
      treemap_chart = px.treemap(
          segment_summary,
          path=["客户分层"],
          values="客户人数",
          color="平均净消费",
          color_continuous_scale="Blues",
          custom_data=["人数占比", "平均净消费"],
          labels={"平均净消费": "平均净消费（元）"},
      )
      treemap_chart.update_traces(
          texttemplate=(
              "<b>%{label}</b><br>%{value} 人"
              " (%{customdata[0]:.1f}%)<br>平均净消费 ¥%{customdata[1]:,.2f}"
          ),
          hovertemplate=(
              "<b>%{label}</b><br>客户人数：%{value} 人"
              " (%{customdata[0]:.1f}%)<br>平均净消费："
              " ¥%{customdata[1]:,.2f}<extra></extra>"
          ),
          textfont=dict(size=14),
      )
      treemap_chart.update_layout(
          height=520,
          margin=dict(t=20, l=10, r=10, b=10),
          uniformtext=dict(minsize=13, mode="hide"),
      )
      st.plotly_chart(treemap_chart, width="stretch")

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
