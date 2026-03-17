"""
财经时政新闻 Skills 评分引擎 v2
- 5个评分维度（财经专用）
- 自动推断：主题/优先级/地区/市场/资产类别/二级主题
- 支持 RLHF 权重迭代（从 strategy.json 加载权重）
"""
import re
import json
import os
from typing import Dict, Any, List, Tuple

# ── 策略文件路径 ───────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGY_PATH = os.path.join(_BASE_DIR, "..", "data", "strategy.json")


# ── 关键词词典（财经时政专用）────────────────────────────────────────────

# 主题推断规则：关键词 → (主题, 二级主题列表, 资产类别列表, 地区列表, 市场列表)
TOPIC_RULES = [
    # ── 央行与利率 ──
    {
        "topic": "央行与利率",
        "keywords": ["fed", "fomc", "federal reserve", "美联储", "powell", "鲍威尔",
                     "interest rate", "利率", "rate cut", "rate hike", "降息", "加息",
                     "ecb", "欧洲央行", "boj", "日本央行", "pboc", "人民银行", "央行",
                     "boe", "英国央行", "qe", "量化宽松", "缩表", "扩表"],
        "sub_topics_hints": {
            "fomc|federal reserve|美联储|powell|鲍威尔|rate cut|rate hike|降息|加息": ["FOMC/美联储决议", "降息/加息预期路径（隐含利率）"],
            "ecb|欧洲央行": ["欧洲央行（ECB）"],
            "boj|日本央行": ["日本央行（BOJ）"],
            "pboc|人民银行": ["中国央行（PBOC）"],
            "boe|英国央行": ["英国央行（BOE）"],
            "yield curve|收益率曲线": ["收益率曲线（Curve）"],
            "tips|real yield|实际利率|通胀预期": ["实际利率与通胀预期（TIPS/BEI）"],
        },
        "asset_classes": ["利率（Rates）"],
    },
    # ── 宏观经济 ──
    {
        "topic": "宏观经济",
        "keywords": ["gdp", "cpi", "pce", "通胀", "inflation", "unemployment", "失业",
                     "nonfarm", "非农", "pmi", "ism", "manufacturing", "制造业", "消费",
                     "retail sales", "零售", "housing", "房屋", "贸易", "trade", "gdp"],
        "sub_topics_hints": {
            "cpi|pce|通胀|inflation": ["通胀（CPI/PCE）"],
            "nonfarm|非农|unemployment|失业": ["就业（NFP/失业率）"],
            "gdp|growth|增长|消费|投资": ["增长（GDP/消费/投资）"],
            "pmi|ism|制造业": ["制造业与景气（PMI/ISM）"],
            "housing|房屋|地产": ["住房与地产（Housing）"],
            "trade|贸易|经常账户": ["贸易与经常账户"],
        },
        "asset_classes": ["利率（Rates）", "外汇（FX）"],
    },
    # ── 财政与政治 ──
    {
        "topic": "财政与政治",
        "keywords": ["trump", "特朗普", "tariff", "关税", "congress", "国会", "budget",
                     "预算", "deficit", "赤字", "debt ceiling", "债务上限", "tax", "税",
                     "election", "选举", "政策", "policy", "law", "法案", "legislation"],
        "sub_topics_hints": {
            "tariff|关税|trade war|贸易战": ["跨境监管（中概/关税/出口管制）"],
            "budget|预算|deficit|赤字": ["预算/赤字/国债发行"],
            "debt ceiling|债务上限": ["债务上限/财政谈判"],
            "tax|税": ["税收政策（减税/加税）"],
            "election|选举": ["选举与政治风险"],
        },
        "asset_classes": ["股票（Equity）", "利率（Rates）"],
    },
    # ── 科技与AI ──
    {
        "topic": "科技与AI",
        "keywords": ["ai", "人工智能", "llm", "gpt", "claude", "gemini", "deepseek",
                     "nvidia", "英伟达", "semiconductor", "芯片", "chip", "gpu",
                     "openai", "anthropic", "meta ai", "robot", "机器人",
                     "data center", "数据中心", "cloud", "云计算", "software", "软件"],
        "sub_topics_hints": {
            "llm|gpt|claude|gemini|deepseek|openai|anthropic": ["大模型（LLM）进展"],
            "ai agent|agent|应用落地": ["AI应用落地（Agent/企业应用）"],
            "gpu|nvidia|英伟达|compute|算力": ["算力与GPU（Compute）"],
            "semiconductor|芯片|chip|tsmc|台积电": ["半导体制造（Foundry）", "半导体设计（Fabless）"],
            "data center|数据中心": ["数据中心与电力需求"],
            "robot|机器人|automation|自动化": ["机器人与自动化"],
            "cloud|云计算|enterprise software|企业软件": ["云计算与企业软件"],
            "ai regulation|ai监管|ai safety": ["AI监管与安全"],
        },
        "asset_classes": ["股票（Equity）"],
    },
    # ── 大宗商品与能源 ──
    {
        "topic": "大宗商品与能源",
        "keywords": ["oil", "原油", "brent", "wti", "opec", "natural gas", "天然气",
                     "gold", "黄金", "copper", "铜", "iron ore", "铁矿", "coal", "煤",
                     "lithium", "锂", "commodity", "大宗商品", "energy", "能源"],
        "sub_topics_hints": {
            "oil|原油|brent|wti": ["原油（Brent/WTI）"],
            "opec": ["OPEC+政策与产量"],
            "natural gas|天然气": ["天然气（LNG/库存/天气）"],
            "gold|黄金": ["黄金与贵金属"],
            "copper|铜|aluminum|铝": ["工业金属（铜/铝等）"],
            "lithium|锂|nickel|镍|cobalt|钴": ["电池金属（锂/镍/钴）"],
        },
        "asset_classes": ["大宗商品（Commodities）"],
    },
    # ── 股票与因子 ──
    {
        "topic": "股票与因子",
        "keywords": ["earnings", "财报", "eps", "revenue", "营收", "s&p", "nasdaq",
                     "dow", "buyback", "回购", "dividend", "分红", "ipo", "融资",
                     "vix", "volatility", "波动率", "options", "期权"],
        "sub_topics_hints": {
            "earnings|财报|eps|revenue|营收": ["财报（Earnings）"],
            "guidance|业绩指引": ["业绩指引（Guidance）"],
            "buyback|回购|dividend|分红": ["回购与分红（Buyback/Dividend）"],
            "ipo|融资|offering": ["融资与再融资（Equity/Debt Offering）"],
            "m&a|merger|acquisition|并购": ["并购重组（M&A）"],
            "vix|volatility|波动率": ["波动率（VIX/波动结构）"],
        },
        "asset_classes": ["股票（Equity）"],
    },
    # ── 外汇与跨境资金 ──
    {
        "topic": "外汇与跨境资金",
        "keywords": ["dollar", "美元", "dxy", "yen", "日元", "euro", "欧元",
                     "rmb", "人民币", "cny", "cnh", "fx", "外汇", "currency",
                     "carry trade", "套利", "capital flow", "资本流动"],
        "sub_topics_hints": {
            "dollar|美元|dxy": ["美元指数与G10外汇"],
            "rmb|人民币|cny|cnh": ["人民币（在岸/离岸）"],
            "yen|日元": ["日元与干预风险"],
            "carry|套利": ["利差交易（Carry）"],
            "capital flow|资本流动": ["资本流动/结售汇"],
        },
        "asset_classes": ["外汇（FX）"],
    },
    # ── 地缘政治与安全 ──
    {
        "topic": "地缘政治与安全",
        "keywords": ["war", "战争", "ukraine", "乌克兰", "russia", "俄罗斯", "israel",
                     "以色列", "iran", "伊朗", "taiwan", "台湾", "sanction", "制裁",
                     "military", "军事", "conflict", "冲突", "geopolitical", "地缘"],
        "sub_topics_hints": {
            "war|conflict|战争|冲突|military|军事": ["军事冲突与升级路径"],
            "sanction|制裁": ["制裁与反制"],
            "strait|海峡|red sea|红海": ["关键航道风险（海峡/红海等）"],
            "ceasefire|停火|negotiation|谈判": ["外交谈判/停火进展"],
        },
        "asset_classes": ["大宗商品（Commodities）", "外汇（FX）"],
    },
    # ── 信用与金融稳定 ──
    {
        "topic": "信用与金融稳定",
        "keywords": ["credit", "信用", "spread", "利差", "default", "违约", "bank",
                     "银行", "liquidity", "流动性", "systemic", "系统性风险",
                     "commercial real estate", "商业地产", "cre"],
        "sub_topics_hints": {
            "ig|hy|spread|利差|investment grade|高收益": ["投资级/高收益利差（IG/HY Spreads）"],
            "bank|银行|liquidity|流动性": ["银行业与流动性压力"],
            "default|违约|restructuring|重组": ["信用事件/违约与重组"],
            "cre|commercial real estate|商业地产": ["商业地产风险（CRE）"],
            "systemic|系统性": ["系统性风险/流动性冲击"],
        },
        "asset_classes": ["信用（Credit）"],
    },
    # ── 中国专题 ──
    {
        "topic": "中国专题",
        "keywords": ["china", "中国", "pboc", "人民银行", "a股", "a-share", "hong kong",
                     "港股", "property", "地产", "evergrande", "恒大", "beige book",
                     "两会", "npc", "政府工作报告"],
        "sub_topics_hints": {
            "social financing|社融|信贷|货币数据": ["社融/信贷/货币数据"],
            "rmb|人民币|汇率|外汇政策": ["汇率与外汇政策"],
            "property|地产|house|住房": ["地产政策与成交"],
            "ipo|资本市场|减持|交易规则": ["资本市场政策（IPO/减持/交易规则）"],
            "local government|地方财政|城投|化债": ["地方财政与化债/城投"],
            "industrial policy|产业政策|新能源|semiconductor": ["产业政策（新能源/半导体/AI）"],
            "export|出口|贸易": ["对外贸易与出口链"],
        },
        "asset_classes": ["股票（Equity）", "外汇（FX）"],
    },
    # ── 监管与法律 ──
    {
        "topic": "监管与法律",
        "keywords": ["sec", "regulation", "监管", "antitrust", "反垄断", "lawsuit", "诉讼",
                     "fine", "罚款", "compliance", "合规", "data privacy", "数据隐私",
                     "export control", "出口管制", "cfius"],
        "sub_topics_hints": {
            "sec|市场监管": ["SEC与市场监管"],
            "antitrust|反垄断": ["反垄断（Antitrust）"],
            "data privacy|数据隐私|gdpr": ["数据与隐私合规"],
            "lawsuit|诉讼|fine|罚款": ["重大诉讼与罚款"],
            "export control|出口管制|cfius|中概": ["跨境监管（中概/关税/出口管制）"],
        },
        "asset_classes": ["股票（Equity）"],
    },
    # ── 行业与公司 ──
    {
        "topic": "行业与公司",
        "keywords": ["apple", "microsoft", "google", "amazon", "meta", "tesla",
                     "berkshire", "jpmorgan", "goldman", "blackrock", "company",
                     "公司", "企业", "产品", "product", "supply chain", "供应链"],
        "sub_topics_hints": {
            "product|产品|demand|需求": ["产品发布与需求（Product/Demand）"],
            "supply chain|供应链": ["供应链与渠道（Supply Chain/Channel）"],
            "accident|事故|recall|召回": ["重大事故/停产/召回"],
        },
        "asset_classes": ["股票（Equity）"],
    },
    # ── 事件与突发风险 ──
    {
        "topic": "事件与突发风险",
        "keywords": ["breaking", "突发", "emergency", "紧急", "crash", "崩盘",
                     "black swan", "黑天鹅", "flash crash", "shock", "冲击",
                     "unexpected", "意外", "surprise", "超预期"],
        "sub_topics_hints": {
            "breaking|突发|emergency|紧急": ["重大突发（Breaking News）"],
            "systemic|系统性|liquidity shock|流动性冲击": ["系统性风险/流动性冲击"],
            "policy surprise|政策意外": ["政策意外（Surprise）"],
            "shock|冲击|超预期": ["数据大幅偏离预期（Shock）"],
        },
        "asset_classes": ["股票（Equity）", "利率（Rates）"],
    },
]

