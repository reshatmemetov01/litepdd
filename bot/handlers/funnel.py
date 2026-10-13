import time
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import content, keyboards
from ..config import Config
from ..db import Database
from ..services import (
    is_admin,
    is_channel_member,
    make_access_link,
    manager_suffix,
    notify_admins,
    send_media,
    user_link,
)

router = Router()


class PayForm(StatesGroup):
    promo = State()


async def send_access_again(bot: Bot, db: Database, config: Config, user_id: int, back: bool = False) -> None:
    def markup(base=None):
        return keyboards.with_back(base) if back else base

    if await is_channel_member(bot, db, config, user_id):
        await bot.send_message(user_id, content.t("access_already_member"), reply_markup=markup())
        return
    link = await make_access_link(bot, db, config, user_id)
    if link:
        await bot.send_message(user_id, content.t("access_link_again", link=link), reply_markup=markup())
    else:
        await bot.send_message(user_id, content.t("access_manual"), reply_markup=markup(keyboards.manager(config)))


async def send_offer(bot: Bot, db: Database, config: Config, user: dict, back: bool = False) -> None:
    """back=True — открыто из нижнего меню, добавляем кнопку «Назад»."""
    # Админ всегда видит оффер, чтобы можно было тестировать оплату повторно
    if user["paid"] and not is_admin(user["user_id"], config):
        await send_access_again(bot, db, config, user["user_id"], back=back)
        return
    await db.add_event(user["user_id"], "checkout")
    markup = keyboards.offer(config.manager_username, config.payment_url)
    await bot.send_message(
        user["user_id"],
        offer_text(config),
        reply_markup=keyboards.with_back(markup) if back else markup,
        disable_web_page_preview=True,
    )


def offer_text(config: Config) -> str:
    return content.t(
        "offer",
        price_line=content.price_line(config.price, config.old_price),
        contact=manager_suffix(config),
        legal=content.legal_line(config.agreement_url, config.privacy_url),
    )


@router.callback_query(F.data == "f:show")
async def show(callback: CallbackQuery, bot: Bot, db: Database, config: Config, user: dict) -> None:
    await callback.answer()
    await db.add_event(user["user_id"], "show")
    demo = await db.get_setting("demo_media")
    if demo:
        await send_media(bot, user["user_id"], demo, caption=content.t("show"), reply_markup=keyboards.after_show())
    else:
        await callback.message.answer(content.t("show"), reply_markup=keyboards.after_show())


@router.callback_query(F.data == "f:offer")
async def offer(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, config: Config,
                user: dict) -> None:
    await callback.answer()
    await state.clear()
    await send_offer(bot, db, config, user)


# ------------------------------------------------ оплата по СБП (временная)

@router.callback_query(F.data == "pay:sbp")
async def pay_sbp(callback: CallbackQuery, db: Database, config: Config, user: dict) -> None:
    await callback.answer()
    if config.payment_url:
        await callback.message.answer(
            content.t("sbp_link", price=config.price), reply_markup=keyboards.sbp(config.payment_url)
        )
        return
    if not config.payment_details:
        await callback.message.answer(
            content.t("payment_not_ready", manager=manager_suffix(config)),
            reply_markup=keyboards.manager(config),
        )
        return
    await callback.message.answer(
        content.t("sbp_details", price=config.price, details=config.payment_details),
        reply_markup=keyboards.sbp(),
    )


@router.callback_query(F.data == "pay:sent")
async def pay_sent(callback: CallbackQuery, bot: Bot, db: Database, config: Config, user: dict) -> None:
    """«Я оплатил» — админу уходит заявка на проверку с кнопками «Подтвердить / Отклонить»."""
    uid = user["user_id"]
    if user["paid"] and not is_admin(uid, config):
        await callback.answer()
        await send_access_again(bot, db, config, uid)
        return
    if await db.pending_request_for(uid):
        await callback.answer()
        await callback.message.answer(content.t("payment_already_checking"))
        return
    await callback.answer()
    await db.add_event(uid, "pay_click")
    request_id = await db.create_payment_request(uid, None)
    await callback.message.answer(content.t("payment_checking"))
    await notify_admins(bot, config, payment_request_text(request_id, user, config),
                        reply_markup=keyboards.admin_payment(request_id))


def payment_request_text(request_id: int, user: dict, config: Config, created_at: int | None = None) -> str:
    when = datetime.fromtimestamp(created_at or time.time(), ZoneInfo(config.timezone)).strftime("%d.%m.%Y %H:%M")
    username = f"@{user['username']}" if user.get("username") else "нет username"
    return (
        f"💳 <b>Оплата на проверку #{request_id}</b>\n\n"
        f"👤 {user_link(user)}\n"
        f"🔗 {escape(username)} · ID <code>{user['user_id']}</code>\n"
        f"🕒 {when} ({config.timezone.split('/')[-1]})\n"
        f"💰 {config.price} ₽\n\n"
        "Проверь поступление в кабинете Paydex."
    )


# ------------------------------------------------------------------ промокод

@router.callback_query(F.data == "pay:promo")
async def promo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(PayForm.promo)
    await callback.message.answer(content.t("promo_ask"))


@router.message(StateFilter(PayForm.promo), F.text)
async def promo_code(message: Message, state: FSMContext, config: Config) -> None:
    # Промокодов пока нет — когда появятся, проверка будет здесь
    await state.clear()
    await message.answer(content.t("promo_not_found"), reply_markup=keyboards.offer(config.manager_username, config.payment_url))
