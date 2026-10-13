from aiogram.types import (
    InlineKeyboardButton as B,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from . import content, runtime
from .config import Config

# ------------------------------------------------------- цвета и эмодзи кнопок
#
# Bot API 9.4+: у кнопки можно задать style (цвет) и icon_custom_emoji_id (премиум-эмодзи
# перед текстом). Цветов всего три — их задаёт Telegram, свой цвет (например, #FF8800) нельзя.
# Цвет настраивается для группы кнопок, эмодзи — для отдельной кнопки (админка → «🎨 Кнопки»).

STYLES = {
    "primary": "🔵 Синий",
    "success": "🟢 Зелёный",
    "danger": "🔴 Красный",
}

BUTTON_GROUPS = {
    "cta": "Главные (Продолжить, Показать, Получить доступ)",
    "pay": "Оплата (Оплатить по СБП, Оплатить, Я оплатил)",
    "secondary": "Остальные (канал, промокод, поддержка, тренажёр)",
    "back": "«Назад»",
    "menu": "Нижнее меню",
}

# ключ кнопки → (текст, группа). Премиум-эмодзи можно поставить на любую из них.
BUTTONS: dict[str, tuple[str, str]] = {
    "continue": ("➡️ Продолжить", "cta"),
    "show": ("⭐ ПОКАЗАТЬ", "cta"),
    "get_access": ("👑 ПОЛУЧИТЬ ДОСТУП", "cta"),
    "followup_show": ("⭐ Посмотреть, что внутри", "cta"),
    "pay_sbp": ("💳 Оплатить по СБП", "pay"),
    "pay_link": ("💳 Оплатить", "pay"),
    "paid": ("✅ Я оплатил", "pay"),
    "subscribe": ("📢 Подписаться на канал", "secondary"),
    "promo": ("🎟 Ввести промокод", "secondary"),
    "support": ("💬 Поддержка", "secondary"),
    "write_support": ("💬 Написать в поддержку", "secondary"),
    "tickets": ("🎫 По билетам", "secondary"),
    "exam": ("🎯 Экзамен", "secondary"),
    "followup_exam": ("📝 Пройти экзамен", "secondary"),
    "exam_retry": ("🔄 Пройти ещё раз", "secondary"),
    "ticket_retry": ("🔄 Пройти билет ещё раз", "secondary"),
    "ticket_other": ("🎫 Другой билет", "secondary"),
    "agreement": ("📁 Пользовательское соглашение", "secondary"),
    "privacy": ("📁 Политика конфиденциальности", "secondary"),
    "back": (content.BACK, "back"),
    "offer_back": ("⬅️ Назад", "back"),
}


def strip_leading_emoji(text: str) -> str:
    """«👑 ПОЛУЧИТЬ ДОСТУП» → «ПОЛУЧИТЬ ДОСТУП»: премиум-эмодзи встаёт вместо обычного."""
    first, _, rest = text.partition(" ")
    if rest and not any(ch.isalnum() for ch in first):
        return rest
    return text


def btn(key: str, **kwargs) -> B:
    """Кнопка из реестра BUTTONS с цветом группы и премиум-эмодзи из админки."""
    text, group = BUTTONS[key]
    rt = runtime.current()
    icon = rt.icons.get(key)
    return B(
        text=strip_leading_emoji(text) if icon else text,
        style=rt.styles.get(group),
        icon_custom_emoji_id=icon,
        **kwargs,
    )


def menu_button(text: str) -> KeyboardButton:
    # У нижнего меню только цвет: текст кнопки должен совпадать с подписью, по ней бот узнаёт раздел
    return KeyboardButton(text=text, style=runtime.current().styles.get("menu"))


def inline(*rows: list[B] | B) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r if isinstance(r, list) else [r] for r in rows])


def back_button() -> B:
    return btn("back", callback_data="menu:back")


def with_back(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup:
    """Добавляет «Назад» под сообщение раздела из нижнего меню."""
    rows = list(markup.inline_keyboard) if markup else []
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [back_button()]])


def reviews_nav(page: int, pages: int) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(B(text="◀️", callback_data=f"rev:{page - 1}"))
    if page + 1 < pages:
        nav.append(B(text="▶️", callback_data=f"rev:{page + 1}"))
    return inline(*([nav] if nav else []), back_button())


def info(agreement_url: str = "", privacy_url: str = "") -> InlineKeyboardMarkup:
    rows = []
    if agreement_url:
        rows.append(btn("agreement", url=agreement_url))
    if privacy_url:
        rows.append(btn("privacy", url=privacy_url))
    return inline(*rows, back_button())


