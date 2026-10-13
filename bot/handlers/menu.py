"""Главное меню и его разделы.

Внизу у клиента одна кнопка «Меню» — она присылает «Добро пожаловать!» с инлайн-кнопками
разделов. Раздел открывается в том же сообщении, «Назад» возвращает к меню.
"""
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import content, keyboards
from ..config import Config
from ..db import Database
from ..questions import QuestionBank
from ..services import is_admin
from .funnel import send_offer

router = Router()

SECTIONS = ("videos", "training", "reviews", "faq", "info", "support")
# Подписи кнопок старого нижнего меню: у кого оно ещё осталось, кнопки продолжают работать
OLD_REPLY_BUTTONS = {
    content.MENU_VIDEOS: "videos", "👑 Видео разборы": "videos", content.MENU_TRAINING: "training",
    content.MENU_REVIEWS: "reviews", content.MENU_FAQ: "faq", content.MENU_INFO: "info",
    content.MENU_SUPPORT: "support",
}


def main_menu_screen(user_id: int, config: Config) -> tuple[str, object]:
    return content.t("main_menu"), keyboards.main_menu(is_admin(user_id, config))


async def reviews_page(db: Database, page: int) -> tuple[str, object]:
    size = content.REVIEWS_PAGE_SIZE
    reviews, total = await db.reviews(limit=size, offset=page * size)
    if not total:
        return content.t("reviews_empty"), keyboards.with_back(None)
    pages = (total + size - 1) // size
    return content.reviews_text(reviews, page, pages), keyboards.reviews_nav(page, pages)


async def section_screen(name: str, db: Database, config: Config,
                         questions: QuestionBank) -> tuple[str, object]:
    if name == "training":
        if not questions.questions:
            return content.EXAM_NO_QUESTIONS, keyboards.with_back(None)
        text = content.t("training_intro", n=min(content.EXAM_QUESTIONS, len(questions.questions)),
                         max_errors=content.EXAM_MAX_ERRORS)
        return text, keyboards.with_back(keyboards.training())
    if name == "faq":
        contact = f"@{config.manager_username}" if config.manager_username else "через кнопку «Поддержка»"
        return content.t("faq", contact=contact), keyboards.with_back(None)
    if name == "info":
        return content.t("info"), keyboards.info(config.agreement_url, config.privacy_url)
    if name == "support":
        if not config.manager_username:
            return content.t("support_not_set"), keyboards.with_back(None)
        return content.t("support"), keyboards.with_back(keyboards.manager(config))
    return await reviews_page(db, 0)


async def open_section(name: str, bot: Bot, db: Database, config: Config, user: dict,
                       questions: QuestionBank, message: Message | None = None, edit: bool = False) -> None:
    """Открыть раздел: в том же сообщении (edit) или новым сообщением."""
    if name == "videos":  # купившим — ссылку в группу, остальным — оффер
        await send_offer(bot, db, config, user, back=True)
        return
    text, markup = await section_screen(name, db, config, questions)
    if edit and message:
        try:
            await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            return
        except TelegramBadRequest:  # например, это было фото или сообщение слишком старое
            pass
    await bot.send_message(user["user_id"], text, reply_markup=markup, disable_web_page_preview=True)


@router.message(F.text.func(keyboards.is_home_text))
@router.message(Command("menu"))
async def home(message: Message, state: FSMContext, config: Config, user: dict) -> None:
    await state.clear()
    text, markup = main_menu_screen(user["user_id"], config)
    await message.answer(text, reply_markup=markup)


@router.message(F.text.in_(OLD_REPLY_BUTTONS))
async def old_reply_button(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config,
                           user: dict, questions: QuestionBank) -> None:
    """Нажата кнопка старого нижнего меню — заменяем его кнопкой «Меню» и открываем раздел."""
    await state.clear()
    await message.answer(content.t("menu_hint"), reply_markup=keyboards.home_keyboard())
    await open_section(OLD_REPLY_BUTTONS[message.text], bot, db, config, user, questions)


@router.message(Command("support"))
async def support_command(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config,
                          user: dict, questions: QuestionBank) -> None:
    await state.clear()
    await open_section("support", bot, db, config, user, questions)


@router.callback_query(F.data.startswith("m:"))
async def section(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, config: Config,
                  user: dict, questions: QuestionBank) -> None:
    name = callback.data.split(":", 1)[1]
    await callback.answer()
    if name not in SECTIONS:
        return
    await state.clear()
    await open_section(name, bot, db, config, user, questions, message=callback.message, edit=True)


@router.callback_query(F.data.startswith("rev:"))
async def reviews_flip(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    text, markup = await reviews_page(db, int(callback.data.split(":")[1]))
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "menu:back")
async def back(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    """«Назад» — сообщение раздела превращается в главное меню."""
    await state.clear()
    await callback.answer()
    text, markup = main_menu_screen(callback.from_user.id, config)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:  # например, раздел был видео с подписью — отправим новое сообщение
        await callback.message.answer(text, reply_markup=markup)
