"""Фоновая отправка дожимающих сообщений тем, кто не купил.

Раз в FOLLOWUP_INTERVAL пользователь получает новое сообщение (без повторов),
ночью не пишем. После покупки или когда сообщения закончились — останавливаемся.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from . import content, keyboards, runtime
from .config import Config
from .db import Database
from .services import send_media

log = logging.getLogger(__name__)

CHECK_INTERVAL = 20  # секунд


def next_followup_time(now: int, config: Config) -> int:
    ts = now + int(content.FOLLOWUP_INTERVAL.total_seconds() * config.followup_delay_scale)
    if config.followup_delay_scale < 1:
        return ts  # ускоренный режим для проверки — без ночной паузы
    quiet_from, quiet_to = content.FOLLOWUP_QUIET_HOURS
    dt = datetime.fromtimestamp(ts, ZoneInfo(config.timezone))
    if dt.hour >= quiet_from:
        dt = (dt + timedelta(days=1)).replace(hour=quiet_to, minute=0, second=0)
    elif dt.hour < quiet_to:
        dt = dt.replace(hour=quiet_to, minute=0, second=0)
    return int(dt.timestamp())


async def pick_next(db: Database, config: Config, user: dict, sent: list[str]) -> str | None:
    if "checkout" not in sent and await db.has_event(user["user_id"], "checkout"):
        return "checkout"
    return next((key for key in content.FOLLOWUP_ORDER if key not in sent), None)


async def render(db: Database, key: str, config: Config) -> tuple[str, object]:
    button = content.FOLLOWUP_MESSAGES[key][1]
    manager = f": @{config.manager_username}" if config.manager_username else ""
    text = content.t(f"fu_{key}", price=config.price, manager=manager)
    return text, keyboards.followup(button, config.manager_username)


async def process_due(bot: Bot, db: Database, config: Config) -> int:
    _, config = await runtime.load(db, config)  # тексты, кнопки и имена из админки
    now = int(time.time())
    sent_count = 0
    for user in await db.due_followups(now):
        uid = user["user_id"]
        if uid in config.admin_ids:  # админам напоминания не шлём — только клиентам
            await db.update_user(uid, next_followup_at=None)
            continue
        sent = json.loads(user["followups_sent"] or "[]")
        key = await pick_next(db, config, user, sent)
        if key is None:
            await db.update_user(uid, next_followup_at=None)
            continue

        text, markup = await render(db, key, config)
        videos = await db.get_setting("followup_media", [])
        try:
            if videos:
                # Видео из админки: по очереди, у каждого напоминания своё; кнопка — «Получить доступ»
                if content.FOLLOWUP_MESSAGES[key][1] != "offer_support":
                    markup = keyboards.get_access()
                await send_media(bot, uid, videos[len(sent) % len(videos)], caption=text, reply_markup=markup)
            else:
                await bot.send_message(uid, text, reply_markup=markup)
            sent_count += 1
        except TelegramForbiddenError:
            await db.update_user(uid, blocked=1, next_followup_at=None)
            continue
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            continue
        except Exception:
            log.exception("Ошибка отправки дожима пользователю %s", uid)
            continue

        sent.append(key)
        has_more = await pick_next(db, config, user, sent) is not None
        await db.update_user(
            uid, followups_sent=json.dumps(sent),
            next_followup_at=next_followup_time(now, config) if has_more else None,
        )
        await asyncio.sleep(0.05)
    return sent_count


async def followup_worker(bot: Bot, db: Database, config: Config) -> None:
    while True:
        try:
            await process_due(bot, db, config)
        except Exception:
            log.exception("Сбой в цикле дожимающих сообщений")
        await asyncio.sleep(CHECK_INTERVAL)
