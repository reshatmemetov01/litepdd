import asyncio
import logging
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User

from .. import content, keyboards
from ..config import Config
from ..db import Database
from ..services import (
    extract_media,
    grant_access,
    manager_suffix,
    private_chat_id,
    send_media,
    channel_url,
    user_link,
)

log = logging.getLogger(__name__)
router = Router()

PAGE_SIZE = 15


class IsAdmin(Filter):
    async def __call__(self, event, config: Config, event_from_user: User | None = None) -> bool:
        return event_from_user is not None and event_from_user.id in config.admin_ids


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class AdminForm(StatesGroup):
    broadcast = State()
    grant = State()
    demo = State()
    followup_video = State()
    review_text = State()
    review_author = State()
    clean_one = State()


async def admin_menu_text(db: Database) -> tuple[str, object]:
    pending = len(await db.pending_payment_requests())
    return "🛠 <b>Админка</b>", keyboards.admin_menu(pending)


@router.message(Command("admin"))
@router.message(F.text == content.MENU_ADMIN)
async def admin_entry(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    text, markup = await admin_menu_text(db)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "adm:menu")
async def admin_menu(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await callback.answer()
    text, markup = await admin_menu_text(db)
    await callback.message.answer(text, reply_markup=markup)


# ---------------------------------------------------------------- статистика

def period_start(period: str, tz: str) -> int:
    now = datetime.now(ZoneInfo(tz))
    if period == "day":
        return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if period == "week":
        return int(now.timestamp()) - 7 * 86400
    return 0


def pct(part: int, whole: int) -> str:
    return f" ({part / whole * 100:.0f}%)" if whole else ""


@router.callback_query(F.data.startswith("adm:stats:"))
async def stats(callback: CallbackQuery, db: Database, config: Config) -> None:
    period = callback.data.split(":")[2]
    c = await db.event_counts(period_start(period, config.timezone))
    title = {"day": "сегодня", "week": "за 7 дней", "all": "за всё время"}[period]
    started = c.get("start", 0)
    paid = c.get("paid", 0)

    lines = [
        f"📊 <b>Статистика {title}</b>\n",
        f"👋 Зашли в бота: <b>{started}</b>",
        f"⭐ Смотрели «Показать»: <b>{c.get('show', 0)}</b>{pct(c.get('show', 0), started)}",
        f"🛒 Дошли до оплаты: <b>{c.get('checkout', 0)}</b>{pct(c.get('checkout', 0), started)}",
        f"💳 Нажали «Я оплатил»: <b>{c.get('pay_click', 0)}</b>",
        f"💰 Купили: <b>{paid}</b>{pct(paid, started)}",
        f"💵 Выручка: <b>{paid * config.price:,} ₽</b>".replace(",", " "),
        "",
        f"📝 Экзамен: начали {c.get('exam_start', 0)}, сдали {c.get('exam_pass', 0)}, "
        f"не сдали {c.get('exam_fail', 0)}",
        "",
        f"🔥 Сейчас дошли до оплаты и не купили (всего): <b>{await db.checkout_not_paid_count()}</b>",
    ]
    if period == "all":
        sources = await db.source_counts()
        if any(s["src"] != "—" for s in sources):
            lines.append("\n🔗 <b>Источники</b> (зашли / купили)")
            lines += [f"{s['src']}: {s['total']} / {s['paid'] or 0}" for s in sources]
    lines.append("\n<i>% — от тех, кто зашёл за этот период</i>")

    await callback.answer()
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=keyboards.admin_stats(period))
    except Exception:  # «message is not modified» при повторном нажатии
        pass


# -------------------------------------------------------------- пользователи

@router.callback_query(F.data == "adm:users")
async def users_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("👥 <b>Пользователи</b>\n\nКого показать?", reply_markup=keyboards.admin_users())


@router.callback_query(F.data.startswith("adm:list:"))
async def users_list(callback: CallbackQuery, db: Database, config: Config) -> None:
    _, _, kind, page_s = callback.data.split(":")
    page = int(page_s)
    rows, total = await db.list_users(kind, PAGE_SIZE, page * PAGE_SIZE)
    title = {
        "checkout": "🛒 Дошли до оплаты, но не купили",
        "paid": "✅ Купили",
        "all": "🆕 Все пользователи",
    }[kind]
    tz = ZoneInfo(config.timezone)
    lines = [f"<b>{title}</b> — {total}\n"]
    for i, u in enumerate(rows, start=page * PAGE_SIZE + 1):
        when = datetime.fromtimestamp(u["ts"], tz).strftime("%d.%m %H:%M") if u["ts"] else ""
        mark = " 💰" if kind == "all" and u["paid"] else ""
        lines.append(f"{i}. {user_link(u)} — {when}{mark}")
    if not rows:
        lines.append("Пока никого.")
    await callback.answer()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboards.admin_list_nav(kind, page, (page + 1) * PAGE_SIZE < total),
        disable_web_page_preview=True,
    )


