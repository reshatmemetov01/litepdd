"""Админка: «✏️ Тексты», «🎨 Кнопки: цвет и эмодзи», «⚙️ Настройки».

Всё хранится в таблице settings (см. runtime.py) и применяется сразу, без перезапуска.
"""
import logging
import re
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import content, emoji_fallback, keyboards, runtime
from ..config import EDITABLE_FIELDS, Config
from ..db import Database
from ..services import visible_length
from .admin import IsAdmin

log = logging.getLogger(__name__)
router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class ContentForm(StatesGroup):
    text = State()
    icon = State()
    setting = State()


async def _reload(db: Database, base: Config) -> Config:
    """Перечитать настройки после изменения — чтобы экран сразу показал новое значение."""
    _, cfg = await runtime.load(db, base)
    return cfg


async def _show(callback: CallbackQuery, text: str, markup) -> None:
    """Обновить экран админки на месте (или прислать новый, если старый не редактируется)."""
    try:
        await callback.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup, disable_web_page_preview=True)


# =================================================================== тексты

TEXTS_HELP = (
    "✏️ <b>Тексты</b>\n\n"
    "Выбери раздел, потом текст. Можно посмотреть текущий, заменить своим или вернуть стандартный.\n\n"
    "• Форматирование (жирный, курсив, ссылки, цитаты) переносится как есть — просто оформи "
    "сообщение в Telegram и отправь.\n"
    "• Премиум-эмодзи в тексте тоже сохраняются (если у владельца бота есть Telegram Premium).\n"
    "• Слова в фигурных скобках, например <code>{price}</code>, — плейсхолдеры: бот подставит "
    "туда цену, имя и т.п. Их нужно оставить как есть.\n\n"
    "<i>✏️ — текст изменён и отличается от стандартного.</i>"
)


def _placeholders_help(key: str) -> str:
    d = content.TEXTS[key]
    items = {**(d.placeholders or {}), **content.GLOBAL_PLACEHOLDERS}
    lines = [f"<code>{{{name}}}</code> — {escape(desc)}" for name, desc in items.items()]
    return "\n".join(lines)


async def _texts_changed(db: Database) -> dict[str, str]:
    return await db.get_setting("texts", {}) or {}


@router.callback_query(F.data == "txt:menu")
async def texts_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await _show(callback, TEXTS_HELP, keyboards.admin_text_categories())


