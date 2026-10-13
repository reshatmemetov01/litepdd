from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import content, keyboards
from ..config import Config
from ..db import Database
from ..questions import QuestionBank
from ..services import channel_url, is_admin
from .exam import start_ticket

router = Router()


async def continue_funnel(bot: Bot, db: Database, config: Config, user_id: int) -> None:
    await bot.send_message(user_id, content.t("menu_hint"), reply_markup=keyboards.home_keyboard())
    await bot.send_message(user_id, content.t("about"), reply_markup=keyboards.show())
    await db.add_event(user_id, "about")


@router.message(CommandStart(deep_link=True, magic=F.args.regexp(r"^ticket_\d+$")))
async def start_ticket_link(message: Message, command: CommandObject, state: FSMContext, bot: Bot,
                            db: Database, config: Config, user: dict, questions: QuestionBank) -> None:
    """t.me/бот?start=ticket_5 — сразу запускает тест по билету (ссылка под видео-разбором в группе)."""
    await state.clear()
    number = int(command.args.split("_")[1])
    await message.answer(content.t("menu_hint"), reply_markup=keyboards.home_keyboard())
    if not await start_ticket(bot, db, user["user_id"], questions, number):
        await message.answer(content.TICKET_NOT_FOUND, reply_markup=keyboards.tickets(questions.ticket_numbers()))


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot,
                    db: Database, config: Config, user: dict, is_new: bool) -> None:
    await state.clear()
    uid = user["user_id"]
    # Источник трафика из ссылки t.me/бот?start=метка — запоминаем только при первом входе
    if is_new and command.args:
        await db.update_user(uid, source=command.args.strip()[:64])

    if user["paid"] and not is_admin(uid, config):
        await message.answer(content.t("welcome_back_paid"), reply_markup=keyboards.home_keyboard())
        await message.answer(content.t("main_menu"), reply_markup=keyboards.main_menu(False))
        return

    welcome = content.t("welcome", name=escape(message.from_user.first_name or "друг"))
    url = await channel_url(db, config)
    if url:
        # Канал — по желанию: кнопки «Подписаться» и «Продолжить»
        await message.answer(f"{welcome}\n\n{content.t('channel_prompt')}", reply_markup=keyboards.welcome(url))
        return
    await message.answer(welcome)
    await continue_funnel(bot, db, config, uid)


# sub:check — кнопка из старых сообщений, когда подписка была обязательной
@router.callback_query(F.data.in_({"go:continue", "sub:check"}))
async def go_continue(callback: CallbackQuery, bot: Bot, db: Database, config: Config, user: dict) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await continue_funnel(bot, db, config, user["user_id"])
