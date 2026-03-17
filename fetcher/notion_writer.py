"""
Notion API 写入模块 —— 精确适配「新闻-财经时政」数据库
字段清单（29个）：
  title        标题         (title)
  链接                      (url)
  时间                      (date)
  摘要                      (rich_text)
  原文摘录                  (rich_text)
  中文翻译                  (rich_text)
  来源                      (multi_select) → 官方公告/主流媒体/金融媒体/社交媒体/研究报告/其他
  主题                      (select)       → 宏观经济/央行与利率/财政与政治/...
  二级主题                  (multi_select) → 细分标签
  地区                      (multi_select) → 美国/中国/欧洲/...
  市场                      (multi_select) → 美股/A股/港股/...
  资产类别                  (multi_select) → 股票/利率/外汇/...
  优先级                    (select)       → P0/P1/P2
  重要性                    (select)       → 平平无奇/长期跟踪/值得一看/要写报告
  置信度                    (number)       → 0-100
  去重Key                   (rich_text)
  处理状态                  (status)       → 待处理
  已自动分析                (checkbox)
  自动分析时间              (date)
  要点                      (rich_text)
  分析提纲                  (rich_text)
  备注                      (rich_text)
  标签                      (rich_text)    ← 自由文本标签
  P0工作流                  (status)       → 待阅读（仅P0填写）
  报告截止                  (date)         ← 可选
  负责人/报告/创建时间/最后更新  ← 系统字段，不写入
"""
import json
import time
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

NOTION_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"

WRITE_INTERVAL = 0.4   # 写入间隔，避免 429
MAX_RETRIES = 3


# ── 字段枚举（严格匹配数据库中已有的选项名称）──────────────────────────

VALID_TOPICS = {
    "宏观经济", "央行与利率", "财政与政治", "外汇与跨境资金",
    "股票与因子", "行业与公司", "信用与金融稳定", "大宗商品与能源",
    "地缘政治与安全", "监管与法律", "科技与AI", "中国专题", "事件与突发风险"
}

VALID_MARKETS = {
    "美股", "A股", "港股", "欧股", "日股", "新兴市场（EM）", "全球"
}

VALID_ASSET_CLASSES = {
    "股票（Equity）", "利率（Rates）", "外汇（FX）", "信用（Credit）",
    "大宗商品（Commodities）", "加密（Crypto）", "波动率/衍生品（Vol/Derivatives）"
}

VALID_REGIONS = {
    "美国", "中国", "欧洲", "英国", "日本", "韩国", "印度", "中东", "拉美", "全球"
}

VALID_SOURCE_TYPES = {
    "官方公告", "主流媒体", "金融媒体", "社交媒体", "研究报告", "其他"
}

VALID_IMPORTANCE = {
    "平平无奇", "长期跟踪", "值得一看", "要写报告"
}

VALID_PRIORITY = {"P0", "P1", "P2"}


