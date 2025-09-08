import os
from openai import OpenAI
from typing import List, Dict, Any
from settings import Settings

client = None
_settings = None

def get_client() -> OpenAI:
    global client, _settings
    if client is None:
        _settings = Settings()
        client = OpenAI(api_key=_settings.OPENAI_API_KEY)
    return client

def chat_completion(messages: List[Dict[str,str]], system_prompt: str | None = None, model: str | None = None) -> str:
    settings = Settings()
    c = get_client()
    msg_stack = []
    if system_prompt:
        msg_stack.append({"role": "system", "content": system_prompt})
    msg_stack.extend(messages)

    m = model or settings.MODEL_NAME
    resp = c.chat.completions.create(
        model=m,
        messages=msg_stack,
        temperature=0.3,
    )
    return resp.choices[0].message.content
