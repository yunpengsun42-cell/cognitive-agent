import os
import json
import urllib.request
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "glm-4-flash")


def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.7,
             max_tokens: int = 900) -> str:
    """调用大模型。无 key 或请求失败时使用 mock 兜底,保证全流程可跑通。"""
    if not API_KEY:
        return _mock(user_prompt)
    url = f"{BASE_URL}/chat/completions"
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:  # 网络/密钥异常时降级,不阻断应用
        print(f"[llm] 调用失败,启用 mock 兜底: {e}")
        return _mock(user_prompt)


def _mock(user_prompt: str) -> str:
    return (
        "（当前为离线兜底模式,未配置可用模型 key）\n"
        "如果这是训练场景:请回想你刚才描述的那个瞬间,"
        "当时你脑子里第一个冒出来的念头是什么?它让你做了什么选择?"
    )