# 地区推断词典
REGION_KEYWORDS = {
    "美国": ["us ", "u.s.", "american", "america", "federal", "washington", "new york",
             "美国", "美联储", "国会", "白宫", "华尔街", "纽约", "芝加哥"],
    "中国": ["china", "chinese", "beijing", "shanghai", "中国", "北京", "上海", "深圳",
             "广州", "a股", "港股", "人民币", "两会", "pboc", "人民银行"],
    "欧洲": ["europe", "european", "eurozone", "ecb", "germany", "france", "italy",
             "欧洲", "欧元区", "欧洲央行", "德国", "法国", "意大利", "欧盟"],
    "英国": ["uk", "britain", "british", "boe", "london", "英国", "英格兰银行", "伦敦"],
    "日本": ["japan", "japanese", "boj", "tokyo", "yen", "nikkei",
             "日本", "日本央行", "东京", "日元", "日经"],
    "韩国": ["korea", "korean", "seoul", "kospi", "韩国", "首尔"],
    "印度": ["india", "indian", "sensex", "nifty", "rbi", "印度", "孟买"],
    "中东": ["middle east", "saudi", "israel", "iran", "opec", "gulf",
             "中东", "沙特", "以色列", "伊朗", "海湾"],
    "拉美": ["latin america", "brazil", "mexico", "argentina",
             "拉美", "巴西", "墨西哥", "阿根廷"],
}