# ----------------------------------------------------- подтверждение оплат

@router.callback_query(F.data == "adm:pending")
async def pending(callback: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    await callback.answer()
    requests = await db.pending_payment_requests()
    if not requests:
        await callback.message.answer("💳 Нет оплат, ожидающих подтверждения.", reply_markup=keyboards.admin_back())
        return
    from .funnel import payment_request_text
    for r in requests:
        user = await db.get_user(r["user_id"]) or {"user_id": r["user_id"]}
        await callback.message.answer(payment_request_text(r["id"], user, config, r["created_at"]),
                                      reply_markup=keyboards.admin_payment(r["id"]))


async def _mark_handled(callback: CallbackQuery, note: str) -> None:
    msg = callback.message
    try:
        if msg.caption is not None:
            await msg.edit_caption(caption=f"{msg.html_text}\n\n{note}", reply_markup=None)
        else:
            await msg.edit_text(f"{msg.html_text}\n\n{note}", reply_markup=None)
    except Exception:
        await msg.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("adm:pay_ok:"))
async def payment_ok(callback: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    request_id = int(callback.data.split(":")[2])
    request = await db.get_payment_request(request_id)
    if not request or not await db.set_payment_request_status(request_id, "approved"):
        await callback.answer("Эта оплата уже обработана", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    await callback.answer("Доступ выдан ✅")
    await grant_access(bot, db, config, request["user_id"], via=f"СБП, чек #{request_id}")
    await _mark_handled(callback, "✅ <b>Подтверждено, доступ выдан</b>")


@router.callback_query(F.data.startswith("adm:pay_no:"))
async def payment_no(callback: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    request_id = int(callback.data.split(":")[2])
    request = await db.get_payment_request(request_id)
    if not request or not await db.set_payment_request_status(request_id, "rejected"):
        await callback.answer("Эта оплата уже обработана", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    await callback.answer("Отклонено")
    try:
        await bot.send_message(request["user_id"], content.t("payment_rejected", manager=manager_suffix(config)),
                               reply_markup=keyboards.manager(config))
    except Exception:
        log.exception("Не удалось уведомить об отклонении %s", request["user_id"])
    await _mark_handled(callback, "❌ <b>Отклонено</b>")


# ------------------------------------------------------------------ рассылка

AUDIENCE_TITLES = {"all": "всем", "unpaid": "тем, кто не купил", "paid": "тем, кто купил"}


@router.callback_query(F.data == "adm:bc")
async def broadcast_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("📣 <b>Рассылка</b>\n\nКому отправить?",
                                     reply_markup=keyboards.admin_broadcast_audience())


@router.callback_query(F.data.startswith("adm:bc_to:"))
async def broadcast_audience(callback: CallbackQuery, state: FSMContext) -> None:
    audience = callback.data.split(":")[2]
    await state.set_state(AdminForm.broadcast)
    await state.update_data(audience=audience)
    await callback.answer()
    await callback.message.edit_text(
        f"📣 Рассылка {AUDIENCE_TITLES[audience]}.\n\n"
        "Пришли сообщение, которое нужно разослать — текст, фото, видео, кружок, что угодно. "
        "Оно уйдёт копией, без пометки «переслано».",
        reply_markup=keyboards.admin_back(),
    )


@router.message(StateFilter(AdminForm.broadcast))
async def broadcast_message(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await state.update_data(chat_id=message.chat.id, message_id=message.message_id)
    count = len(await db.user_ids(data["audience"]))
    await message.answer(
        f"☝️ Так будет выглядеть сообщение.\n\nОтправить {AUDIENCE_TITLES[data['audience']]} — "
        f"<b>{count}</b> чел.?",
        reply_markup=keyboards.admin_broadcast_confirm(),
    )


async def run_broadcast(bot: Bot, db: Database, admin_id: int, audience: str, chat_id: int,
                        message_id: int) -> None:
    sent = failed = 0
    for uid in await db.user_ids(audience):
        try:
            await bot.copy_message(uid, chat_id, message_id)
            sent += 1
        except Exception:
            failed += 1
            await db.update_user(uid, blocked=1)
        await asyncio.sleep(0.05)  # лимит Telegram ~30 сообщений в секунду
    await bot.send_message(admin_id, f"✅ Рассылка завершена.\nДоставлено: {sent}\nНе доставлено: {failed}")


@router.callback_query(F.data == "adm:bc_go")
async def broadcast_go(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    data = await state.get_data()
    await state.clear()
    if "message_id" not in data:
        await callback.answer("Сначала пришли сообщение", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text("🚀 Рассылка запущена, пришлю отчёт, когда закончу.")
    asyncio.create_task(run_broadcast(
        bot, db, callback.from_user.id, data["audience"], data["chat_id"], data["message_id"]
    ))


# ------------------------------------------------------------- выдать доступ

@router.callback_query(F.data == "adm:grant")
async def grant_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminForm.grant)
    await callback.answer()
    await callback.message.edit_text(
        "🎁 <b>Выдать доступ вручную</b>\n\n"
        "Пришли ID пользователя, его @username или перешли любое его сообщение.\n"
        "<i>Человек должен хотя бы раз запустить бота.</i>",
        reply_markup=keyboards.admin_back(),
    )


@router.message(StateFilter(AdminForm.grant))
async def grant_input(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    user = None
    if message.forward_from:
        user = await db.get_user(message.forward_from.id)
    elif message.text:
        value = message.text.strip()
        if value.lstrip("-").isdigit():
            user = await db.get_user(int(value))
        else:
            user = await db.find_user_by_username(value)
    if not user:
        await message.answer(
            "Не нашёл такого пользователя в боте 🤔 Проверь ID/username — "
            "или попроси человека нажать /start и попробуй снова.",
            reply_markup=keyboards.admin_back(),
        )
        return
    await state.clear()
    if await grant_access(bot, db, config, user["user_id"], via="выдан вручную"):
        await message.answer(f"✅ Доступ выдан: {user_link(user)}", reply_markup=keyboards.admin_back())
    else:
        await message.answer(f"У {user_link(user)} уже есть доступ.", reply_markup=keyboards.admin_back())


# ------------------------------------------------------------- видео воронки

async def media_screen(db: Database) -> tuple[str, object]:
    demo = await db.get_setting("demo_media")
    videos = await db.get_setting("followup_media", [])
    text = (
        "🎬 <b>Видео в воронке</b>\n\n"
        f"• Видео-показ канала (после кнопки «Показать»): {'✅ есть' if demo else '❌ нет'}\n"
        f"• Видео к напоминаниям: {len(videos)} шт.\n\n"
        "<i>Напоминания тем, кто не купил, приходят видео + текст + кнопка «Получить доступ». "
        "Если видео несколько — бот чередует их по очереди. Пока видео нет — только текст.</i>"
    )
    return text, keyboards.admin_media(bool(demo), len(videos))


@router.callback_query(F.data == "adm:media")
async def media_menu(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await callback.answer()
    text, markup = await media_screen(db)
    await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "adm:m_demo")
async def media_demo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminForm.demo)
    await callback.answer()
    await callback.message.answer("Пришли видео, где ты показываешь канал 🎬", reply_markup=keyboards.admin_back())


@router.message(StateFilter(AdminForm.demo))
async def media_upload(message: Message, state: FSMContext, db: Database) -> None:
    media = extract_media(message)
    if not media:
        await message.answer("Это не видео/фото 🤔 Пришли файл или нажми «В админку».")
        return
    await db.set_setting("demo_media", media)
    await state.clear()
    await message.answer("✅ Видео-показ сохранён.")
    text, markup = await media_screen(db)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "adm:m_preview")
async def media_preview(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    await callback.answer()
    uid = callback.from_user.id
    demo = await db.get_setting("demo_media")
    if demo:
        await send_media(bot, uid, demo, caption=content.t("show"), reply_markup=keyboards.after_show())


# ------------------------------------------------------- каналы и группа

@router.callback_query(F.data == "adm:chats")
async def chats_screen(callback: CallbackQuery, db: Database, config: Config) -> None:
    await callback.answer()
    private = await db.get_setting("private_chat")
    channel = await db.get_setting("channel")
    private_line = (f"✅ {private['title']}" if private
                    else ("✅ задана в .env" if await private_chat_id(db, config) else "❌ не выбрана"))
    url = await channel_url(db, config)
    channel_line = (f"✅ {escape(channel['title'])}" if channel and channel.get("url")
                    else (f"✅ {url}" if url else "❌ не показывается"))
    await callback.message.answer(
        "🔗 <b>Каналы и группа</b>\n\n"
        f"🔒 Закрытая группа с разборами: {private_line}\n"
        f"📢 Канал в приветствии (необязательная подписка): {channel_line}\n\n"
        "<i>Чтобы выбрать: добавь бота <b>администратором</b> в нужный канал или группу "
        "(с правом «Пригласительные ссылки») — он сразу пришлёт сюда вопрос, что это за чат.</i>",
        reply_markup=keyboards.admin_chats(bool(url)),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("setchat:"))
async def set_chat(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    _, role, chat_id_s = callback.data.split(":")
    chat_id = int(chat_id_s)

    if role == "none":
        await callback.answer("Ок")
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    if role == "channel_off":
        await db.set_setting("channel", {"title": "—", "url": ""})
        await callback.answer("Канал убран из приветствия")
        await callback.message.edit_reply_markup(reply_markup=None)
        return

    try:
        chat = await bot.get_chat(chat_id)
        me = await bot.get_chat_member(chat_id, bot.id)
    except Exception:
        await callback.answer("Не могу получить доступ к чату — проверь, что я там админ", show_alert=True)
        return
    if not getattr(me, "can_invite_users", False):
        await callback.answer(
            "Дай мне право «Пригласительные ссылки» (Add members / Invite users) и нажми ещё раз", show_alert=True
        )
        return

    title = chat.title or str(chat_id)
    if role == "private":
        await db.set_setting("private_chat", {"id": chat_id, "title": title})
        note = (f"✅ «{escape(title)}» — закрытая группа с разборами.\n"
                "После оплаты я буду выдавать в неё одноразовые ссылки.")
    else:
        url = f"https://t.me/{chat.username}" if chat.username else (await bot.export_chat_invite_link(chat_id))
        await db.set_setting("channel", {"id": chat_id, "title": title, "url": url})
        note = f"✅ «{escape(title)}» — наш канал.\nСсылка на него будет в приветствии (подписка не обязательна)."
    await callback.answer("Сохранено")
    await callback.message.edit_text(note, reply_markup=keyboards.admin_back())


# ------------------------------------------------------------------ отзывы

async def reviews_screen(db: Database) -> tuple[str, object]:
    reviews, total = await db.reviews(limit=30)
    lines = [f"⭐ <b>Отзывы</b> — {total}\n"]
    for r in reviews:
        short = r["text"] if len(r["text"]) <= 80 else r["text"][:80] + "…"
        lines.append(f"#{r['id']} <b>{escape(r['author'])}</b>: {escape(short)}")
    if not reviews:
        lines.append("Пока нет ни одного отзыва.")
    if total > len(reviews):
        lines.append(f"\n<i>Показаны последние {len(reviews)}.</i>")
    return "\n".join(lines), keyboards.admin_reviews(reviews)


@router.callback_query(F.data == "adm:reviews")
async def reviews_menu(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await callback.answer()
    text, markup = await reviews_screen(db)
    await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "adm:rev_add")
async def review_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminForm.review_text)
    await callback.answer()
    await callback.message.answer("✍️ Пришли текст отзыва:", reply_markup=keyboards.admin_back())


@router.message(StateFilter(AdminForm.review_text), F.text)
async def review_text(message: Message, state: FSMContext) -> None:
    await state.update_data(review_text=message.text.strip()[:1500])
    await state.set_state(AdminForm.review_author)
    await message.answer(
        "👤 Кто автор? Напиши имя или @username — например: <code>Анна</code> или <code>@anna_drive</code>",
        reply_markup=keyboards.admin_back(),
    )


@router.message(StateFilter(AdminForm.review_author), F.text)
async def review_author(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await state.clear()
    review_id = await db.add_review(message.text.strip()[:64], data["review_text"])
    await message.answer(f"✅ Отзыв #{review_id} добавлен — он уже виден в разделе «Отзывы».")
    text, markup = await reviews_screen(db)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("adm:rev_del:"))
async def review_delete(callback: CallbackQuery, db: Database) -> None:
    review_id = int(callback.data.split(":")[2])
    deleted = await db.delete_review(review_id)
    await callback.answer(f"Отзыв #{review_id} удалён" if deleted else "Уже удалён")
    text, markup = await reviews_screen(db)
    await callback.message.edit_text(text, reply_markup=markup)


# ------------------------------------------------------------------ очистка

@router.callback_query(F.data == "adm:clean")
async def clean_menu(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await callback.answer()
    _, total = await db.list_users("all", 1, 0)
    await callback.message.answer(
        "🧹 <b>Очистка</b>\n\n"
        f"Сейчас в базе пользователей: <b>{total}</b>.\n\n"
        "• <b>Очистить всё</b> — удалит всех пользователей, всю статистику и заявки на оплату.\n"
        "• <b>Удалить одного</b> — удалит одного пользователя и его статистику.\n\n"
        "<i>Отзывы, видео, каналы и настройки не удаляются.</i>",
        reply_markup=keyboards.admin_clean(),
    )


@router.callback_query(F.data == "adm:clean_all")
async def clean_all_ask(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    rows, total = await db.list_users("paid", 1, 0)
    warn = (f"\n\n⚠️ Среди них <b>{total}</b> купивших — после очистки бот перестанет считать их "
            "покупателями (в группе они останутся)." if total else "")
    await callback.message.edit_text(
        "❗ Точно удалить <b>всех</b> пользователей и всю статистику? Отменить будет нельзя." + warn,
        reply_markup=keyboards.admin_confirm("adm:clean_all_yes"),
    )


@router.callback_query(F.data == "adm:clean_all_yes")
async def clean_all(callback: CallbackQuery, db: Database) -> None:
    n = await db.wipe_users_and_stats()
    await callback.answer("Очищено")
    await callback.message.edit_text(f"✅ Готово: удалено пользователей — {n}, статистика обнулена.",
                                     reply_markup=keyboards.admin_back())


@router.callback_query(F.data == "adm:clean_one")
async def clean_one_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminForm.clean_one)
    await callback.answer()
    await callback.message.edit_text(
        "👤 Пришли ID пользователя, его @username или перешли любое его сообщение.",
        reply_markup=keyboards.admin_back(),
    )


@router.message(StateFilter(AdminForm.clean_one))
async def clean_one_input(message: Message, state: FSMContext, db: Database) -> None:
    user = None
    if message.forward_from:
        user = await db.get_user(message.forward_from.id)
    elif message.text:
        user = await db.find_user(message.text)
    if not user:
        await message.answer("Не нашёл такого пользователя 🤔 Проверь ID или @username.",
                             reply_markup=keyboards.admin_back())
        return
    await state.clear()
    status = "💰 купил" if user["paid"] else "не покупал"
    await message.answer(
        f"Удалить {user_link(user)} ({status}) и всю его статистику?",
        reply_markup=keyboards.admin_confirm(f"adm:clean_one_yes:{user['user_id']}"),
    )


@router.callback_query(F.data.startswith("adm:clean_one_yes:"))
async def clean_one(callback: CallbackQuery, db: Database) -> None:
    user_id = int(callback.data.split(":")[2])
    await db.delete_user(user_id)
    await callback.answer("Удалено")
    await callback.message.edit_text(f"✅ Пользователь <code>{user_id}</code> удалён.",
                                     reply_markup=keyboards.admin_back())


# ------------------------------------------------------ видео к напоминаниям

@router.callback_query(F.data == "adm:m_fu")
async def followup_video_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminForm.followup_video)
    await callback.answer()
    await callback.message.answer(
        "🎬 Пришли видео-разбор для напоминаний. Можно несколько подряд — бот будет чередовать их.",
        reply_markup=keyboards.admin_back(),
    )


@router.message(StateFilter(AdminForm.followup_video))
async def followup_video_upload(message: Message, db: Database) -> None:
    media = extract_media(message)
    if not media:
        await message.answer("Это не видео 🤔 Пришли видео или нажми «В админку».", reply_markup=keyboards.admin_back())
        return
    videos = await db.get_setting("followup_media", [])
    videos.append(media)
    await db.set_setting("followup_media", videos)
    await message.answer(f"✅ Добавлено (всего: {len(videos)}). Пришли ещё или вернись в админку.",
                         reply_markup=keyboards.admin_back())


@router.callback_query(F.data == "adm:m_fu_clear")
async def followup_video_clear(callback: CallbackQuery, db: Database) -> None:
    await db.set_setting("followup_media", [])
    await callback.answer("Удалено")
    text, markup = await media_screen(db)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "adm:m_fu_preview")
async def followup_video_preview(callback: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    from ..followups import render
    await callback.answer()
    videos = await db.get_setting("followup_media", [])
    if not videos:
        return
    text, _ = await render(db, content.FOLLOWUP_ORDER[0], config)
    await send_media(bot, callback.from_user.id, videos[0], caption=text, reply_markup=keyboards.get_access())
