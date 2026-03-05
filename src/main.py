import argparse
import base64
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "prompts" / "daily_briefing.md"
MAX_RETRIES = 3
DEFAULT_MAX_CHARS = 1500


def get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip()
    if not value:
        logging.warning("环境变量 %s 为空，使用默认值 %s", name, default)
        return default

    try:
        return int(value)
    except ValueError:
        logging.warning("环境变量 %s 不是有效整数(%r)，使用默认值 %s", name, raw, default)
        return default


def load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"未找到 prompt 文件: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def get_client() -> OpenAI:
    api_key = (
        os.environ.get("OPENAI_COMPAT_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY（或 OPENAI_COMPAT_API_KEY）")

    base_url = (
        os.environ.get("OPENAI_COMPAT_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
    )
    if base_url:
        return OpenAI(api_key=api_key, base_url=normalize_base_url(base_url))
    return OpenAI(api_key=api_key)


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    invalid_suffixes = ["/chat/completions", "/responses"]
    for suffix in invalid_suffixes:
        if normalized.endswith(suffix):
            fixed = normalized[: -len(suffix)]
            logging.warning(
                "检测到 OPENAI_BASE_URL/OPENAI_COMPAT_BASE_URL 配置为接口路径(%s)，"
                "已自动修正为 API 根路径: %s",
                normalized,
                fixed,
            )
            return fixed
    return normalized


def build_chat_completions_url(base_url: str) -> str:
    normalized = normalize_base_url(base_url)
    return f"{normalized}/chat/completions"


def should_fallback_to_chat_completions(exc: Exception) -> bool:
    if not isinstance(exc, requests.HTTPError):
        return False
    response = exc.response
    if response is None:
        return False

    if response.status_code in (400, 404, 405, 415, 422):
        return True

    text = response.text.lower()
    fallback_hints = [
        "responses",
        "unsupported",
        "not implemented",
        "unknown parameter",
        "web_search",
        "tools",
    ]
    return any(hint in text for hint in fallback_hints)


def extract_compat_message_text(data: dict[str, Any]) -> str:
    message = data.get("choices", [{}])[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        ]
        return "\n".join(part for part in texts if part).strip()
    return ""


def retry_with_backoff(func, action_name: str):
    for i in range(MAX_RETRIES):
        try:
            return func()
        except Exception as exc:
            if i == MAX_RETRIES - 1:
                logging.exception("%s 失败（已重试 %s 次）", action_name, MAX_RETRIES - 1)
                raise
            wait = 2 ** i
            logging.warning("%s 失败：%s；%ss 后重试(%s/%s)", action_name, exc, wait, i + 1, MAX_RETRIES - 1)
            time.sleep(wait)


def call_openai(mode: str, max_chars: int, force: bool) -> str:
    prompt = load_prompt()
    client = get_client()
    compat_base_url = (
        os.environ.get("OPENAI_COMPAT_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or ""
    ).strip().rstrip("/")
    compat_api_key = (
        os.environ.get("OPENAI_COMPAT_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    today = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d")
    extra = (
        f"\n\n执行参数：mode={mode}，max_chars={max_chars}，force={str(force).lower()}，日期={today}。"
        "请严格遵守结构与长度要求。"
    )

    def _call():
        user_prompt = (
            "请基于今天最新可得信息生成《每日投资情报早报》。"
            "必须执行至少5次web_search，涵盖宏观、科技、中国港股、黄金美元、地缘/贸易，"
            "并尽可能补充一次资金流/ETF。"
            "输出必须是中文 markdown，且包含来源链接与日期（YYYY-MM-DD）。"
            + extra
        )

        if compat_base_url.endswith("/chat/completions"):
            chat_url = compat_base_url
        elif compat_base_url:
            chat_url = build_chat_completions_url(compat_base_url)
        else:
            chat_url = ""

        def _call_chat_completions(require_web_search_hint: bool = False):
            if not chat_url:
                raise RuntimeError("未配置兼容接口 base_url，无法回退到 chat/completions")

            hint = "" if not require_web_search_hint else "\n\n若不支持 tools 字段，请忽略但尽量联网检索。"
            payload = {
                "model": os.getenv("OPENAI_MODEL", "gpt-5.2"),
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_prompt + hint},
                ],
            }
            headers = {
                "Authorization": f"Bearer {compat_api_key}",
                "Content-Type": "application/json",
            }
            resp = requests.post(chat_url, headers=headers, json=payload, timeout=180)
            resp.raise_for_status()
            return {"compat_chat_completion": resp.json()}

        if compat_base_url.endswith("/chat/completions"):
            return _call_chat_completions(require_web_search_hint=True)

        try:
            return client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.2"),
            tools=[{"type": "web_search"}],
            input=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            reasoning={"effort": "medium"},
            )
        except Exception as exc:
            if not should_fallback_to_chat_completions(exc):
                raise
            logging.warning("Responses API 不可用，自动回退到 chat/completions: %s", exc)
            return _call_chat_completions(require_web_search_hint=True)

    resp = retry_with_backoff(_call, "OpenAI Responses API 调用")
    if isinstance(resp, dict) and "compat_chat_completion" in resp:
        data = resp["compat_chat_completion"]
        text = extract_compat_message_text(data)
    else:
        text = (resp.output_text or "").strip()
    if not text:
        raise RuntimeError("OpenAI 返回内容为空")
    return text


def compress_markdown(md: str, max_chars: int) -> str:
    if len(md) <= max_chars:
        return md

    sections = re.split(r"\n(?=##\s)", md)
    for idx, sec in enumerate(sections):
        if sec.startswith("## B"):
            lines = sec.splitlines()
            compact = [lines[0]] + [ln for ln in lines[1:] if ln.startswith("- ")][:6]
            sections[idx] = "\n".join(compact)
    md = "\n".join(sections)
    if len(md) <= max_chars:
        return md

    for idx, sec in enumerate(sections):
        if sec.startswith("## A"):
            lines = sec.splitlines()
            compact = [lines[0]] + lines[1:10]
            sections[idx] = "\n".join(compact)
    md = "\n".join(sections)
    if len(md) > max_chars:
        md = md[: max_chars - 40].rstrip() + "\n\n（内容过长已自动压缩，保留核心证据链接）"
    return md


def load_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def markdown_to_image(markdown: str) -> bytes:
    width = 1200
    padding = 50
    line_spacing = 14
    bg_color = "#0b1020"
    panel_color = "#121a30"
    title_color = "#8be9fd"
    text_color = "#e6edf3"

    title_font = load_font(42)
    header_font = load_font(30)
    body_font = load_font(24)

    lines = []
    for raw in markdown.splitlines():
        text = raw.rstrip()
        if not text:
            lines.append(("", body_font, text_color))
            continue
        if text.startswith("# "):
            lines.append((text[2:], title_font, title_color))
        elif text.startswith("## "):
            lines.append((text[3:], header_font, "#ffd580"))
        else:
            prefix = "• " if text.startswith("- ") else ""
            content = text[2:] if text.startswith("- ") else text
            wrapped = wrap_text(prefix + content, body_font, width - 2 * padding)
            for w in wrapped:
                lines.append((w, body_font, text_color))

    total_h = padding * 2
    for text, font, _ in lines:
        bbox = font.getbbox(text or " ")
        total_h += (bbox[3] - bbox[1]) + line_spacing
    total_h += 60

    img = Image.new("RGB", (width, total_h), color=bg_color)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((20, 20, width - 20, total_h - 20), radius=24, fill=panel_color)

    y = padding
    for text, font, color in lines:
        draw.text((padding, y), text, font=font, fill=color)
        bbox = font.getbbox(text or " ")
        y += (bbox[3] - bbox[1]) + line_spacing

    bio = BytesIO()
    img.save(bio, format="PNG", optimize=True)
    return bio.getvalue()


def wrap_text(text: str, font, max_width: int):
    result = []
    current = ""
    for ch in text:
        test = current + ch
        bbox = font.getbbox(test)
        if (bbox[2] - bbox[0]) <= max_width:
            current = test
        else:
            result.append(current)
            current = ch
    if current:
        result.append(current)
    return result


def send_wecom_markdown(webhook: str, markdown: str):
    payload = {"msgtype": "markdown", "markdown": {"content": markdown}}
    r = requests.post(webhook, json=payload, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"企业微信 markdown HTTP 错误: {r.status_code} {r.text}")
    data = r.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"企业微信 markdown 发送失败: {json.dumps(data, ensure_ascii=False)}")


