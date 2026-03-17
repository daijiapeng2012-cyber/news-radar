"""
Vercel Edge Function: 接收用户反馈
POST /api/feedback
{
  "type": "like" | "dislike",
  "title": "文章标题",
  "source": "来源名",
  "breakdown": {...},
  "note": "可选备注"
}
"""
import json
import os
import hashlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler


# GitHub API 写入反馈
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # e.g. "username/news-radar"
FEEDBACK_PATH = "data/feedback.json"


def get_github_file(path: str) -> tuple[str, str]:
    """获取 GitHub 文件内容和 sha"""
    import urllib.request
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "NewsRadarBot",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    import base64
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def update_github_file(path: str, content: str, sha: str, message: str):
    """更新 GitHub 文件"""
    import urllib.request
    import base64
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
        "sha": sha,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="PUT",
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "NewsRadarBot",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            feedback = json.loads(body)

            # 验证必要字段
            if feedback.get("type") not in ("like", "dislike"):
                self._respond(400, {"error": "type must be like or dislike"})
                return

            # 构造反馈条目
            entry = {
                "id": hashlib.sha256(
                    f"{feedback.get('title','')}|{datetime.now().isoformat()}".encode()
                ).hexdigest()[:12],
                "type": feedback["type"],
                "title": feedback.get("title", ""),
                "source": feedback.get("source", ""),
                "note": feedback.get("note", ""),
                "breakdown": feedback.get("breakdown", {}),
                "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            # 写入 GitHub（如果配置了）
            if GITHUB_TOKEN and GITHUB_REPO:
                try:
                    content, sha = get_github_file(FEEDBACK_PATH)
                    feedback_list = json.loads(content)
                    feedback_list.append(entry)
                    # 保留最近 500 条
                    feedback_list = feedback_list[-500:]
                    update_github_file(
                        FEEDBACK_PATH,
                        json.dumps(feedback_list, ensure_ascii=False, indent=2),
                        sha,
                        f"📊 用户反馈: {entry['type']} - {entry['title'][:30]}",
                    )
                except Exception as e:
                    print(f"GitHub 写入失败: {e}")
                    # 降级：仍然返回成功，避免影响用户体验

            self._respond(200, {"ok": True, "id": entry["id"]})

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_GET(self):
        """返回反馈统计"""
        try:
            if GITHUB_TOKEN and GITHUB_REPO:
                content, _ = get_github_file(FEEDBACK_PATH)
                feedback_list = json.loads(content)
            else:
                feedback_list = []
            stats = {
                "total": len(feedback_list),
                "likes": sum(1 for f in feedback_list if f.get("type") == "like"),
                "dislikes": sum(1 for f in feedback_list if f.get("type") == "dislike"),
                "recent": feedback_list[-5:],
            }
            self._respond(200, stats)
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _set_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _respond(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # 静默日志