def main_menu(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [menu_button(content.MENU_VIDEOS)],
        [menu_button(content.MENU_TRAINING), menu_button(content.MENU_REVIEWS)],
        [menu_button(content.MENU_FAQ), menu_button(content.MENU_INFO)],
        [menu_button(content.MENU_SUPPORT)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=content.MENU_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------------------------------------------------------------- воронка

def welcome(channel_url: str) -> InlineKeyboardMarkup:
    return inline(
        btn("subscribe", url=channel_url),
        btn("continue", callback_data="go:continue"),
    )


def show() -> InlineKeyboardMarkup:
    return inline(btn("show", callback_data="f:show"))


def after_show() -> InlineKeyboardMarkup:
    return get_access()


def get_access() -> InlineKeyboardMarkup:
    return inline(btn("get_access", callback_data="f:offer"))


def support_button(manager_username: str) -> B | None:
    if not manager_username:
        return None
    return btn("support", url=f"https://t.me/{manager_username}")


def offer(manager_username: str, payment_url: str = "") -> InlineKeyboardMarkup:
    # «Оплатить по СБП» открывает отдельное меню: «Оплатить» (ссылка) и «Я оплатил»
    rows = [
        btn("pay_sbp", callback_data="pay:sbp"),
        btn("promo", callback_data="pay:promo"),
    ]
    if support := support_button(manager_username):
        rows.append(support)
    return inline(*rows)


def sbp(payment_url: str = "") -> InlineKeyboardMarkup:
    rows = [btn("pay_link", url=payment_url)] if payment_url else []
    return inline(
        *rows,
        btn("paid", callback_data="pay:sent"),
        btn("offer_back", callback_data="f:offer"),
    )


def manager(config: Config) -> InlineKeyboardMarkup | None:
    if not config.manager_username:
        return None
    return inline(btn("write_support", url=f"https://t.me/{config.manager_username}"))


# ---------------------------------------------------------------- экзамен

def exam_start() -> InlineKeyboardMarkup:
    return inline(B(text="▶️ Начать экзамен", callback_data="ex:start"))


def training() -> InlineKeyboardMarkup:
    return inline(
        btn("tickets", callback_data="tk:list"),
        btn("exam", callback_data="ex:start"),
    )


def tickets(numbers: list[int]) -> InlineKeyboardMarkup:
    buttons = [B(text=str(n), callback_data=f"tk:{n}") for n in numbers]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    return inline(*rows, back_button())


def ticket_finish(ticket: int, show_offer: bool) -> InlineKeyboardMarkup:
    rows = []
    if show_offer:
        rows.append(btn("get_access", callback_data="f:offer"))
    rows += [
        btn("ticket_retry", callback_data=f"tk:{ticket}"),
        btn("ticket_other", callback_data="tk:list"),
    ]
    return inline(*rows)


def exam_answers(index: int, count: int) -> InlineKeyboardMarkup:
    return inline([B(text=str(i + 1), callback_data=f"ex:{index}:{i}") for i in range(count)])


def exam_finish(failed: bool, paid: bool) -> InlineKeyboardMarkup:
    rows = []
    if failed and not paid:
        rows.append(btn("get_access", callback_data="f:offer"))
    rows.append(btn("exam_retry", callback_data="ex:start"))
    return inline(*rows)


# ------------------------------------------------------------------ дожим

def followup(button: str, manager_username: str = "") -> InlineKeyboardMarkup:
    if button == "show":
        return inline(btn("followup_show", callback_data="f:show"))
    if button == "offer_support" and (support := support_button(manager_username)):
        return inline(btn("get_access", callback_data="f:offer"), support)
    if button == "exam":
        return inline(btn("followup_exam", callback_data="ex:start"))
    return get_access()


# ------------------------------------------------------------------ админка

def admin_menu(pending: int) -> InlineKeyboardMarkup:
    pending_text = f"💳 Ждут подтверждения ({pending})" if pending else "💳 Ждут подтверждения"
    return inline(
        B(text="📊 Статистика", callback_data="adm:stats:day"),
        B(text="👥 Пользователи", callback_data="adm:users"),
        B(text=pending_text, callback_data="adm:pending"),
        B(text="📣 Рассылка", callback_data="adm:bc"),
        B(text="🎁 Выдать доступ", callback_data="adm:grant"),
        B(text="🎬 Видео в воронке", callback_data="adm:media"),
        B(text="🔗 Каналы и группа", callback_data="adm:chats"),
        B(text="⭐ Отзывы", callback_data="adm:reviews"),
        B(text="✏️ Тексты", callback_data="txt:menu"),
        B(text="🎨 Кнопки: цвет и эмодзи", callback_data="btn:menu"),
        B(text="⚙️ Настройки", callback_data="set:menu"),
        B(text="🧹 Очистка", callback_data="adm:clean"),
    )


def admin_clean() -> InlineKeyboardMarkup:
    return inline(
        B(text="🧹 Очистить всё", callback_data="adm:clean_all"),
        B(text="👤 Удалить одного пользователя", callback_data="adm:clean_one"),
        B(text="⬅️ В админку", callback_data="adm:menu"),
    )


def admin_confirm(yes_data: str) -> InlineKeyboardMarkup:
    return inline([
        B(text="✅ Да, удалить", callback_data=yes_data),
        B(text="✖️ Отмена", callback_data="adm:clean"),
    ])


def admin_reviews(reviews: list[dict]) -> InlineKeyboardMarkup:
    rows = [B(text="➕ Добавить отзыв", callback_data="adm:rev_add")]
    rows += [B(text=f"🗑 Удалить #{r['id']} — {r['author'][:20]}", callback_data=f"adm:rev_del:{r['id']}")
             for r in reviews]
    rows.append(B(text="⬅️ В админку", callback_data="adm:menu"))
    return inline(*rows)


def admin_setchat(chat_id: int) -> InlineKeyboardMarkup:
    return inline(
        B(text="🔒 Закрытая группа с разборами", callback_data=f"setchat:private:{chat_id}"),
        B(text="📢 Наш канал (ссылка в приветствии)", callback_data=f"setchat:channel:{chat_id}"),
        B(text="✖️ Ни то, ни другое", callback_data="setchat:none:0"),
    )


def admin_chats(has_channel: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_channel:
        rows.append(B(text="🚫 Убрать канал из приветствия", callback_data="setchat:channel_off:0"))
    rows.append(B(text="⬅️ В админку", callback_data="adm:menu"))
    return inline(*rows)


def admin_back() -> InlineKeyboardMarkup:
    return inline(B(text="⬅️ В админку", callback_data="adm:menu"))


def admin_stats(period: str) -> InlineKeyboardMarkup:
    labels = {"day": "Сегодня", "week": "7 дней", "all": "Всё время"}
    return inline(
        [B(text=("• " if p == period else "") + t, callback_data=f"adm:stats:{p}") for p, t in labels.items()],
        B(text="⬅️ В админку", callback_data="adm:menu"),
    )


def admin_users() -> InlineKeyboardMarkup:
    return inline(
        B(text="🛒 Дошли до оплаты, не купили", callback_data="adm:list:checkout:0"),
        B(text="✅ Купили", callback_data="adm:list:paid:0"),
        B(text="🆕 Все (новые сверху)", callback_data="adm:list:all:0"),
        B(text="⬅️ В админку", callback_data="adm:menu"),
    )


def admin_list_nav(kind: str, page: int, has_next: bool) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(B(text="◀️", callback_data=f"adm:list:{kind}:{page - 1}"))
    if has_next:
        nav.append(B(text="▶️", callback_data=f"adm:list:{kind}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append(B(text="⬅️ Назад", callback_data="adm:users"))
    return inline(*rows)


def admin_payment(request_id: int) -> InlineKeyboardMarkup:
    return inline([
        B(text="✅ Подтвердить", callback_data=f"adm:pay_ok:{request_id}"),
        B(text="❌ Отклонить", callback_data=f"adm:pay_no:{request_id}"),
    ])


def admin_broadcast_audience() -> InlineKeyboardMarkup:
    return inline(
        B(text="👥 Всем", callback_data="adm:bc_to:all"),
        B(text="🛒 Кто не купил", callback_data="adm:bc_to:unpaid"),
        B(text="✅ Кто купил", callback_data="adm:bc_to:paid"),
        B(text="⬅️ В админку", callback_data="adm:menu"),
    )


def admin_broadcast_confirm() -> InlineKeyboardMarkup:
    return inline([
        B(text="🚀 Отправить", callback_data="adm:bc_go"),
        B(text="✖️ Отмена", callback_data="adm:menu"),
    ])


def admin_media(has_demo: bool, followup_count: int = 0) -> InlineKeyboardMarkup:
    rows = [
        B(text=("🔁 Заменить" if has_demo else "➕ Загрузить") + " видео-показ канала", callback_data="adm:m_demo"),
    ]
    if has_demo:
        rows.append(B(text="👁 Посмотреть, как видит клиент", callback_data="adm:m_preview"))
    rows.append(B(text="➕ Добавить видео к напоминаниям", callback_data="adm:m_fu"))
    if followup_count:
        rows.append(B(text="👁 Пример напоминания с видео", callback_data="adm:m_fu_preview"))
        rows.append(B(text=f"🗑 Удалить видео напоминаний ({followup_count})", callback_data="adm:m_fu_clear"))
    rows.append(B(text="⬅️ В админку", callback_data="adm:menu"))
    return inline(*rows)


# ------------------------------------------------------------ админка: тексты

def admin_text_categories() -> InlineKeyboardMarkup:
    rows = [B(text=title, callback_data=f"txt:c:{cat}") for cat, title in content.TEXT_CATEGORIES.items()]
    rows.append(B(text="⬅️ В админку", callback_data="adm:menu"))
    return inline(*rows)


def admin_text_list(category: str, changed: set[str]) -> InlineKeyboardMarkup:
    rows = [B(text=("✏️ " if key in changed else "") + d.title, callback_data=f"txt:v:{key}")
            for key, d in content.TEXTS.items() if d.category == category]
    rows.append(B(text="⬅️ К разделам", callback_data="txt:menu"))
    return inline(*rows)


def admin_text(key: str, changed: bool) -> InlineKeyboardMarkup:
    rows = [B(text="✍️ Заменить", callback_data=f"txt:e:{key}")]
    if changed:
        rows.append(B(text="↩️ Сбросить к стандартному", callback_data=f"txt:r:{key}"))
    rows.append(B(text="⬅️ К списку", callback_data=f"txt:c:{content.TEXTS[key].category}"))
    return inline(*rows)


def admin_text_confirm(key: str) -> InlineKeyboardMarkup:
    return inline([
        B(text="✅ Сохранить", callback_data=f"txt:s:{key}"),
        B(text="✖️ Отмена", callback_data=f"txt:v:{key}"),
    ])


# ------------------------------------------------------------ админка: кнопки

def admin_buttons() -> InlineKeyboardMarkup:
    styles = runtime.current().styles
    rows = [B(text=f"{STYLES.get(styles.get(g), '⚪ Обычный').split()[0]} {title}", callback_data=f"btn:g:{g}")
            for g, title in BUTTON_GROUPS.items()]
    rows.append(B(text="✨ Премиум-эмодзи на кнопках", callback_data="btn:icons"))
    rows.append(B(text="⬅️ В админку", callback_data="adm:menu"))
    return inline(*rows)


def admin_button_group(group: str) -> InlineKeyboardMarkup:
    current = runtime.current().styles.get(group)
    choices = [*STYLES.items(), ("none", "⚪ Обычный (как в Telegram)")]
    rows = [B(text=("• " if (value == current or (value == "none" and not current)) else "") + title,
              callback_data=f"btn:s:{group}:{value}", style=value if value in STYLES else None)
            for value, title in choices]
    rows.append(B(text="⬅️ Назад", callback_data="btn:menu"))
    return inline(*rows)


def admin_icons() -> InlineKeyboardMarkup:
    icons = runtime.current().icons
    rows = [B(text=("✨ " if key in icons else "") + text, callback_data=f"btn:i:{key}")
            for key, (text, _) in BUTTONS.items()]
    rows.append(B(text="⬅️ Назад", callback_data="btn:menu"))
    return inline(*rows)


def admin_icon(key: str, has_icon: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_icon:
        rows.append(B(text="🗑 Убрать премиум-эмодзи", callback_data=f"btn:x:{key}"))
    rows.append(B(text="⬅️ Назад", callback_data="btn:icons"))
    return inline(*rows)


# --------------------------------------------------------- админка: настройки

SETTING_TITLES = {
    "bot_name": "Название бота",
    "author_name": "Имя автора",
    "manager_username": "Username поддержки",
    "agreement_url": "Ссылка на соглашение",
    "privacy_url": "Ссылка на политику",
    "payment_url": "Ссылка на оплату (Paydex)",
}


def admin_settings() -> InlineKeyboardMarkup:
    rows = [B(text=title, callback_data=f"set:e:{field}") for field, title in SETTING_TITLES.items()]
    rows.append(B(text="⬅️ В админку", callback_data="adm:menu"))
    return inline(*rows)


def admin_setting(field: str, overridden: bool) -> InlineKeyboardMarkup:
    rows = [B(text="🚫 Сделать пустым", callback_data=f"set:clear:{field}")]
    if overridden:
        rows.append(B(text="↩️ Вернуть значение из .env", callback_data=f"set:env:{field}"))
    rows.append(B(text="⬅️ Назад", callback_data="set:menu"))
    return inline(*rows)
