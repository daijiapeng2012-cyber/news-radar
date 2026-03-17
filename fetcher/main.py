"""
新闻雷达 · 主抓取脚本
流程：
  1. 读取 sources.json（RSS 源列表）
  2. 逐源抓取 RSS，备用 Google News 代理
  3. Skills 评分（scorer.py）
  4. 去重过滤
  5. 写入 Notion（notion_writer.py）
  6. 输出 data/news.json（前端读取）
  7. 写入运行日志
"""
import json
import os
import ssl
import time
import hashlib
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from scorer import score_article, load_strategy
from notion_writer import NotionWriter, make_dedup_key

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")
NEWS_JSON = os.path.join(DATA_DIR, "news.json")
FEED_STATUS_FILE = os.path.join(DATA_DIR, "feed_status.json")
RUN_LOG_FILE = os.path.join(DATA_DIR, "run_log.json")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_NEWS_DB = os.environ.get("NOTION_NEWS_DB", "0bbda71b549c4007ac53834a7633fee6")
DEBUG_MODE = os.environ.get("DEBUG_MODE", "false").lower() == "true"
FORCE_WRITE = os.environ.get("FORCE_WRITE", "false").lower() == "true"

# 最大写入条数
MAX_WRITE = 15
# 最小写入条数
MIN_WRITE = 3
# 文章时效窗口（小时）
TIME_WINDOW_HOURS = 168  # 7天
# 连续失败阈值 → 本次跳过
FAIL_THRESHOLD = 3
# 评分精选阈值
ELITE_THRESHOLD = 75


def load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}


def save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# RSS 抓取
# ──────────────────────────────────────────────
def make_ssl_context(verify: bool = True):
    if verify:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_rss(url: str, timeout: int = 20) -> str:
    """抓取 RSS，先验证 SSL，失败后降级忽略证书，再失败切备用 URL"""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NewsRadarBot/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    for verify_ssl in [True, False]:
        try:
            req = urllib.request.Request(url, headers=headers)
            ctx = make_ssl_context(verify_ssl)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}") from e
        except Exception as e:
            if not verify_ssl:
                raise RuntimeError(str(e)) from e
            # SSL 失败，降级重试
            print(f"      SSL 错误，降级重试: {e}")
            time.sleep(2)
    raise RuntimeError("fetch_rss: 不可能到达此处")


def google_news_rss(domain: str) -> str:
    """生成 Google News RSS 代理 URL"""
    return (
        f"https://news.google.com/rss/search"
        f"?q=site:{domain}&hl=en-US&gl=US&ceid=US:en"
    )


def parse_rss(xml_text: str) -> list:
    """解析 RSS XML，返回 [{title, url, summary, pub_time, pub_hours_ago}]"""
    # 预处理：去掉命名空间前缀避免 find 失败
    xml_clean = re.sub(r'<(\/?)[a-zA-Z]+:([a-zA-Z])', r'<\1\2', xml_text)
    try:
        root = ET.fromstring(xml_clean)
    except ET.ParseError:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise RuntimeError(f"RSS 解析失败: {e}") from e

    # 兼容 RSS 2.0 和 Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns) or root.findall(".//entry")
    now = datetime.now(timezone.utc)
    results = []

    def get_text(item, *tags):
        """安全获取元素文本，修复 ElementTree 元素真值判断 bug"""
        for tag in tags:
            el = item.find(tag)
            if el is not None:  # 必须用 is not None，不能用 bool(el)
                text = (el.text or "").strip()
                if text:
                    return text
                # 有些 RSS 把内容放在子元素里
                for child in el:
                    if child.text and child.text.strip():
                        return child.text.strip()
        return ""

    def get_attr(item, tag, attr):
        el = item.find(tag)
        if el is not None:
            return el.get(attr, "")
        return ""

    for item in items:
        title = get_text(item, "title")
        # link 可能是属性（Atom）或文本（RSS）
        url = get_text(item, "link", "guid")
        if not url:
            url = get_attr(item, "link", "href")
        summary = get_text(item, "description", "summary", "content")
        pub_str = get_text(item, "pubDate", "published", "updated", "date")

        # 正则兜底：直接从原始 XML 片段提取（应对奇葩格式）
        if not url:
            m = re.search(r'<link[^>]*>(https?://[^<]+)</link>', xml_text)
            if m:
                url = m.group(1).strip()
        if not pub_str:
            m = re.search(r'<pubDate[^>]*>([^<]+)</pubDate>', xml_text)
            if m:
                pub_str = m.group(1).strip()

        # 清理 HTML 标签
        summary = re.sub(r"<[^>]+>", "", summary) if summary else ""
        summary = summary[:500].strip()

        if not title or not url:
            continue
        if not url.startswith("http"):
            continue

        # 解析发布时间
        pub_hours_ago = 1.0  # 默认 1h 前（保守估计，确保不被过滤）
        pub_time_iso = ""
        if pub_str:
            try:
                pub_dt = parsedate_to_datetime(pub_str)
                pub_dt = pub_dt.astimezone(timezone.utc)
                pub_hours_ago = max(0.0, (now - pub_dt).total_seconds() / 3600)
                pub_time_iso = pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                try:
                    # 尝试 ISO 格式
                    from datetime import datetime as dt
                    pub_dt = dt.fromisoformat(pub_str.replace('Z', '+00:00'))
                    pub_dt = pub_dt.astimezone(timezone.utc)
                    pub_hours_ago = max(0.0, (now - pub_dt).total_seconds() / 3600)
                    pub_time_iso = pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    pass

        # 过滤时效窗口外
        if pub_hours_ago > TIME_WINDOW_HOURS:
            continue

        results.append({
            "title": title,
            "url": url,
            "summary": summary,
            "pub_time": pub_time_iso or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pub_hours_ago": pub_hours_ago,
        })

    return results



