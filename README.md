# 新闻雷达 · News Radar MVP

> 财经时政新闻自动抓取 + AI 评分 + 写入 Notion，24小时全自动运行

## 📦 项目结构

```
news-radar/
├── fetcher/
│   ├── main.py              # 主抓取脚本（RSS多源 + SSL容错 + 429退避）
│   ├── scorer.py            # 财经时政 Skills 评分引擎（5维度 + 自动分类）
│   ├── notion_writer.py     # Notion API 写入模块（精确匹配「新闻-财经时政」库）
│   ├── strategy_updater.py  # RLHF 策略迭代脚本（每周运行）
│   └── sources.json         # 28个精选 RSS 源
├── data/
│   ├── strategy.json        # 当前评分策略（权重配置）
│   ├── feedback.json        # 用户反馈数据
│   ├── news.json            # 最新抓取结果（前端读取）
│   └── feed_status.json     # 各源抓取状态
├── frontend/
│   └── index.html           # Web 前端（从 GitHub 读取数据）
├── api/
│   └── feedback.py          # Vercel 反馈 API
├── .github/workflows/
│   └── fetch.yml            # GitHub Actions 定时任务（每6h抓取 + 每周迭代）
├── vercel.json              # Vercel 部署配置
└── requirements.txt         # Python 依赖
```

---

## 🚀 部署指南（约 10 分钟）

### Step 1：推送到 GitHub

```bash
# 在 GitHub 新建仓库（推荐命名：news-radar）
# 然后在本地：
cd d:\clawpool\Notion\news-radar
git init
git add .
git commit -m "🚀 Initial: 新闻雷达 MVP"
git remote add origin https://github.com/你的用户名/news-radar.git
git push -u origin main
```

### Step 2：配置 GitHub Secrets

在 GitHub 仓库页面：`Settings → Secrets and variables → Actions → New repository secret`

| Secret 名称 | 值 |
|-------------|-----|
| `NOTION_TOKEN` | 你的 Notion Integration Token（在 Notion 开发者后台获取）|
| `NOTION_NEWS_DB` | `0bbda71b549c4007ac53834a7633fee6` |

> ⚠️ **重要**：`NOTION_NEWS_DB` 对应的是「新闻-财经时政」数据库，请确认 Notion Integration 已被添加到该数据库的 Connections 中。

### Step 3：部署到 Vercel（前端）

1. 登录 [vercel.com](https://vercel.com)，点击 **Add New → Project**
2. 导入你的 `news-radar` 仓库
3. 配置环境变量（同上 Secrets）
4. 点击 **Deploy**

Vercel 会自动托管 `frontend/index.html`，并运行 `api/feedback.py`。

### Step 4：触发首次运行

在 GitHub → Actions → `新闻雷达 · 定时抓取` → **Run workflow** → 手动触发

首次运行约 3-5 分钟，完成后：
- `data/news.json` 会被更新（前端读取源）
- Notion 「新闻-财经时政」数据库会出现新条目

---

## ⚙️ 运行机制

### 自动调度

| 任务 | 频率 | 描述 |
|------|------|------|
| 新闻抓取 | 每6小时 | 0:00 / 6:00 / 12:00 / 18:00 UTC |
| 策略迭代 | 每周一 | 分析上周反馈，自动调整评分权重 |

### Skills 评分（5维度）

| Skill | 权重 | 说明 |
|-------|------|------|
| 市场影响力 | 35% | 对资产价格的潜在影响 |
| 时效性 | 25% | 是否最新突发事件 |
| 可信度 | 20% | 来源权威性 |
| 可操作性 | 15% | 能否形成交易/分析观点 |
| 噪音惩罚 | -5% | 广告/标题党降权 |

### 自动分类

评分引擎会自动推断：
- **主题**：宏观经济 / 央行与利率 / 科技与AI / 地缘政治与安全 等13个分类
- **二级主题**：FOMC/美联储决议 / 通胀（CPI/PCE）/ 大模型（LLM）进展 等80+个细分标签
- **优先级**：P0（≥85分）/ P1（≥65分）/ P2（其他）
- **地区**：美国 / 中国 / 欧洲 / 日本 等10个地区
- **市场**：美股 / A股 / 港股 / 欧股 等
- **资产类别**：股票 / 利率 / 外汇 / 大宗商品 等

### Notion 字段映射

写入「新闻-财经时政」数据库（`0bbda71b549c4007ac53834a7633fee6`）时自动填充：

| Notion 字段 | 来源 |
|------------|------|
| 标题 | RSS 标题 |
| 链接 | 原文 URL |
| 时间 | RSS 发布时间 |
| 摘要 | RSS description |
| 主题 | 评分引擎自动推断 |
| 二级主题 | 评分引擎自动推断 |
| 地区 | 评分引擎自动推断 |
| 市场 | 评分引擎自动推断 |
| 资产类别 | 评分引擎自动推断 |
| 优先级 | 评分 ≥ 85 → P0，≥ 65 → P1 |
| 重要性 | P0 → 要写报告，P1 → 值得一看 |
| 置信度 | 来源可信度评分 |
| 处理状态 | 默认「待处理」|
| 去重Key | title+source 的 SHA256 |

---

## 🔄 RLHF 策略迭代

在前端 Web 页面上，每篇文章都可以：
- 👍 点赞（标记为优质内容）
- 👎 踩（标记为低质内容）
- ⭐ 收藏

每周一 GitHub Actions 自动：
1. 统计上周反馈数据
2. 分析高分文章 vs 低分文章的特征
3. 调整 `strategy.json` 中的权重
4. 策略版本升级（记录到 `strategy_changelog.json`）

这样评分会越来越符合你的偏好。

---

## 🛠️ 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 本地测试抓取（会写入真实 Notion）
cd fetcher
python main.py

# 单独测试评分引擎
python scorer.py

# 单独测试 Notion 写入
python notion_writer.py

# 运行策略迭代
python strategy_updater.py
```

---

## 📡 RSS 源列表（28个精选）

覆盖：Reuters / Bloomberg / FT / WSJ / NYT / BBC / CNBC / MarketWatch / Investing.com / Seeking Alpha / Zero Hedge / The Economist 等主流财经媒体。

完整列表见 `fetcher/sources.json`。

---

## 🔑 配置说明

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `NOTION_TOKEN` | Notion Integration Token | （需配置）|
| `NOTION_NEWS_DB` | 目标数据库 ID | `0bbda71b549c4007ac53834a7633fee6` |
| `DEBUG_MODE` | 设为 `true` 则不写入 Notion | `false` |

---

## ✅ MVP 验证状态

- [x] 评分引擎：`scorer.py` 测试通过（美联储新闻70分，NVIDIA 63分，广告降权）
- [x] Notion 写入：`notion_writer.py` 测试通过（`created` 成功写入）
- [x] 字段对齐：精确匹配「新闻-财经时政」数据库全部29个字段
- [ ] 完整抓取流程：等待 GitHub Actions 首次运行
- [ ] 前端展示：等待 Vercel 部署