# 市场推断词典
MARKET_KEYWORDS = {
    "美股": ["s&p", "nasdaq", "dow", "nyse", "spx", "qqq", "russell", "美股", "纽交所"],
    "A股": ["a股", "a-share", "上交所", "深交所", "沪深", "科创板", "创业板", "北交所"],
    "港股": ["hkex", "hang seng", "港股", "恒生", "香港股市", "h股"],
    "欧股": ["stoxx", "dax", "cac", "ftse", "欧股", "德股", "法股"],
    "日股": ["nikkei", "topix", "日经", "日股", "东证"],
    "全球": ["global", "worldwide", "全球", "国际市场"],
}

# 高影响力词汇（用于优先级评估）
HIGH_IMPACT_PHRASES = [
    "emergency", "breaking", "crisis", "crash", "surges", "plunges", "shock",
    "unexpected", "surprise", "historic", "record", "unprecedented",
    "突发", "紧急", "危机", "崩盘", "暴跌", "暴涨", "超预期", "历史性", "创纪录",
    "black swan", "黑天鹅", "systemic risk", "系统性风险",
    "war", "战争", "sanction", "制裁", "default", "违约",
]

MEDIUM_IMPACT_PHRASES = [
    "rate decision", "fomc", "nonfarm", "cpi report", "gdp", "earnings miss", "earnings beat",
    "利率决议", "非农数据", "cpi数据", "gdp数据", "财报", "盈利超预期", "盈利不及预期",
    "policy change", "政策调整", "merger", "acquisition", "ipo", "buyback",
]