import re  # 需要在模块级别导入


# ──────────────────────────────────────────────
# 主逻辑
# ──────────────────────────────────────────────
def run():
    print(f"\n{'='*60}")
    print(f"新闻雷达 · 开始抓取 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if DEBUG_MODE:
        print("⚠️  调试模式：不写入 Notion")
    if FORCE_WRITE:
        print("⚡ 强制写入模式：忽略去重")
    print(f"{'='*60}")

    # 加载配置
    sources = load_json(SOURCES_FILE, [])
    strategy_weights = load_strategy()
    feed_status = load_json(FEED_STATUS_FILE, {})
    existing_news = load_json(NEWS_JSON, [])

    # 已存在的去重 key 集合（强制写入时清空）
    existing_keys = set() if FORCE_WRITE else {
        make_dedup_key(n.get("title", ""), n.get("source", ""))
        for n in existing_news
    }

    writer = None
    if NOTION_TOKEN and NOTION_NEWS_DB:
        writer = NotionWriter(NOTION_TOKEN, NOTION_NEWS_DB)
        print(f"✅ Notion 写入已启用 (DB: {NOTION_NEWS_DB[:8]}...)")
    else:
        print("⚠️  Notion 写入未配置，仅输出 JSON")

    # 统计
    stats = {
        "sources_total": len(sources),
        "sources_ok": 0,
        "sources_skip": 0,
        "sources_fail": 0,
        "articles_fetched": 0,
        "articles_scored": 0,
        "writes_created": 0,
        "writes_updated": 0,
        "writes_skipped": 0,
    }

    all_articles = []
    new_articles = []

    # 逐源抓取
    for src in sources:
        name = src.get("name", "")
        rss_url = src.get("rss_url", "")
        domain = extract_domain(src.get("website", ""))
        rating = src.get("rating", "")
        src_type = src.get("type", "")
        domain_tag = src.get("domain", "")

        if not rss_url:
            stats["sources_skip"] += 1
            continue

        # 检查连续失败
        fail_count = feed_status.get(name, {}).get("fail_count", 0)
        if fail_count >= FAIL_THRESHOLD:
            last_fail = feed_status.get(name, {}).get("last_fail", "")
            print(f"  ⏭ 跳过 {name}（连续失败 {fail_count} 次）")
            stats["sources_skip"] += 1
            continue

        print(f"\n  📡 {name}")
        articles = []

        # 尝试主 URL → 备用 Google News 代理
        urls_to_try = [rss_url]
        if domain:
            urls_to_try.append(google_news_rss(domain))

        success = False
        for url in urls_to_try:
            try:
                xml = fetch_rss(url)
                articles = parse_rss(xml)
                print(f"     ✅ 抓到 {len(articles)} 条 ({url[:60]}...)")
                # 重置失败计数
                feed_status[name] = {"fail_count": 0, "last_ok": now_iso()}
                stats["sources_ok"] += 1
                success = True
                break
            except Exception as e:
                print(f"     ❌ 失败 ({url[:50]}): {e}")
                time.sleep(2)

        if not success:
            fail_count += 1
            feed_status[name] = {
                "fail_count": fail_count,
                "last_fail": now_iso(),
            }
            stats["sources_fail"] += 1
            if fail_count >= FAIL_THRESHOLD:
                print(f"     ⚠️  连续失败 {fail_count} 次，下次将跳过")
            continue

        stats["articles_fetched"] += len(articles)

        # 评分
        for art in articles:
            dedup_key = make_dedup_key(art["title"], name)

            # 推断标签（简单规则）
            tags = infer_tags(art["title"], art["summary"], domain_tag)

            result = score_article(
                title=art["title"],
                summary=art["summary"],
                url=art["url"],
                source_name=name,
                source_rating=rating,
                source_type=src_type,
                tags=tags,
                pub_hours_ago=art["pub_hours_ago"],
                weights=strategy_weights,
            )

            enriched = {
                **art,
                "source": name,
                "source_rating": rating,
                "source_type": src_type,
                "domain": domain_tag,
                "tags": tags,
                "score": result["total"],
                "is_elite": result["is_elite"],
                "breakdown": result["breakdown"],
                "dedup_key": dedup_key,
            }
            all_articles.append(enriched)
            stats["articles_scored"] += 1

            if dedup_key not in existing_keys:
                new_articles.append(enriched)
                existing_keys.add(dedup_key)

    # 排序：先按分数，再按时间
    new_articles.sort(key=lambda x: (-x["score"], x["pub_hours_ago"]))
    all_articles.sort(key=lambda x: (-x["score"], x["pub_hours_ago"]))

    print(f"\n{'─'*40}")
    print(f"新文章: {len(new_articles)} 条 | 总文章: {len(all_articles)} 条")

    # 写入 Notion（只写新文章，上限 MAX_WRITE 条）
    to_write = new_articles[:MAX_WRITE]
    if writer and to_write and not DEBUG_MODE:
        print(f"\n📝 写入 Notion（{len(to_write)} 条）...")
        for art in to_write:
            action = writer.upsert_article(art)
            if action == "created":
                stats["writes_created"] += 1
            elif action == "updated":
                stats["writes_updated"] += 1
            else:
                stats["writes_skipped"] += 1
            print(f"   [{action}] {art['title'][:50]} (分数: {art['score']})")
            time.sleep(WRITE_INTERVAL)

    # 更新 news.json（保留最新 200 条）
    merged = new_articles + [
        n for n in existing_news
        if make_dedup_key(n.get("title", ""), n.get("source", "")) not in
        {make_dedup_key(a["title"], a["source"]) for a in new_articles}
    ]
    merged = merged[:200]
    save_json(NEWS_JSON, merged)
    print(f"\n💾 news.json 更新完毕（{len(merged)} 条）")

    # 保存 feed_status
    save_json(FEED_STATUS_FILE, feed_status)

    # 写运行日志
    total_writes = stats["writes_created"] + stats["writes_updated"]
    run_status = (
        "Success" if total_writes >= 1 else
        "ZeroWrite" if stats["articles_fetched"] > 0 else
        "Failed"
    )
    log_entry = {
        "time": now_iso(),
        "status": run_status,
        **stats,
    }
    run_log = load_json(RUN_LOG_FILE, [])
    run_log.insert(0, log_entry)
    save_json(RUN_LOG_FILE, run_log[:50])  # 保留最近 50 次

    print(f"\n{'='*60}")
    print(f"运行状态: {run_status}")
    print(f"来源: 成功 {stats['sources_ok']} / 跳过 {stats['sources_skip']} / 失败 {stats['sources_fail']}")
    print(f"写入: 新建 {stats['writes_created']} / 更新 {stats['writes_updated']} / 跳过 {stats['writes_skipped']}")
    print(f"{'='*60}\n")

    return run_status


def extract_domain(url: str) -> str:
    """从 URL 提取域名"""
    import re
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


def infer_tags(title: str, summary: str, domain_tag: str) -> list:
    """根据内容推断标签"""
    text = f"{title} {summary}".lower()
    tags = []

    tag_rules = [
        ("Agent", ["agent", "智能体", "自主"]),
        ("模型", ["model", "模型", "llm", "gpt", "claude", "gemini"]),
        ("开源", ["open source", "开源", "github", "repository"]),
        ("工具", ["tool", "工具", "sdk", "framework", "框架"]),
        ("研究", ["paper", "论文", "research", "study", "arxiv"]),
        ("产品", ["product", "产品", "launch", "发布", "release"]),
        ("MCP", ["mcp", "model context protocol"]),
        ("RAG", ["rag", "retrieval", "检索增强"]),
        ("安全", ["security", "安全", "vulnerability", "漏洞"]),
        ("财经", ["market", "markets", "stock", "finance", "经济", "金融"]),
    ]

    for tag, keywords in tag_rules:
        if any(kw in text for kw in keywords):
            tags.append(tag)

    # 加入领域标签
    if domain_tag:
        tags.append(domain_tag)

    return list(dict.fromkeys(tags))[:8]  # 去重，最多 8 个


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    import sys
    # 允许从命令行传入参数覆盖环境变量
    if len(sys.argv) > 1:
        os.environ["NOTION_TOKEN"] = sys.argv[1]
    if len(sys.argv) > 2:
        os.environ["NOTION_NEWS_DB"] = sys.argv[2]
    status = run()
    # ZeroWrite（抓到文章但没有新条目）不算失败
    sys.exit(0 if status in ("Success", "ZeroWrite") else 1)
