import sqlite3
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import pandas as pd
import streamlit as st

DATABASE_PATH = Path(__file__).resolve().with_name("shop.db")

# 预设角色权限规则配置
ROLE_PERMISSIONS = {
    "Admin": {
        "label": "系统总监 (Admin)",
        "tabs": [
            "📊 销售与退款总览",
            "💎 客户价值分层 (RFM)",
            "📈 价格监控与 AI 助手 (RAG)",
            "🔐 权限与账号管理",
        ],
        "max_days": None,  # None 表示不限制历史时间跨度
        "allowed_cities": None,  # None 表示不限制城市
        "can_export": True,
        "can_write_price": True,
        "can_use_rag": True,
    },
    "Manager": {
        "label": "区域业务主管 (Manager)",
        "tabs": [
            "📊 销售与退款总览",
            "💎 客户价值分层 (RFM)",
            "📈 价格监控与 AI 助手 (RAG)",
        ],
        "max_days": 90,  # 仅可见近 90 天数据
        "allowed_cities": ["上海", "杭州"],  # 仅可见所辖大区城市
        "can_export": True,
        "can_write_price": True,
        "can_use_rag": True,
    },
    "Operator": {
        "label": "一线采价专员 (Operator)",
        "tabs": ["📈 价格监控与 AI 助手 (RAG)"],  # 屏蔽核心财务与全量客户表
        "max_days": 14,  # 仅可见近 14 天数据
        "allowed_cities": ["北京"],
        "can_export": False,  # 禁止导出商业数据
        "can_write_price": True,
        "can_use_rag": False,  # 禁止通过 AI 助手套取全库汇总数据
    },
}


def hash_password(raw_pwd: str) -> str:
  """对密码进行 SHA-256 哈希加密存储，杜绝明文密码"""
  return hashlib.sha256(raw_pwd.encode("utf-8")).hexdigest()


def init_rbac_table():
  """初始化系统账号表并写入默认演示账号"""
  with closing(sqlite3.connect(DATABASE_PATH)) as conn:
    conn.execute("""
            CREATE TABLE IF NOT EXISTS sys_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                department TEXT,
                created_at TEXT
            )
        """)
    # 检查是否已有初始账号
    count = conn.execute("SELECT COUNT(*) FROM sys_users").fetchone()[0]
    if count == 0:
      now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      default_users = [
          (
              "admin",
              hash_password("admin123"),
              "张总监",
              "Admin",
              "集团经营管理部",
              now_str,
          ),
          (
              "manager_sh",
              hash_password("mgr123"),
              "李主管 (华东)",
              "Manager",
              "华东大区运营部",
              now_str,
          ),
          (
              "op_bj",
              hash_password("op123"),
              "王专员 (北京)",
              "Operator",
              "北京一线采价组",
              now_str,
          ),
      ]
      conn.executemany(
          "INSERT INTO sys_users (username, password_hash, display_name, role,"
          " department, created_at) VALUES (?, ?, ?, ?, ?, ?)",
          default_users,
      )
      conn.commit()


def authenticate_user(username: str, password: str) -> dict | None:
  """校验账号密码并返回用户信息与权限配置"""
  init_rbac_table()
  with closing(sqlite3.connect(DATABASE_PATH)) as conn:
    row = conn.execute(
        "SELECT username, password_hash, display_name, role, department FROM"
        " sys_users WHERE username = ?",
        (username.strip(),),
    ).fetchone()

  if row and row[1] == hash_password(password):
    role = row[3]
    perms = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["Operator"])
    return {
        "username": row[0],
        "display_name": row[2],
        "role": role,
        "department": row[4],
        "perms": perms,
    }
  return None


def render_login_gate() -> dict | None:
  """渲染企业级登录门禁界面，登录成功后返回当前用户上下文"""
  init_rbac_table()
  if "current_user" in st.session_state and st.session_state.current_user:
    return st.session_state.current_user

  st.markdown("## 🔐 企业经营分析与价格监控系统 · 身份认证")
  st.info(
      "系统已启用基于角色的访问控制（RBAC）。不同岗位登录后将自动隔离**可见功能模块、历史数据时间跨度、管辖城市范围及导出/AI"
      " 权限**。"
  )

  col_login, col_demo = st.columns([1.2, 1])

  with col_login:
    with st.form("rbac_login_form"):
      u_input = st.text_input("登录账号", placeholder="请输入账号，如 admin")
      p_input = st.text_input(
          "登录密码", type="password", placeholder="请输入密码"
      )
      submit_login = st.form_submit_button(
          "安全登录", use_container_width=True
      )

      if submit_login:
        user_ctx = authenticate_user(u_input, p_input)
        if user_ctx:
          st.session_state.current_user = user_ctx
          st.rerun()
        else:
          st.error("❌ 账号或密码错误，请重试（或使用右侧快捷演示账号登录）。")

  with col_demo:
    st.markdown("#### ⚡ 演示快捷通道（一键切换岗位视角）")
    if st.button(
        "👑 以【系统总监 admin】登录（全量权限）", use_container_width=True
    ):
      st.session_state.current_user = authenticate_user("admin", "admin123")
      st.rerun()
    if st.button(
        "👔 以【华东主管 manager_sh】登录（近90天/沪杭）",
        use_container_width=True,
    ):
      st.session_state.current_user = authenticate_user("manager_sh", "mgr123")
      st.rerun()
    if st.button(
        "📋 以【北京专员 op_bj】登录（仅采价/近14天）",
        use_container_width=True,
    ):
      st.session_state.current_user = authenticate_user("op_bj", "op123")
      st.rerun()

  return None


def apply_row_level_security(
    orders_df: pd.DataFrame, user_ctx: dict
) -> pd.DataFrame:
  """行级数据权限过滤器（RLS）：按角色自动裁剪时间跨度与管辖城市"""
  df = orders_df.copy()
  perms = user_ctx["perms"]

  # 1. 城市行级隔离
  if perms["allowed_cities"] is not None:
    df = df[df["city"].isin(perms["allowed_cities"])]

  # 2. 时间跨度行级隔离（基于数据集中最新订单日期向前回溯 N 天）
  if perms["max_days"] is not None and not df.empty:
    df["order_date_dt"] = pd.to_datetime(df["order_date"])
    max_dt = df["order_date_dt"].max()
    cutoff_dt = max_dt - timedelta(days=perms["max_days"])
    df = df[df["order_date_dt"] >= cutoff_dt].drop(columns=["order_date_dt"])

  return df
