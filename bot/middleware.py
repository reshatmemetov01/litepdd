import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Chat, TelegramObject, User

from . import runtime
from .db import Database
from .followups import next_followup_time


class UserMiddleware(BaseMiddleware):
    """Создаёт/обновляет пользователя в базе и передаёт его в обработчик как `user`.

    Заодно подгружает настройки из админки (тексты, кнопки, имена) — см. runtime.py —
    и подменяет `config` на итоговый, с учётом «⚙️ Настройки».
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat: Chat | None = data.get("event_chat")
        if chat is not None and chat.type != "private":
            # Бот — админ закрытой группы и видит её сообщения. В группах и каналах молчим
            # и никого не заносим в базу: общение с клиентами только в личке.
            return None
        db: Database = data["db"]
        # base_config — как в .env; config — с учётом правок из админки
        data.setdefault("base_config", data["config"])
        _, config = await runtime.load(db, data["base_config"])
        data["config"] = config
        tg_user: User | None = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)
        user, is_new = await db.touch_user(
            tg_user.id, tg_user.username, tg_user.full_name, next_followup_time(int(time.time()), config),
        )
        data["user"] = user
        data["is_new"] = is_new
        return await handler(event, data)
