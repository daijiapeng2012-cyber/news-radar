"""
策略迭代脚本（每周一由 GitHub Actions 触发）
读取 feedback.json → 分析反馈 → 更新 strategy.json → 生成 changelog
"""
import json
import os
from datetime import datetime, timezone
from scorer import iterate_strategy, load_strategy, DEFAULT_WEIGHTS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")

FEEDBACK_FILE = os.path.join(DATA_DIR, "feedback.json")
STRATEGY_FILE = os.path.join(DATA_DIR, "strategy.json")
CHANGELOG_FILE = os.path.join(DATA_DIR, "strategy_changelog.json")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run():
    feedback_list = load_json(FEEDBACK_FILE, [])
    strategy = load_json(STRATEGY_FILE, {})
    changelog = load_json(CHANGELOG_FILE, [])

    current_version = strategy.get("version", 1)
    current_weights = strategy.get("weights", DEFAULT_WEIGHTS.copy())

    print(f"当前版本: v{current_version}")
    print(f"反馈数量: {len(feedback_list)} 条")
    print(f"  - 赞: {sum(1 for f in feedback_list if f.get('type') == 'like')}")
    print(f"  - 踩: {sum(1 for f in feedback_list if f.get('type') == 'dislike')}")

    if len(feedback_list) < 3:
        print("⚠️  反馈数量不足（< 3条），跳过迭代")
        return

    # 执行迭代
    new_weights = iterate_strategy(feedback_list, current_weights)

    # 生成 diff
    diff = []
    for k in current_weights:
        old_val = current_weights.get(k, 0)
        new_val = new_weights.get(k, 0)
        if abs(old_val - new_val) > 0.001:
            diff.append({
                "skill": k,
                "old": old_val,
                "new": new_val,
                "delta": round(new_val - old_val, 4),
            })

    new_version = current_version + 1
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 保存新策略
    new_strategy = {
        "version": new_version,
        "updated_at": now,
        "weights": new_weights,
        "feedback_count": len(feedback_list),
    }
    save_json(STRATEGY_FILE, new_strategy)

    # 保存 changelog
    log_entry = {
        "version": new_version,
        "date": now,
        "feedback_count": len(feedback_list),
        "diff": diff,
        "weights_before": current_weights,
        "weights_after": new_weights,
    }
    changelog.insert(0, log_entry)
    save_json(CHANGELOG_FILE, changelog[:20])

    # 清空已处理的反馈（可选：保留最近 10 条作为上下文）
    save_json(FEEDBACK_FILE, feedback_list[-10:])

    print(f"\n✅ 策略已更新到 v{new_version}")
    for d in diff:
        arrow = "↑" if d["delta"] > 0 else "↓"
        print(f"  {arrow} {d['skill']}: {d['old']:.3f} → {d['new']:.3f} ({d['delta']:+.4f})")

    if not diff:
        print("  (权重无显著变化)")


if __name__ == "__main__":
    run()
