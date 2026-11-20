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
            orders.order_date
        FROM orders
        LEFT JOIN users ON orders.user_id = users.user_id
    """
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return pd.read_sql_query(query, connection)


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

total_sales = filtered_orders["amount"].sum()
order_count = len(filtered_orders)
average_order_value = total_sales / order_count if order_count else 0

metric_columns = st.columns(3)
metric_columns[0].metric("总销售额", f"¥{total_sales:,.2f}")
metric_columns[1].metric("总订单数", f"{order_count:,}")
metric_columns[2].metric("平均客单价", f"¥{average_order_value:,.2f}")

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
        top_users["user_name"] + "（用户 " + top_users["user_id"].astype(str) + "）"
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
st.dataframe(
    filtered_orders,
    width="stretch",
    hide_index=True,
    column_config={
        "order_id": "订单编号",
        "user_id": "用户编号",
        "name": "用户姓名",
        "city": "城市",
        "product_name": "商品名称",
        "category": "商品类别",
        "amount": st.column_config.NumberColumn("订单金额", format="¥%.2f"),
        "order_date": "下单日期",
    },
)
