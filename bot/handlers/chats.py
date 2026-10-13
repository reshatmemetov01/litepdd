"""Бота добавили в канал/группу (или убрали) — сообщаем админам и предлагаем выбрать роль чата."""
import logging
from html import escape

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated

from .. import keyboards
from ..config import Config
from ..db import Database
from ..services import notify_admins

log = logging.getLogger(__name__)
router = Router()


@router.my_chat_member()
async def bot_membership_changed(event: ChatMemberUpdated, bot: Bot, db: Database, config: Config) -> None:
    chat = event.chat
    if chat.type == "private":
        return
    title = escape(chat.title or str(chat.id))
    status = event.new_chat_member.status

    if status == ChatMemberStatus.ADMINISTRATOR:
        await notify_admins(
            bot, config,
            f"🔔 Меня сделали админом в «<b>{title}</b>».\n\nЧто это за чат?",
            reply_markup=keyboards.admin_setchat(chat.id),
        )
    elif status in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED):
        await notify_admins(
            bot, config,
            f"🔔 Меня добавили в «<b>{title}</b>», но не админом.\n\n"
            "Сделай меня <b>администратором</b> с правом «Пригласительные ссылки» — "
            "иначе я не смогу выдавать ссылки.",
        )
    elif status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        private = await db.get_setting("private_chat")
        if private and private["id"] == chat.id:
            await notify_admins(
                bot, config,
                f"⚠️ Меня удалили из «<b>{title}</b>». Выдача ссылок в группу не работает — "
                "верни меня админом.",
            )
