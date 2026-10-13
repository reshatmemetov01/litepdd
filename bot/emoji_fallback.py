"""Страховка для премиум-эмодзи.

Премиум (custom) эмодзи в тексте (<tg-emoji>) и на кнопках (icon_custom_emoji_id) бот может
отправлять, только если у владельца бота есть Telegram Premium или бот купил дополнительный
username на Fragment. Если это условие перестало выполняться (например, закончился Premium),
Telegram отклонит сообщение целиком — и клиент ничего не получит.

Чтобы воронка не ломалась, при ошибке Telegram запрос повторяется без премиум-эмодзи:
в тексте остаётся обычный эмодзи-заменитель, у кнопки — просто текст.
"""
import logging
import re
import time

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

log = logging.getLogger(__name__)

TG_EMOJI = re.compile(r'<tg-emoji\s+emoji-id="[^"]*"\s*>(.*?)</tg-emoji>', re.S)

# Когда в последний раз пришлось убирать премиум-эмодзи (показываем в админке)
last_failure: dict[str, object] = {"ts": 0, "error": ""}


def _strip_markup(markup):
    if isinstance(markup, InlineKeyboardMarkup):
        rows, attr = markup.inline_keyboard, "inline_keyboard"
    elif isinstance(markup, ReplyKeyboardMarkup):
        rows, attr = markup.keyboard, "keyboard"
    else:
        return markup, False
    changed = any(getattr(b, "icon_custom_emoji_id", None) for row in rows for b in row)
    if not changed:
        return markup, False
    new_rows = [[b.model_copy(update={"icon_custom_emoji_id": None}) for b in row] for row in rows]
    return markup.model_copy(update={attr: new_rows}), True


def strip_premium(method: TelegramMethod) -> TelegramMethod | None:
    """Копия запроса без премиум-эмодзи или None, если их там не было."""
    update = {}
    for field in ("text", "caption"):
        value = getattr(method, field, None)
        if isinstance(value, str) and "<tg-emoji" in value:
            update[field] = TG_EMOJI.sub(r"\1", value)
    markup, changed = _strip_markup(getattr(method, "reply_markup", None))
    if changed:
        update["reply_markup"] = markup
    return method.model_copy(update=update) if update else None


class PremiumEmojiFallback(BaseRequestMiddleware):
    async def __call__(self, make_request: NextRequestMiddlewareType, bot: Bot, method: TelegramMethod):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            stripped = strip_premium(method)
            if stripped is None:
                raise
            last_failure.update(ts=int(time.time()), error=e.message)
            log.warning("Telegram отклонил премиум-эмодзи (%s) — отправляю без них", e.message)
            return await make_request(bot, stripped)