class NotionWriter:
    def __init__(self, token: str, db_id: str):
        self.token = token
        self.db_id = db_id
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict = None) -> dict:
        """发送 Notion API 请求，自动处理 429 退避"""
        url = f"{NOTION_API_BASE}{path}"
        data = json.dumps(payload).encode("utf-8") if payload else None

        for attempt in range(MAX_RETRIES):
            req = urllib.request.Request(url, data=data, method=method)
            for k, v in self._headers.items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = int(e.headers.get("Retry-After", 5))
                    print(f"    [429] Rate limit，等待 {retry_after}s (尝试 {attempt+1}/{MAX_RETRIES})")
                    time.sleep(retry_after)
                    continue
                elif e.code == 409:
                    print(f"    [409] 冲突，跳过")
                    return {}
                else:
                    body = e.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(f"Notion API {e.code}: {body[:300]}") from e
        raise RuntimeError(f"Notion API 请求超过最大重试次数: {path}")

    def query_db(self, filter_payload: dict = None) -> list:
        """查询数据库，返回所有结果（自动翻页）"""
        results = []
        cursor = None
        while True:
            payload = {"page_size": 100}
            if filter_payload:
                payload["filter"] = filter_payload
            if cursor:
                payload["start_cursor"] = cursor
            data = self._request("POST", f"/databases/{self.db_id}/query", payload)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            time.sleep(0.2)
        return results

    def find_existing(self, dedup_key: str) -> Optional[str]:
        """按去重 Key 查找已存在的页面，返回 page_id 或 None"""
        filter_payload = {
            "property": "去重Key",
            "rich_text": {"equals": dedup_key}
        }
        try:
            results = self.query_db(filter_payload)
            if results:
                return results[0]["id"]
        except Exception as e:
            print(f"    [warn] 去重查询失败: {e}")
        return None

    def create_page(self, properties: dict) -> dict:
        payload = {
            "parent": {"database_id": self.db_id},
            "properties": properties,
        }
        return self._request("POST", "/pages", payload)

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self._request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def upsert_article(self, article: dict) -> str:
        """
        写入或更新一篇文章
        返回 "created" | "updated" | "skipped"
        """
        dedup_key = make_dedup_key(article.get("title", ""), article.get("source_name", ""))
        props = build_properties(article, dedup_key)
        existing_id = self.find_existing(dedup_key)
        time.sleep(WRITE_INTERVAL)

        try:
            if existing_id:
                self.update_page(existing_id, props)
                return "updated"
            else:
                self.create_page(props)
                return "created"
        except Exception as e:
            print(f"    [error] 写入失败 ({article.get('title','')[:40]}): {e}")
            return "skipped"


# ── 工具函数 ─────────────────────────────────────────────────────────

def make_dedup_key(title: str, source: str) -> str:
    raw = f"{title.strip()}|{source.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _rt(s: str, max_len: int = 2000) -> list:
    """构造 rich_text 值"""
    return [{"text": {"content": str(s)[:max_len]}}]


def _multi_select(items: List[str], valid_set: set = None) -> dict:
    """构造 multi_select 值，过滤掉不合法的选项"""
    if valid_set:
        items = [i for i in items if i in valid_set]
    return {"multi_select": [{"name": i[:100]} for i in items[:10]]}