def send_wecom_image(webhook: str, png: bytes):
    b64 = base64.b64encode(png).decode("utf-8")
    md5 = hashlib.md5(png).hexdigest()
    payload = {"msgtype": "image", "image": {"base64": b64, "md5": md5}}
    r = requests.post(webhook, json=payload, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"企业微信 image HTTP 错误: {r.status_code} {r.text}")
    data = r.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"企业微信 image 发送失败: {json.dumps(data, ensure_ascii=False)}")


def parse_args():
    parser = argparse.ArgumentParser(description="生成并推送 AI 投资早报")
    parser.add_argument("--mode", choices=["markdown", "image"], default=os.getenv("DEFAULT_MODE", "markdown"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-chars", type=int, default=get_int_env("MAX_CHARS", DEFAULT_MAX_CHARS))
    return parser.parse_args()


def main():
    args = parse_args()
    webhook = os.getenv("WECOM_WEBHOOK")
    if not webhook:
        raise RuntimeError("缺少 WECOM_WEBHOOK")

    markdown = call_openai(args.mode, args.max_chars, args.force)
    markdown = compress_markdown(markdown, args.max_chars)

    if args.mode == "markdown":
        retry_with_backoff(lambda: send_wecom_markdown(webhook, markdown), "发送企业微信 markdown")
        logging.info("markdown 推送成功")
        return

    png = markdown_to_image(markdown)
    out = ROOT / "assets" / "latest_briefing.png"
    out.write_bytes(png)
    logging.info("已生成图片: %s", out)

    try:
        retry_with_backoff(lambda: send_wecom_image(webhook, png), "发送企业微信 image")
        logging.info("image 推送成功")
    except Exception as exc:
        logging.error("image 推送失败，降级 markdown：%s", exc)
        retry_with_backoff(lambda: send_wecom_markdown(webhook, markdown), "降级发送企业微信 markdown")
        logging.info("降级 markdown 推送成功")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("任务失败: %s", e)
        sys.exit(1)
