#!/usr/bin/env python3
"""
GitHub WebHook 接收服务
当 GitHub 推送代码到 main 分支时，自动执行 auto-update.sh 更新服务
"""

import hmac
import hashlib
import subprocess
from flask import Flask, request, jsonify

app = Flask(__name__)

# WebHook 密钥（你需要在 GitHub WebHook 设置中填写相同的密钥）
# 如果不需要验证签名，可以留空
WEBHOOK_SECRET = ""

def run_update():
    """执行自动更新脚本"""
    try:
        result = subprocess.run(
            ["/root/.openclaw/workspace_coder/a_share_market_monitor/auto-update.sh"],
            capture_output=True,
            text=True,
            timeout=300
        )
        output = result.stdout + "\n" + result.stderr
        return result.returncode == 0, output
    except Exception as e:
        return False, str(e)

@app.route("/webhook", methods=["POST"])
def webhook():
    # 验证签名（如果设置了密钥）
    if WEBHOOK_SECRET:
        signature_header = request.headers.get("X-Hub-Signature-256", "")
        if not signature_header.startswith("sha256="):
            return jsonify({"message": "Invalid signature format"}), 403
        
        digest = hmac.new(
            WEBHOOK_SECRET.encode(),
            request.get_data(),
            hashlib.sha256
        ).hexdigest()
        
        expected_signature = f"sha256={digest}"
        if not hmac.compare_digest(expected_signature, signature_header):
            return jsonify({"message": "Invalid signature"}), 403
    
    # 检查是否推送的是 main 分支
    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return jsonify({"message": "Not a push event, ignored"}), 200
    
    payload = request.get_json()
    ref = payload.get("ref", "")
    
    if ref != "refs/heads/main":
        return jsonify({"message": f"Ignored push to {ref}, only main branch is auto-updated"}), 200
    
    # 执行更新
    print("Received push to main branch, starting update...")
    success, output = run_update()
    print(output)
    
    if success:
        return jsonify({
            "message": "Update completed successfully",
            "output": output
        }), 200
    else:
        return jsonify({
            "message": "Update failed",
            "output": output
        }), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "a-share-etf-monitor-webhook"}), 200

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8082)
