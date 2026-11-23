# 📊 电商全链路 BI 看板、双轨价格监控与私有数据 RAG 助手

基于 **Python + SQLite + Pandas + Streamlit + Plotly + GitHub Actions** 构建的轻量级商业智能（BI）与自动化数据工程系统。项目覆盖从底层多表关系型数据库建模、销售与退款全链路监控、RFM 客户价值分层，到跨设备在线采价闭环、云端定时行情采集及私有数据 RAG 智能问答的完整闭环。

🔗 **在线交互演示**：[点击访问 Streamlit Cloud 在线看板](https://ecommerce-dashboard-sxyqxentfzg3rvulaxwpe8.streamlit.app)

---

## 🏗️ 系统全景架构

```text
[ 数据采集与自动化管道 ]              [ 数据存储与动态迁移 ]            [ 交互可视化与 AI 助手 ]
• GitHub Actions 每日抓金价 ──┐
• Streamlit 网页在线采价表单 ─┼──▶ SQLite (shop.db) ──────────▶ Streamlit 三模块 Web 看板
  (GitHub REST API 自动回写)  │    (5张关联业务表 + 自动补列)    ├── 销售与退款全景总览
• GitHub Actions 3小时巡检 ───┘                                  ├── RFM 客户价值分层系统
  (漏报自动触发 SMTP 邮件提醒)                                   └── 双轨监控与多轮 RAG 助手
```

---

## ✨ 三大核心功能模块

### 1. 📊 销售与退款全景总览
* **核心指标动态联动**：实时核算**总销售额、总订单数、平均客单价、总退款金额**及扣除退款水分后的**净销售额**。
* **多维度交叉下钻**：支持按“商品类别”与“城市”进行全局动态筛选（支持留空默认全量展示）。
* **趋势与归因可视化**：
  * **各品类销售额对比**：柱状图直观呈现各业务线营收贡献。
  * **周度销售额趋势**：将原始日度订单按周聚合，平滑短期波动噪音，精准捕捉销售波峰。
  * **退款原因归因分析**：通过 `LEFT JOIN` 关联退款表，以环形图定位“物流时效”等核心退货诱因。
* **订单明细与数据导出**：支持筛选后订单明细的格式化展示（自动隐藏空值异常、自适应列宽）及一键导出 CSV。

### 2. 💎 客户价值分层系统（RFM 模型）
* **自动化五分位数打分**：基于剔除退款后的用户**净消费总额（Monetary）**，结合**最近一次消费间隔（Recency）**与**消费频次（Frequency）**，动态计算每位用户的 RFM 得分。
* **八大运营人群划分**：自动化识别并划分出**重要价值客户（核心金主）、重要唤回客户（高消费流失预警）、重要深耕客户、潜力客户、新客户、流失客户**等 8 类精准人群。
* **跨端自适应可视化**：采用带动态留白、置顶水平色阶图例及高对比度配色的横向柱状图，确保桌面端与手机移动端均能完整展示所有分层数据标签。
* **精准运营名单导出**：支持运营人员按客户分层二次圈选目标人群，并一键导出营销触达名单（CSV）。

### 3. 📈 双轨价格监控与私有数据 RAG 智能助手
* **全自动公开行情管道（路线一）**：通过 GitHub Actions 定时运行 `gold_tracker.py`，对接公开贵金属与汇率接口（内置三级容灾兜底），自动追踪近 30 天金价走势。
* **人机协同采价闭环（路线二）**：
  * **跨设备在线录入**：支持手机、Mac 或 Windows 浏览器直接在看板内填报每日商品价格或勾选**“已下架”**状态，提交后通过 GitHub REST API 自动回写持久化至仓库 `shop.db`，避免云端实例休眠导致数据丢失。
  * **3 小时无人值守巡检与邮件提醒**：GitHub Actions 每 3 小时运行 `milk_cloud_watchdog.py`，若当日已填报或已标记下架则静默结束；若检测到漏报，自动通过 SMTP 发送跨端提醒邮件。
* **私有数据 RAG 智能分析助手**：
  * **动态多表检索引擎**：实时提取经营总览、品类/城市退货率、RFM 核心金主、黄金高低点及牛奶采价状态切片作为上下文证据链。
  * **双模式对话交互**：支持多轮连续追问与一键业务诊断快捷按钮；填入兼容 OpenAI 格式的 API Key（如 DeepSeek）可进行深度推理，不填也可使用内置多意图规则引擎开箱即用。

---

## 🛠️ 技术栈与项目结构

* **数据存储与查询**：SQLite3（`LEFT JOIN` 多表关联、`COALESCE` 空值处理、`PRAGMA` 自动字段迁移）
* **数据处理与建模**：Python 3.11、Pandas、Faker、Requests、OpenPyXL
* **可视化与 Web 框架**：Streamlit（多标签页路由、Session State 对话管理）、Plotly Express
* **自动化运维与部署**：GitHub Actions（Cron 定时工作流）、GitHub REST API、Streamlit Community Cloud

```text
ecommerce-dashboard/
├── .github/
│   └── workflows/
│       ├── daily_gold.yml        # GitHub Actions：每日定时抓取最新黄金行情
│       └── milk_watchdog.yml     # GitHub Actions：每 3 小时巡检牛奶采价并触发邮件提醒
├── dashboard.py                  # Streamlit 主程序（三大分析模块、在线表单同步与 RAG 助手）
├── gold_tracker.py               # 黄金公开行情采集脚本（含主备 API 与本地基准容灾）
├── milk_cloud_watchdog.py        # 云端采价状态巡检与 SMTP 邮件通知脚本
├── shop.db                       # SQLite 业务数据库（users, orders, refunds, gold_prices, milk_prices）
├── requirements.txt              # 运行环境依赖清单
└── README.md                     # 项目说明文档
```

---

## 🚀 本地运行与云端配置指南

### 1. 本地启动
1. **克隆本项目到本地**
   ```bash
   git clone https://github.com
   cd ecommerce-dashboard
   ```
2. **安装依赖库**
   ```bash
   pip install -r requirements.txt
   ```
3. **启动看板服务**
   ```bash
   streamlit run dashboard.py
   ```
   启动后浏览器将自动打开 `http://localhost:8501`。

### 2. 云端自动化密钥配置（可选）
* **Streamlit Cloud Secrets**（用于网页表单自动回写数据库至 GitHub）：
  * `GITHUB_TOKEN`：具备仓库 `Contents: Read and write` 权限的 Personal Access Token
  * `GITHUB_REPO`：`Yscjc2019/ecommerce-dashboard`
* **GitHub Repository Secrets**（用于 3 小时漏报邮件提醒）：
  * `SMTP_SERVER`：发件服务器地址（如 `://qq.com` 或 `://gmail.com`）
  * `SENDER_EMAIL`：发件人邮箱地址
  * `SENDER_PASS`：邮箱 SMTP 授权码 / 应用专用密码
  * `DASHBOARD_URL`：Streamlit 在线看板公网网址