@router.callback_query(F.data.startswith("txt:c:"))
async def texts_category(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    category = callback.data.split(":", 2)[2]
    if category not in content.TEXT_CATEGORIES:
        await callback.answer()
        return
    await callback.answer()
    changed = set(await _texts_changed(db))
    await _show(callback, f"✏️ <b>{content.TEXT_CATEGORIES[category]}</b>\n\nКакой текст поменять?",
                keyboards.admin_text_list(category, changed))


async def send_text_screen(bot: Bot, chat_id: int, db: Database, key: str) -> None:
    d = content.TEXTS[key]
    changed = key in await _texts_changed(db)
    tpl = content.template(key)
    # Сам текст — отдельным сообщением, как его увидит клиент (плейсхолдеры видны в скобках).
    # Его удобно скопировать, поправить и прислать обратно.
    try:
        await bot.send_message(chat_id, tpl, parse_mode=None if d.plain else "HTML",
                               disable_web_page_preview=True)
    except TelegramBadRequest:
        await bot.send_message(chat_id, tpl, parse_mode=None)
    status = "✏️ изменён в админке" if changed else "стандартный"
    await bot.send_message(
        chat_id,
        f"☝️ <b>{escape(d.title)}</b> — {status}\n\n<b>Плейсхолдеры:</b>\n{_placeholders_help(key)}",
        reply_markup=keyboards.admin_text(key, changed),
    )


@router.callback_query(F.data.startswith("txt:v:"))
async def text_view(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database) -> None:
    await state.clear()
    key = callback.data.split(":", 2)[2]
    await callback.answer()
    if key in content.TEXTS:
        await send_text_screen(bot, callback.from_user.id, db, key)


@router.callback_query(F.data.startswith("txt:e:"))
async def text_edit(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in content.TEXTS:
        await callback.answer()
        return
    d = content.TEXTS[key]
    await state.set_state(ContentForm.text)
    await state.update_data(text_key=key)
    await callback.answer()
    format_note = ("Пришли новый текст обычным сообщением (форматирование здесь не поддерживается, "
                   f"до {d.limit} символов)." if d.plain else
                   "Пришли новый текст одним сообщением. Жирный, курсив, ссылки, цитаты и премиум-эмодзи "
                   "сохранятся.")
    await callback.message.answer(
        f"✍️ <b>{escape(d.title)}</b>\n\n{format_note}\n\n<b>Плейсхолдеры:</b>\n{_placeholders_help(key)}",
        reply_markup=keyboards.inline(keyboards.B(text="✖️ Отмена", callback_data=f"txt:v:{key}")),
    )


@router.message(StateFilter(ContentForm.text))
async def text_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    key = data.get("text_key")
    if key not in content.TEXTS:
        await state.clear()
        return
    d = content.TEXTS[key]
    if not message.text:
        await message.answer("Пришли текст сообщением (не фото и не файл) — или нажми «Отмена».")
        return
    tpl = message.text.strip() if d.plain else message.html_text.strip()

    errors, rendered = content.check_template(key, tpl)
    if errors:
        await message.answer("⚠️ <b>Текст не сохранён</b>\n\n" + "\n".join(f"• {escape(e)}" for e in errors)
                             + "\n\nИсправь и пришли ещё раз.")
        return

    # Пробная отправка: Telegram сам проверит разметку. Если она сломана — не сохраняем
    failures_before = emoji_fallback.last_failure["ts"]
    try:
        await bot.send_message(message.chat.id, rendered, parse_mode=None if d.plain else "HTML",
                               disable_web_page_preview=True)
    except TelegramBadRequest as e:
        await message.answer(f"⚠️ <b>Telegram не принял текст:</b> <code>{escape(e.message)}</code>\n\n"
                             "Проверь форматирование и пришли ещё раз.")
        return

    notes = []
    if emoji_fallback.last_failure["ts"] != failures_before:
        notes.append("⚠️ Премиум-эмодзи Telegram не пропустил — клиенты увидят обычные эмодзи. "
                     "Они работают, только если у владельца бота есть Telegram Premium "
                     "(или у бота купленный username на Fragment).")
    if d.caption and visible_length(rendered) > content.CAPTION_LIMIT:
        notes.append(f"ℹ️ Текст длиннее {content.CAPTION_LIMIT} символов: если к нему прикреплено видео, "
                     "бот пришлёт видео и текст двумя сообщениями.")
    await state.update_data(text_value=tpl)
    await message.answer(
        "☝️ Так текст будет выглядеть (вместо плейсхолдеров — пример).\n\n"
        + ("\n\n".join(notes) + "\n\n" if notes else "") + "Сохранить?",
        reply_markup=keyboards.admin_text_confirm(key),
    )


@router.callback_query(F.data.startswith("txt:s:"))
async def text_save(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, config: Config,
        base_config: Config) -> None:
    key = callback.data.split(":", 2)[2]
    data = await state.get_data()
    if data.get("text_key") != key or "text_value" not in data:
        await callback.answer("Сначала пришли новый текст", show_alert=True)
        return
    await state.clear()
    texts = await _texts_changed(db)
    texts[key] = data["text_value"]
    await db.set_setting("texts", texts)
    await _reload(db, base_config)
    await _apply_profile_text(bot, key)
    await callback.answer("Сохранено ✅")
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_text_screen(bot, callback.from_user.id, db, key)


@router.callback_query(F.data.startswith("txt:r:"))
async def text_reset(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, config: Config,
        base_config: Config) -> None:
    await state.clear()
    key = callback.data.split(":", 2)[2]
    texts = await _texts_changed(db)
    if texts.pop(key, None) is not None:
        await db.set_setting("texts", texts)
    await _reload(db, base_config)
    await _apply_profile_text(bot, key)
    await callback.answer("Вернул стандартный текст")
    await send_text_screen(bot, callback.from_user.id, db, key)


async def _apply_profile_text(bot: Bot, key: str) -> None:
    """Описание бота в профиле Telegram обновляем сразу."""
    try:
        if key == "bot_description":
            await bot.set_my_description(content.t(key))
        elif key == "bot_short_description":
            await bot.set_my_short_description(content.t(key))
    except Exception:
        log.exception("Не удалось обновить описание бота")


# =================================================================== кнопки

BUTTONS_HELP = (
    "🎨 <b>Кнопки: цвет и эмодзи</b>\n\n"
    "<b>Цвет</b> задаётся для группы кнопок. Telegram разрешает только 3 цвета: "
    "🔵 синий, 🟢 зелёный, 🔴 красный — или обычный (как в приложении). Свой оттенок выбрать нельзя. "
    "Цвет видят пользователи свежих версий Telegram, в старых кнопка будет обычной.\n\n"
    "<b>Премиум-эмодзи</b> ставится перед текстом конкретной кнопки (вместо обычного эмодзи). "
    "Работает, только если у владельца бота есть <b>Telegram Premium</b> или у бота есть "
    "купленный на Fragment username. Иначе бот покажет кнопку без него."
)


async def _buttons_text() -> str:
    text = BUTTONS_HELP
    if emoji_fallback.last_failure["ts"]:
        text += ("\n\n⚠️ <i>Недавно Telegram не пропустил премиум-эмодзи — бот отправил сообщение без них. "
                 "Проверь Premium у владельца бота.</i>")
    return text


@router.callback_query(F.data == "btn:menu")
async def buttons_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await _show(callback, await _buttons_text(), keyboards.admin_buttons())


@router.callback_query(F.data.startswith("btn:g:"))
async def button_group(callback: CallbackQuery) -> None:
    group = callback.data.split(":", 2)[2]
    await callback.answer()
    if group in keyboards.BUTTON_GROUPS:
        await _show(callback, f"🎨 Цвет: <b>{escape(keyboards.BUTTON_GROUPS[group])}</b>",
                    keyboards.admin_button_group(group))


@router.callback_query(F.data.startswith("btn:s:"))
async def button_style(callback: CallbackQuery, db: Database, config: Config,
        base_config: Config) -> None:
    _, _, group, value = callback.data.split(":")
    if group not in keyboards.BUTTON_GROUPS or (value != "none" and value not in keyboards.STYLES):
        await callback.answer()
        return
    styles = await db.get_setting("button_styles", {}) or {}
    if value == "none":
        styles.pop(group, None)
    else:
        styles[group] = value
    await db.set_setting("button_styles", styles)
    await _reload(db, base_config)
    await callback.answer("Сохранено ✅")
    await _show(callback, await _buttons_text(), keyboards.admin_buttons())


@router.callback_query(F.data == "btn:icons")
async def button_icons(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await _show(callback, "✨ <b>Премиум-эмодзи на кнопках</b>\n\nВыбери кнопку. ✨ — эмодзи уже задано.",
                keyboards.admin_icons())


@router.callback_query(F.data.startswith("btn:i:"))
async def button_icon(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in keyboards.BUTTONS:
        await callback.answer()
        return
    await state.set_state(ContentForm.icon)
    await state.update_data(icon_key=key)
    await callback.answer()
    current = runtime.current().icons.get(key)
    await callback.message.answer(
        f"✨ Кнопка «{escape(keyboards.BUTTONS[key][0])}»\n\n"
        + (f"Сейчас: <code>{current}</code>\n\n" if current else "")
        + "Пришли <b>одно премиум-эмодзи</b> (для этого нужен Telegram Premium у тебя) "
        "или его ID числом. Оно встанет перед текстом кнопки вместо обычного эмодзи.",
        reply_markup=keyboards.admin_icon(key, bool(current)),
    )


def extract_custom_emoji_id(message: Message) -> str | None:
    for entity in message.entities or []:
        if entity.type == "custom_emoji" and entity.custom_emoji_id:
            return entity.custom_emoji_id
    text = (message.text or "").strip()
    return text if re.fullmatch(r"\d{5,25}", text) else None


@router.message(StateFilter(ContentForm.icon))
async def button_icon_input(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config,
        base_config: Config) -> None:
    key = (await state.get_data()).get("icon_key")
    if key not in keyboards.BUTTONS:
        await state.clear()
        return
    emoji_id = extract_custom_emoji_id(message)
    if not emoji_id:
        await message.answer("Не вижу премиум-эмодзи 🤔 Пришли именно премиум (анимированное) эмодзи "
                             "из набора или его ID числом.", reply_markup=keyboards.admin_icon(key, False))
        return

    # Проверка: пробуем отправить кнопку с этим эмодзи. Если Telegram не пропустит — не сохраняем
    text, group = keyboards.BUTTONS[key]
    sample = keyboards.inline(keyboards.B(
        text=keyboards.strip_leading_emoji(text), icon_custom_emoji_id=emoji_id,
        style=runtime.current().styles.get(group), callback_data="btn:icons",
    ))
    failures_before = emoji_fallback.last_failure["ts"]
    try:
        await bot.send_message(message.chat.id, "Пример кнопки 👇", reply_markup=sample)
    except TelegramBadRequest as e:
        await message.answer(f"⚠️ Telegram не принял это эмодзи: <code>{escape(e.message)}</code>")
        return
    if emoji_fallback.last_failure["ts"] != failures_before:
        await state.clear()
        await message.answer(
            "⚠️ <b>Не сохранено.</b> Telegram не разрешает этому боту премиум-эмодзи на кнопках.\n\n"
            "Нужно одно из двух: у владельца бота (кто создал его в @BotFather) есть Telegram Premium, "
            "или у бота есть дополнительный username, купленный на Fragment.",
            reply_markup=keyboards.admin_icon(key, False),
        )
        return

    icons = await db.get_setting("button_icons", {}) or {}
    icons[key] = emoji_id
    await db.set_setting("button_icons", icons)
    await _reload(db, base_config)
    await state.clear()
    await message.answer("✅ Сохранено. ☝️ Так кнопка выглядит у клиентов.", reply_markup=keyboards.admin_icons())


@router.callback_query(F.data.startswith("btn:x:"))
async def button_icon_remove(callback: CallbackQuery, state: FSMContext, db: Database, config: Config,
        base_config: Config) -> None:
    await state.clear()
    key = callback.data.split(":", 2)[2]
    icons = await db.get_setting("button_icons", {}) or {}
    icons.pop(key, None)
    await db.set_setting("button_icons", icons)
    await _reload(db, base_config)
    await callback.answer("Убрано")
    await _show(callback, "✨ <b>Премиум-эмодзи на кнопках</b>\n\nВыбери кнопку. ✨ — эмодзи уже задано.",
                keyboards.admin_icons())


# ================================================================ настройки

SETTING_HINTS = {
    "bot_name": "Название бота — подставляется в тексты вместо <code>{bot_name}</code>.",
    "author_name": "Имя автора — в текстах «о себе» и «Информация» (<code>{author}</code>).",
    "manager_username": "Username для кнопки «💬 Поддержка» и текстов, без @. Например: <code>support_bot</code>",
    "agreement_url": "Ссылка на пользовательское соглашение (https://...). Пусто — ссылку не показываем.",
    "privacy_url": "Ссылка на политику конфиденциальности (https://...). Пусто — ссылку не показываем.",
    "payment_url": "Многоразовая ссылка на оплату (Paydex). Пусто — кнопки «Оплатить» не будет.",
}


async def settings_text(db: Database, config: Config) -> str:
    profile = await db.get_setting("profile", {}) or {}
    lines = ["⚙️ <b>Настройки</b>\n"]
    for field, title in keyboards.SETTING_TITLES.items():
        value = getattr(config, field)
        shown = escape(value) if value else "— пусто —"
        source = " <i>(админка)</i>" if field in profile else " <i>(.env)</i>"
        lines.append(f"<b>{title}:</b> {shown}{source}")
    if not config.author_name:
        lines.append("\n⚠️ Имя автора не задано — в текстах «о себе» и «Информация» будет пусто.")
    lines.append("\n<i>Значение из админки важнее, чем из .env. Токен, админы и цена — только в .env.</i>")
    return "\n".join(lines)


@router.callback_query(F.data == "set:menu")
async def settings_menu(callback: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await state.clear()
    await callback.answer()
    await _show(callback, await settings_text(db, config), keyboards.admin_settings())


@router.callback_query(F.data.startswith("set:e:"))
async def setting_edit(callback: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    field = callback.data.split(":", 2)[2]
    if field not in EDITABLE_FIELDS:
        await callback.answer()
        return
    await state.set_state(ContentForm.setting)
    await state.update_data(setting_field=field)
    await callback.answer()
    profile = await db.get_setting("profile", {}) or {}
    value = getattr(config, field)
    await callback.message.answer(
        f"✍️ <b>{keyboards.SETTING_TITLES[field]}</b>\n"
        f"Сейчас: {escape(value) if value else '— пусто —'}\n\n{SETTING_HINTS[field]}\n\nПришли новое значение:",
        reply_markup=keyboards.admin_setting(field, field in profile),
        disable_web_page_preview=True,
    )


def validate_setting(field: str, value: str) -> tuple[str | None, str | None]:
    """(значение, ошибка)"""
    value = value.strip()
    if field == "manager_username":
        value = value.removeprefix("https://t.me/").lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", value):
            return None, "Username — это 4–32 латинских буквы, цифры или _, например support_bot."
        return value, None
    if field.endswith("_url"):
        if not re.fullmatch(r"(https?|tg)://\S+", value):
            return None, "Нужна ссылка целиком, начиная с https://"
        return value, None
    if not value or len(value) > 64:
        return None, "Нужен текст до 64 символов."
    if "{" in value or "}" in value:
        return None, "Фигурные скобки здесь нельзя."
    return value, None


async def _save_profile(db: Database, field: str, value: str | None) -> None:
    profile = await db.get_setting("profile", {}) or {}
    if value is None:
        profile.pop(field, None)
    else:
        profile[field] = value
    await db.set_setting("profile", profile)


@router.message(StateFilter(ContentForm.setting))
async def setting_input(message: Message, state: FSMContext, db: Database, config: Config,
        base_config: Config) -> None:
    field = (await state.get_data()).get("setting_field")
    if field not in EDITABLE_FIELDS:
        await state.clear()
        return
    value, error = validate_setting(field, message.text or "")
    if error:
        await message.answer(f"⚠️ {error}\n\nПришли ещё раз или нажми «Назад».",
                             reply_markup=keyboards.admin_setting(field, False))
        return
    await _save_profile(db, field, value)
    await state.clear()
    cfg = await _reload(db, base_config)
    await message.answer("✅ Сохранено.\n\n" + await settings_text(db, cfg),
                         reply_markup=keyboards.admin_settings(), disable_web_page_preview=True)


@router.callback_query(F.data.startswith("set:clear:") | F.data.startswith("set:env:"))
async def setting_reset(callback: CallbackQuery, state: FSMContext, db: Database, config: Config,
        base_config: Config) -> None:
    _, action, field = callback.data.split(":", 2)
    if field not in EDITABLE_FIELDS:
        await callback.answer()
        return
    await state.clear()
    await _save_profile(db, field, "" if action == "clear" else None)
    cfg = await _reload(db, base_config)
    await callback.answer("Сохранено ✅")
    await _show(callback, await settings_text(db, cfg), keyboards.admin_settings())
