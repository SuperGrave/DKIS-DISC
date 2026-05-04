from openai import OpenAI

from .config import AppConfig


class AIResponder:
    def __init__(self, config: AppConfig):
        self._client = OpenAI(api_key=config.openai_api_key)
        self._model = config.openai_model
        self._system_prompt = config.system_prompt

    def generate_reply(self, user_text: str) -> str:
        text = (user_text or "").strip()
        if not text:
            return "すみません、メッセージが空みたいです。もう一度送ってください。"

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": text},
            ],
        )
        reply = response.choices[0].message.content or ""
        reply = reply.strip()
        return reply or "すみません、うまく返答を作れませんでした。もう一度話しかけてください。"