def _select(name: str, valid_set: set = None) -> dict:
    """构造 select 值"""
    if valid_set and name not in valid_set:
        return None
    return {"select": {"name": name}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_properties(article: dict, dedup_key: str) -> dict:
    """
    将 article dict 映射为 Notion properties 格式

    article 期望字段：
      title         str   标题（必须）
      url           str   原文链接
      summary       str   摘要
      excerpt       str   原文摘录（英文原文关键段）
      translation   str   中文翻译
      pub_time      str   发布时间 ISO8601
      source_name   str   来源媒体名称（用于去重和备注）
      source_type   str   来源类型 → 映射到 VALID_SOURCE_TYPES
      topic         str   主题 → 映射到 VALID_TOPICS
      sub_topics    list  二级主题 → 映射到 VALID_二级主题
      regions       list  地区
      markets       list  市场
      asset_classes list  资产类别
      priority      str   优先级 P0/P1/P2
      importance    str   重要性
      confidence    int   置信度 0-100
      key_points    str   要点（换行分隔）
      tags          str   自由标签（逗号分隔）
    """
    title = article.get("title", "(无标题)")
    url = article.get("url", "")
    pub_time = article.get("pub_time", "")
    priority = article.get("priority", "P2")
    importance = article.get("importance", "值得一看")
    confidence = article.get("confidence", 60)
    source_type = article.get("source_type", "主流媒体")
    topic = article.get("topic", "宏观经济")
    regions = article.get("regions", [])
    markets = article.get("markets", [])
    asset_classes = article.get("asset_classes", [])
    sub_topics = article.get("sub_topics", [])

    props: dict = {}

    # ── 必填字段 ──
    props["标题"] = {"title": [{"text": {"content": str(title)[:200]}}]}
    props["去重Key"] = {"rich_text": _rt(dedup_key)}
    props["处理状态"] = {"status": {"name": "待处理"}}
    props["已自动分析"] = {"checkbox": False}
    props["自动分析时间"] = {"date": {"start": _now_iso()}}

    # ── 链接 & 时间 ──
    if url:
        props["链接"] = {"url": url}
    if pub_time:
        # 确保格式符合 ISO8601
        try:
            # 尝试解析并标准化
            t = pub_time.replace("Z", "+00:00")
            props["时间"] = {"date": {"start": pub_time}}
        except Exception:
            pass

    # ── 文本内容 ──
    if article.get("summary"):
        props["摘要"] = {"rich_text": _rt(article["summary"])}
    if article.get("excerpt"):
        props["原文摘录"] = {"rich_text": _rt(article["excerpt"])}
    if article.get("translation"):
        props["中文翻译"] = {"rich_text": _rt(article["translation"])}
    if article.get("key_points"):
        props["要点"] = {"rich_text": _rt(article["key_points"])}
    if article.get("tags"):
        props["标签"] = {"rich_text": _rt(str(article["tags"])[:500])}
    if article.get("source_name"):
        props["备注"] = {"rich_text": _rt(f"来源媒体: {article['source_name']}")}

    # ── 结构化分类 ──
    source_type_val = source_type if source_type in VALID_SOURCE_TYPES else "主流媒体"
    props["来源"] = _multi_select([source_type_val])

    topic_val = topic if topic in VALID_TOPICS else None
    if topic_val:
        props["主题"] = {"select": {"name": topic_val}}

    importance_val = importance if importance in VALID_IMPORTANCE else "值得一看"
    props["重要性"] = {"select": {"name": importance_val}}

    priority_val = priority if priority in VALID_PRIORITY else "P2"
    props["优先级"] = {"select": {"name": priority_val}}

    # P0 新闻额外设置工作流状态
    if priority_val == "P0":
        props["P0工作流"] = {"status": {"name": "待阅读"}}

    # ── 数字 ──
    props["置信度"] = {"number": min(100, max(0, int(confidence)))}

    # ── multi_select ──
    if regions:
        props["地区"] = _multi_select(regions, VALID_REGIONS)
    if markets:
        props["市场"] = _multi_select(markets, VALID_MARKETS)
    if asset_classes:
        props["资产类别"] = _multi_select(asset_classes, VALID_ASSET_CLASSES)
    if sub_topics:
        # 二级主题选项非常多，不做严格过滤，由 Notion 自动拒绝无效选项
        props["二级主题"] = {"multi_select": [{"name": t[:100]} for t in sub_topics[:8]]}

    return props


# ── 快速测试 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, sys
    sys.stdout.reconfigure(encoding="utf-8")

    TOKEN = os.environ.get("NOTION_TOKEN", "")
    DB_ID = os.environ.get("NOTION_NEWS_DB", "0bbda71b549c4007ac53834a7633fee6")

    writer = NotionWriter(TOKEN, DB_ID)
    test_article = {
        "title": "[TEST] 新闻雷达 MVP 写入测试 2026-03-17",
        "url": "https://example.com/test-mvp",
        "summary": "这是一条测试条目，用于验证 Notion 写入模块是否正常工作，字段是否全部对齐。",
        "excerpt": "This is a test article to verify the Notion writer module.",
        "translation": "这是一篇用于验证 Notion 写入模块的测试文章。",
        "pub_time": "2026-03-17T08:00:00Z",
        "source_name": "新闻雷达 MVP",
        "source_type": "主流媒体",
        "topic": "科技与AI",
        "sub_topics": ["大模型（LLM）进展", "AI应用落地（Agent/企业应用）"],
        "regions": ["美国", "全球"],
        "markets": ["美股", "全球"],
        "asset_classes": ["股票（Equity）"],
        "priority": "P1",
        "importance": "值得一看",
        "confidence": 75,
        "key_points": "• 验证写入功能\n• 确认字段映射正确\n• 测试去重逻辑",
        "tags": "MVP, 测试, 新闻雷达",
    }
    print(f"写入数据库: {DB_ID}")
    print(f"文章: {test_article['title']}")
    result = writer.upsert_article(test_article)
    print(f"结果: {result}")