def load_strategy() -> dict:
    """从 strategy.json 加载评分权重"""
    try:
        with open(STRATEGY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        pass
    # 默认策略（财经时政专用）
    return {
        "version": "1.0",
        "weights": {
            "market_impact": 0.35,    # 市场影响力：对资产价格的潜在影响
            "timeliness": 0.25,       # 时效性：是否是新鲜数据/事件
            "credibility": 0.20,      # 可信度：来源权威性
            "actionability": 0.15,    # 可操作性：能否形成交易/分析观点
            "noise_penalty": -0.05,   # 噪音惩罚：广告/标题党降权
        },
        "elite_threshold": 72,
        "p0_threshold": 85,
        "p1_threshold": 65,
    }


def infer_topic(text: str) -> Tuple[str, List[str], List[str], List[str], List[str]]:
    """
    根据文章文本推断：主题、二级主题、资产类别、地区、市场
    返回 (topic, sub_topics, asset_classes, regions, markets)
    """
    text_lower = text.lower()

    # 主题推断
    best_topic = "宏观经济"
    best_score = 0
    best_sub_topics: List[str] = []
    best_asset_classes: List[str] = []

    for rule in TOPIC_RULES:
        score = sum(1 for kw in rule["keywords"] if kw in text_lower)
        if score > best_score:
            best_score = score
            best_topic = rule["topic"]
            best_asset_classes = rule.get("asset_classes", [])

            # 推断二级主题
            sub_topics = []
            for pattern, tags in rule.get("sub_topics_hints", {}).items():
                if re.search(pattern, text_lower):
                    sub_topics.extend(tags)
            best_sub_topics = list(dict.fromkeys(sub_topics))[:5]  # 去重取前5

    # 地区推断
    regions = []
    for region, keywords in REGION_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            regions.append(region)
    if not regions:
        regions = ["全球"]

    # 市场推断
    markets = []
    for market, keywords in MARKET_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            markets.append(market)
    if not markets and regions:
        # 根据地区推断默认市场
        region_market_map = {
            "美国": "美股", "中国": "A股", "欧洲": "欧股", "日本": "日股", "英国": "欧股"
        }
        for r in regions:
            if r in region_market_map:
                markets.append(region_market_map[r])
    if not markets:
        markets = ["全球"]

    return best_topic, best_sub_topics, best_asset_classes, regions, markets


def score_article(article: dict, strategy: dict = None) -> dict:
    """
    对文章进行综合评分，返回增强后的 article（包含评分、分类信息）

    输入 article 字段：
      title, summary, url, source_name, source_type, pub_time, ...

    输出新增字段：
      score           综合评分 0-100
      priority        P0/P1/P2
      importance      重要性文字
      confidence      置信度
      topic           主题
      sub_topics      二级主题列表
      asset_classes   资产类别列表
      regions         地区列表
      markets         市场列表
      key_points      要点（占位，LLM模式下填充）
      breakdown       各维度得分明细
    """
    if strategy is None:
        strategy = load_strategy()

    weights = strategy.get("weights", {})
    elite_threshold = strategy.get("elite_threshold", 72)
    p0_threshold = strategy.get("p0_threshold", 85)
    p1_threshold = strategy.get("p1_threshold", 65)

    title = article.get("title", "")
    summary = article.get("summary", "")
    source_type = article.get("source_type", "主流媒体")
    full_text = f"{title} {summary}".lower()

    # ── Skill 1: 市场影响力 (0-100) ──────────────────────────────
    market_impact = 50  # 基础分
    # 高影响词汇加分
    high_hits = sum(1 for p in HIGH_IMPACT_PHRASES if p in full_text)
    medium_hits = sum(1 for p in MEDIUM_IMPACT_PHRASES if p in full_text)
    market_impact += min(35, high_hits * 15 + medium_hits * 8)
    # 来源类型加分
    source_bonus = {
        "官方公告": 20, "研究报告": 15, "金融媒体": 10,
        "主流媒体": 5, "社交媒体": 0, "其他": 0
    }
    market_impact += source_bonus.get(source_type, 0)
    market_impact = min(100, market_impact)

    # ── Skill 2: 时效性 (0-100) ──────────────────────────────────
    timeliness = 70  # 默认较新
    # 标题中有"breaking"/"突发"/"刚刚"类词提升时效性
    breaking_words = ["breaking", "just in", "突发", "刚刚", "实时", "最新", "今日"]
    if any(w in full_text for w in breaking_words):
        timeliness = 95
    elif any(w in full_text for w in ["weekly", "monthly", "report", "报告", "季报", "年报"]):
        timeliness = 55

    # ── Skill 3: 可信度 (0-100) ──────────────────────────────────
    credibility_map = {
        "官方公告": 95, "研究报告": 85, "金融媒体": 75,
        "主流媒体": 65, "社交媒体": 40, "其他": 50
    }
    credibility = credibility_map.get(source_type, 60)

    # ── Skill 4: 可操作性 (0-100) ────────────────────────────────
    actionability = 45  # 基础分
    action_phrases = [
        "trade", "buy", "sell", "position", "hedge", "交易", "买入", "卖出", "对冲",
        "outlook", "forecast", "预测", "预期", "策略", "配置", "建议",
        "opportunity", "risk", "机会", "风险", "影响", "impact"
    ]
    action_hits = sum(1 for p in action_phrases if p in full_text)
    actionability += min(45, action_hits * 8)

    # ── Skill 5: 噪音惩罚 (0-30，越高越噪音) ─────────────────────
    noise_score = 0
    noise_phrases = [
        "advertisement", "sponsored", "广告", "推广", "促销",
        "click here", "点击查看", "limited time", "限时", "优惠",
        "?", "!", "！", "？"  # 标题党标志
    ]
    title_noise = sum(1 for p in noise_phrases if p in title.lower())
    noise_score += min(30, title_noise * 10)
    # 标题过短或过长也扣分
    if len(title) < 10:
        noise_score += 15
    if title.count("!") + title.count("！") >= 2:
        noise_score += 10
    noise_score = min(30, noise_score)

    # ── 综合加权得分 ────────────────────────────────────────────────
    w_mi = weights.get("market_impact", 0.35)
    w_ti = weights.get("timeliness", 0.25)
    w_cr = weights.get("credibility", 0.20)
    w_ac = weights.get("actionability", 0.15)
    w_np = abs(weights.get("noise_penalty", 0.05))

    raw_score = (
        market_impact * w_mi +
        timeliness * w_ti +
        credibility * w_cr +
        actionability * w_ac -
        noise_score * w_np
    ) / (w_mi + w_ti + w_cr + w_ac)

    score = max(0, min(100, round(raw_score)))

    # ── 推断分类信息 ────────────────────────────────────────────────
    topic, sub_topics, asset_classes, regions, markets = infer_topic(full_text)

    # ── 优先级和重要性 ──────────────────────────────────────────────
    if score >= p0_threshold:
        priority = "P0"
        importance = "要写报告"
    elif score >= p1_threshold:
        priority = "P1"
        importance = "值得一看"
    elif score >= 45:
        priority = "P2"
        importance = "长期跟踪"
    else:
        priority = "P2"
        importance = "平平无奇"

    breakdown = {
        "market_impact": market_impact,
        "timeliness": timeliness,
        "credibility": credibility,
        "actionability": actionability,
        "noise_penalty": noise_score,
        "final_score": score,
    }

    # 返回增强后的 article（原地更新）
    result = dict(article)
    result.update({
        "score": score,
        "priority": priority,
        "importance": importance,
        "confidence": min(95, credibility + 5),
        "topic": topic,
        "sub_topics": sub_topics,
        "asset_classes": asset_classes,
        "regions": regions,
        "markets": markets,
        "breakdown": breakdown,
        "key_points": article.get("key_points", ""),  # LLM模式才填充
    })
    return result


def batch_score(articles: List[dict], strategy: dict = None) -> List[dict]:
    """批量评分，按得分降序排列"""
    if strategy is None:
        strategy = load_strategy()
    scored = [score_article(a, strategy) for a in articles]
    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored


# ── 快速测试 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    test_cases = [
        {
            "title": "Fed Holds Rates Steady, Signals Cautious Path Forward Amid Tariff Uncertainty",
            "summary": "The Federal Reserve kept interest rates unchanged at 4.25%-4.50% on Wednesday while signaling only two cuts are likely in 2026, as policymakers grapple with stubborn inflation and Trump tariff risks.",
            "source_name": "Reuters",
            "source_type": "金融媒体",
            "url": "https://reuters.com/test",
            "pub_time": "2026-03-17T14:00:00Z",
        },
        {
            "title": "NVIDIA Unveils Next-Gen Blackwell Ultra GPUs, Forecasts $5B Data Center Revenue",
            "summary": "NVIDIA announced its next-generation Blackwell Ultra architecture at GTC 2026, targeting AI inference workloads with 40% performance improvement over previous generation.",
            "source_name": "Bloomberg",
            "source_type": "金融媒体",
            "url": "https://bloomberg.com/test",
            "pub_time": "2026-03-17T09:00:00Z",
        },
        {
            "title": "China February CPI Falls -0.7%, Deflationary Pressure Deepens",
            "summary": "China's consumer price index fell 0.7% year-on-year in February, worse than the -0.4% consensus, as domestic demand remains weak and pork prices continue to weigh.",
            "source_name": "Caixin",
            "source_type": "金融媒体",
            "url": "https://caixin.com/test",
            "pub_time": "2026-03-17T02:00:00Z",
        },
        {
            "title": "点击查看！最新投资推荐！！！限时优惠！",
            "summary": "广告推广内容，点击查看详情。",
            "source_name": "unknown",
            "source_type": "其他",
            "url": "https://spam.com",
            "pub_time": "2026-03-17T00:00:00Z",
        },
    ]

    strategy = load_strategy()
    print("=" * 65)
    print("财经时政新闻评分引擎 v2 测试")
    print("=" * 65)
    for t in test_cases:
        result = score_article(t, strategy)
        print(f"\n标题: {result['title'][:55]}...")
        print(f"  评分: {result['score']:3d}  优先级: {result['priority']}  重要性: {result['importance']}")
        print(f"  主题: {result['topic']}")
        print(f"  地区: {result['regions']}  市场: {result['markets']}")
        print(f"  资产: {result['asset_classes']}")
        if result['sub_topics']:
            print(f"  二级: {result['sub_topics'][:3]}")
        b = result['breakdown']
        print(f"  明细: 市场影响={b['market_impact']} 时效={b['timeliness']} 可信={b['credibility']} 可操作={b['actionability']} 噪音惩罚={b['noise_penalty']}")
    print("\n" + "=" * 65)
