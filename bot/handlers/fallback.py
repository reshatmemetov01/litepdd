from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from .. import content

router = Router()


@router.message(StateFilter(None))
async def any_message(message: Message) -> None:
    await message.answer(content.t("fallback"))


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела — нажми /start")
