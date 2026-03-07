_webhook_url = None  # 内部的に保持される変数

def set_webhook_url(url: str):
    global _webhook_url
    _webhook_url = url
    print(f"Webhook URL設定完了: {url}")

def get_webhook_url() -> str:
    return _webhook_url