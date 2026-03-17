"""
translator.py — 轻量翻译模块
使用 Google 翻译非官方接口（无需 key），翻译英文标题和摘要为中文。

用法：
    from translator import translate_article
    article = translate_article(article)  # 原地添加 title_cn / summary_cn / translation 字段
"""
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import re


# Google 翻译非官方接口（稳定使用多年，无需 key）
_GOOGLE_TRANS_URL = (
    "https://translate.googleapis.com/translate_a/single"
    "?client=gtx&sl=auto&tl=zh-CN&dt=t&q={q}"
)

# 翻译超时（秒）
_TIMEOUT = 8
# 失败重试次数
_RETRIES = 2
# 请求间隔（秒），避免触发限速
_INTERVAL = 0.3
# 摘要最大翻译字符数
_SUMMARY_MAX_CHARS = 200


def _is_chinese(text: str) -> bool:
    """判断文本是否已是中文（超过 30% 中文字符则认为是中文）"""
    if not text:
        return False
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    return chinese_chars / max(len(text), 1) > 0.3


def _google_translate(text: str) -> str:
    """
    调用 Google 翻译非官方接口
    返回翻译结果字符串，失败返回空字符串
    """
    if not text or not text.strip():
        return ""

    # 已是中文则直接返回
    if _is_chinese(text):
        return text

    encoded = urllib.parse.quote(text[:500])
    url = _GOOGLE_TRANS_URL.format(q=encoded)

    for attempt in range(_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                }
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            # Google 返回格式：[[["译文","原文",...], ...], ...]
            parts = data[0]
            translated = "".join(
                seg[0] for seg in parts if seg and seg[0]
            )
            return translated.strip()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt < _RETRIES:
                time.sleep(1.0 * (attempt + 1))
                continue
            # 最终失败，静默返回空
            return ""
        except Exception:
            return ""


def translate_article(article: dict) -> dict:
    """
    翻译一篇文章的标题和摘要（前200字），原地修改并返回。

    添加字段：
      title_cn    str  中文标题（原标题为中文时等于原标题）
      summary_cn  str  中文摘要（原摘要为中文时等于原摘要）
      translation str  「标题：xxx\n摘要：xxx」合并文本，写入 Notion「中文翻译」字段
    """
    title = article.get("title", "")
    summary = article.get("summary", "")

    # ── 翻译标题 ──
    title_cn = _google_translate(title) if title else ""
    if not title_cn:
        title_cn = title  # 翻译失败保留原文
    time.sleep(_INTERVAL)

    # ── 翻译摘要（截取前 200 字） ──
    summary_snippet = summary[:_SUMMARY_MAX_CHARS].strip() if summary else ""
    if summary_snippet:
        summary_cn = _google_translate(summary_snippet)
        if not summary_cn:
            summary_cn = summary_snippet
    else:
        summary_cn = ""
    time.sleep(_INTERVAL)

    # ── 组合 translation 字段（写入 Notion「中文翻译」列） ──
    parts = []
    if title_cn and title_cn != title:
        parts.append(f"【标题】{title_cn}")
    if summary_cn and summary_cn != summary_snippet:
        parts.append(f"【摘要】{summary_cn}")
    translation = "\n".join(parts)

    article["title_cn"] = title_cn
    article["summary_cn"] = summary_cn
    article["translation"] = translation
    return article


def translate_batch(articles: list, verbose: bool = True) -> list:
    """
    批量翻译一批文章
    返回翻译后的文章列表（原地修改）
    """
    total = len(articles)
    for i, art in enumerate(articles):
        title = art.get("title", "")[:50]
        if _is_chinese(art.get("title", "")):
            art["title_cn"] = art.get("title", "")
            art["summary_cn"] = art.get("summary", "")
            art["translation"] = ""
            if verbose:
                print(f"   [{i+1}/{total}] 已是中文，跳过: {title}")
            continue

        translate_article(art)

        if verbose:
            cn = art.get("title_cn", "")[:40]
            status = "✓" if art.get("title_cn") != title else "✗(失败)"
            print(f"   [{i+1}/{total}] {status} {cn or title}")

    return articles


# ── 本地快速测试 ────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    tests = [
        {
            "title": "Federal Reserve holds interest rates steady amid inflation concerns",
            "summary": "The Federal Reserve kept its benchmark interest rate unchanged on Wednesday, "
                       "as policymakers weigh persistent inflation against signs of a cooling labor market.",
        },
        {
            "title": "Apple unveils new AI features for iPhone at WWDC 2026",
            "summary": "Apple announced a suite of artificial intelligence features for its iPhone lineup, "
                       "including on-device language models and enhanced Siri capabilities.",
        },
        {
            "title": "中国央行下调存款准备金率50个基点",
            "summary": "中国人民银行宣布，将于本月下旬下调金融机构存款准备金率0.5个百分点。",
        },
    ]

    print("=== 翻译测试 ===\n")
    for t in tests:
        result = translate_article(t.copy())
        print(f"原文: {result['title']}")
        print(f"译文: {result['title_cn']}")
        print(f"摘要译文: {result['summary_cn']}")
        print(f"translation字段: {result['translation']}")
        print()
