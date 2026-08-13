import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)
VENDOR_DIR = os.path.join(PACKAGE_DIR, "vendor")
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")


def load_token():
    token = None
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == "notion_token":
                    token = value.strip()
    return token or os.environ.get("notion_token")


def get_client():
    from notion_client import Client

    token = load_token()
    if not token:
        raise RuntimeError("notion_token을 찾을 수 없습니다 (.env 또는 환경변수를 확인하세요)")
    return Client(auth=token)
