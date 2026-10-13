"""Сквозные тесты бота: апдейты идут через настоящий Dispatcher, а вместо
Telegram API — сессия-заглушка, которая записывает все запросы.

Запуск: python tests/test_bot.py   (или pytest)
"""
import asyncio
import re
import os
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from itertools import count

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    ExportChatInviteLink,
    GetChat,
    CopyMessage,
    CreateChatInviteLink,
    GetChatMember,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendVideo,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    AcceptedGiftTypes,
    CallbackQuery,
    ChatFullInfo,
    ChatMemberAdministrator,
    ChatMemberUpdated,
    Chat,
    ChatInviteLink,
    ChatMemberLeft,
    ChatMemberMember,
    Message,
    MessageEntity,
    MessageId,
    PhotoSize,
    Update,
    User,
    Video,
)

from bot import content
from bot.__main__ import build_dispatcher
from bot.config import Config
from bot.db import Database
from bot.emoji_fallback import PremiumEmojiFallback
from bot.followups import process_due
from bot.questions import QuestionBank

ADMIN = 1000
AGREEMENT = "https://example.com/agreement"
PRIVACY = "https://example.com/privacy"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTIONS = QuestionBank.load(os.path.join(ROOT, "data", "questions.json"))
_ids = count(1)


class MockSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []
        self.members: set[tuple[str | int, int]] = set()
        # Имитация бота без Telegram Premium у владельца: премиум-эмодзи отклоняются
        self.reject_premium = False
        self.middleware(PremiumEmojiFallback())  # как в боте (__main__.py)

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if self.reject_premium and has_premium(method):
            raise TelegramBadRequest(method=method, message="Bad Request: custom emoji are not allowed")
        if isinstance(method, SendMessage) and "СЛОМАТЬ" in (method.text or ""):
            raise TelegramBadRequest(method=method, message="Bad Request: can't parse entities")
        if isinstance(method, GetChatMember) and method.user_id == bot.id:
            return admin_member(bot.id)
        if isinstance(method, GetChat):
            username = "lite_channel" if method.chat_id == -100900 else None
            return ChatFullInfo(id=method.chat_id, type="supergroup", title=f"Chat {method.chat_id}",
                                username=username, accent_color_id=0, max_reaction_count=0,
                                accepted_gift_types=AcceptedGiftTypes(
                                    unlimited_gifts=False, limited_gifts=False, unique_gifts=False,
                                    premium_subscription=False, gifts_from_channels=False))
        if isinstance(method, ExportChatInviteLink):
            return "https://t.me/+exported"
        if isinstance(method, GetChatMember):
            u = User(id=method.user_id, is_bot=False, first_name="x")
            if (method.chat_id, method.user_id) in self.members:
                return ChatMemberMember(user=u)
            return ChatMemberLeft(user=u)
        if isinstance(method, CreateChatInviteLink):
            return ChatInviteLink(
                invite_link=f"https://t.me/+invite{next(_ids)}", creator=User(id=1, is_bot=True, first_name="b"),
                creates_join_request=False, is_primary=False, is_revoked=False,
            )
        if isinstance(method, CopyMessage):
            return MessageId(message_id=next(_ids))
        if isinstance(method, (SendMessage, SendPhoto, SendVideo, SendDocument)):
            return Message(message_id=next(_ids), date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"))
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def close(self) -> None:
        pass

    def texts(self, chat_id: int) -> list[str]:
        out = []
        for r in self.requests:
            if isinstance(r, SendMessage) and r.chat_id == chat_id:
                out.append(r.text)
            elif isinstance(r, (SendPhoto, SendVideo, SendDocument)) and r.chat_id == chat_id and r.caption:
                out.append(r.caption)
        return out

    def of(self, kind, chat_id: int | None = None) -> list:
        return [r for r in self.requests if isinstance(r, kind) and (chat_id is None or r.chat_id == chat_id)]

    def screen(self, chat_id: int):
        """Последнее, что видит человек: новое сообщение или изменённое (раздел открывается на месте)."""
        return [r for r in self.requests if isinstance(r, (SendMessage, EditMessageText))
                and r.chat_id == chat_id][-1]

    def alerts(self) -> list[str]:
        return [r.text for r in self.of(AnswerCallbackQuery) if r.text]

    def clear(self) -> None:
        self.requests.clear()


def has_premium(method) -> bool:
    text = (getattr(method, "text", None) or "") + (getattr(method, "caption", None) or "")
    markup = getattr(method, "reply_markup", None)
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or []
    return "<tg-emoji" in text or any(getattr(b, "icon_custom_emoji_id", None) for row in rows for b in row)


def buttons_of(method) -> list:
    markup = method.reply_markup
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None) or []
    return [b for row in rows for b in row]


def admin_member(user_id: int) -> ChatMemberAdministrator:
    # Все обязательные флаги прав — False (их список растёт с новыми версиями Bot API)
    flags = {name: False for name, f in ChatMemberAdministrator.model_fields.items()
             if f.is_required() and name not in ("status", "user", "can_invite_users")}
    return ChatMemberAdministrator(user=User(id=user_id, is_bot=True, first_name="bot"),
                                   can_invite_users=True, **flags)


def make_config(db_path: str, **overrides) -> Config:
    base = Config(
        bot_token="42:TEST", admin_ids=(ADMIN,), db_path=db_path,
        channel_url="https://t.me/+channel", private_channel_id=-100500, access_link="",
        price=1490, old_price=None, manager_username="support_lite", payment_url="",
        payment_details="Т-Банк, +7 900 000-00-00, Получатель", questions_path="",
        followup_delay_scale=1, timezone="Europe/Moscow",
        bot_name="Лайт ПДД", author_name="Автор Тестов", agreement_url=AGREEMENT, privacy_url=PRIVACY,
    )
    return replace(base, **overrides)


# Роутеры aiogram — модульные объекты, поэтому диспетчер один на все тесты,
# а db/config передаются в каждый апдейт.
_DP = None


class Harness:
    def __init__(self, db: Database, config: Config):
        global _DP
        if _DP is None:
            _DP = build_dispatcher(db, config, QUESTIONS)
        self.db, self.config = db, config
        self.s = MockSession()
        self.bot = Bot(config.bot_token, session=self.s)

    async def _feed(self, **upd) -> None:
        await _DP.feed_update(self.bot, Update(update_id=next(_ids), **upd),
                              db=self.db, config=self.config, questions=QUESTIONS)

    def _user(self, uid: int) -> User:
        return User(id=uid, is_bot=False, first_name=f"U{uid}", username=f"user{uid}")

    def _msg(self, uid: int, **kw) -> Message:
        return Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=uid, type="private"),
                       from_user=self._user(uid), **kw)

    async def text(self, uid: int, text: str) -> None:
        entities = None
        if text.startswith("/"):
            entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
        await self._feed(message=self._msg(uid, text=text, entities=entities))

    async def rich(self, uid: int, text: str, entities: list[MessageEntity]) -> None:
        """Сообщение с форматированием (жирный, премиум-эмодзи и т.п.)."""
        await self._feed(message=self._msg(uid, text=text, entities=entities))

    async def photo(self, uid: int) -> None:
        await self._feed(message=self._msg(uid, photo=[PhotoSize(file_id="receipt", file_unique_id="r",
                                                                 width=1, height=1)]))

    async def video(self, uid: int, file_id: str) -> None:
        await self._feed(message=self._msg(uid, video=Video(file_id=file_id, file_unique_id=file_id,
                                                            width=1, height=1, duration=1)))

    async def click(self, uid: int, data: str) -> None:
        await self._feed(callback_query=CallbackQuery(
            id=str(next(_ids)), from_user=self._user(uid), chat_instance="x", data=data,
            message=self._msg(uid, text="old"),
        ))

    async def section(self, uid: int, name: str):
        """Открыть раздел кнопкой главного меню; возвращает экран раздела."""
        await self.click(uid, f"m:{name}")
        return self.s.screen(uid)

    async def force_followup(self, uid: int) -> int:
        await self.db.update_user(uid, next_followup_at=int(time.time()) - 1)
        return await process_due(self.bot, self.db, self.config)


async def with_db(fn, **cfg):
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "bot.db"))
        await db.connect()
        try:
            await fn(Harness(db, make_config(db.path, **cfg)))
        finally:
            await db.close()


# ---------------------------------------------------------------- сценарии

async def scenario_purchase(h: Harness) -> None:
    db, s, uid = h.db, h.s, 501

    # /start с меткой источника → приветствие + необязательный канал: «Подписаться» и «Продолжить»
    await h.text(uid, "/start insta")
    user = await db.get_user(uid)
    assert user["source"] == "insta" and user["next_followup_at"]
    welcome = s.of(SendMessage, uid)[0]
    assert "не обязательна" in welcome.text and "Подписка есть" not in welcome.text
    buttons = [b for row in welcome.reply_markup.inline_keyboard for b in row]
    assert buttons[0].url == "https://t.me/+channel" and buttons[1].callback_data == "go:continue"

    # «Продолжить» — без всякой проверки подписки → меню + «о себе»
    s.clear()
    await h.click(uid, "go:continue")
    assert not s.of(GetChatMember)
    texts = s.texts(uid)
    assert content.MENU_HINT in texts and any("Меня зовут Автор Тестов" in t for t in texts)
    assert any("Чат с учениками и поддержка" in t for t in texts)

    # «Показать» без видео → только текст
    s.clear()
    await h.click(uid, "f:show")
    assert any("Вот как это выглядит" in t for t in s.texts(uid)) and not s.of(SendVideo)

    # админ загружает видео-показ
    await h.text(ADMIN, "/admin")
    await h.click(ADMIN, "adm:m_demo")
    await h.video(ADMIN, "demo_vid")
    assert (await db.get_setting("demo_media"))["file_id"] == "demo_vid"

    s.clear()
    await h.click(uid, "f:show")
    show_msg = s.of(SendVideo, uid)[0]
    assert show_msg.video == "demo_vid"
    show_buttons = [b.text for row in show_msg.reply_markup.inline_keyboard for b in row]
    assert show_buttons == ["👑 ПОЛУЧИТЬ ДОСТУП"]  # бесплатных разборов больше нет

    # оффер: юзернейм в тексте + кнопка поддержки
    s.clear()
    await h.click(uid, "f:offer")
    offer_msg = [r for r in s.of(SendMessage, uid) if "<b>Стоимость:</b> 1490₽" in r.text][0]
    assert "@support_lite" in offer_msg.text
    assert AGREEMENT in offer_msg.text and PRIVACY in offer_msg.text
    assert offer_msg.disable_web_page_preview
    urls = [b.url for row in offer_msg.reply_markup.inline_keyboard for b in row if b.url]
    assert urls == ["https://t.me/support_lite"]
    s.clear()
    assert await h.force_followup(uid) == 1
    checkout_msg = s.of(SendMessage, uid)[0]
    assert "открыл оплату" in checkout_msg.text
    assert any(b.url == "https://t.me/support_lite" for row in checkout_msg.reply_markup.inline_keyboard for b in row)

    # кнопка «Поддержка» в меню
    s.clear()
    support_msg = await h.section(uid, "support")
    assert support_msg.reply_markup.inline_keyboard[0][0].url == "https://t.me/support_lite"

    # промокод → не найден
    s.clear()
    await h.click(uid, "pay:promo")
    await h.text(uid, "SALE")
    assert content.PROMO_NOT_FOUND in s.texts(uid)

    # оплата по СБП: реквизиты → «я оплатил» → чек
    s.clear()
    await h.click(uid, "pay:sbp")
    assert any("Т-Банк" in t for t in s.texts(uid))
    await h.click(uid, "pay:sent")
    assert content.PAYMENT_CHECKING in s.texts(uid)
    admin_card = [r for r in s.of(SendMessage, ADMIN) if "Оплата на проверку #1" in r.text][0]
    assert "@user501" in admin_card.text and "1490 ₽" in admin_card.text
    assert re.search(r"\d\d\.\d\d\.\d{4} \d\d:\d\d", admin_card.text)  # дата и время
    buttons = [b.callback_data for b in admin_card.reply_markup.inline_keyboard[0]]
    assert buttons == ["adm:pay_ok:1", "adm:pay_no:1"]
    # повторное «Я оплатил» не создаёт вторую заявку
    s.clear()
    await h.click(uid, "pay:sent")
    assert content.PAYMENT_ALREADY_CHECKING in s.texts(uid) and not s.texts(ADMIN)

    # админ подтверждает → доступ и одноразовая ссылка
    s.clear()
    await h.click(ADMIN, "adm:pay_ok:1")
    user = await db.get_user(uid)
    assert user["paid"] and user["next_followup_at"] is None
    link_req = s.of(CreateChatInviteLink)[0]
    assert link_req.chat_id == -100500 and link_req.member_limit == 1
    assert any("СПАСИБО ЗА ПОКУПКУ" in t and "t.me/+invite" in t for t in s.texts(uid))
    assert any("Новая покупка — 1490 ₽" in t for t in s.texts(ADMIN))

    # повторное нажатие не выдаёт доступ дважды
    s.clear()
    await h.click(ADMIN, "adm:pay_ok:1")
    assert "уже обработана" in s.alerts()[-1] and not s.of(CreateChatInviteLink)

    # купившему дожим не приходит
    assert await h.force_followup(uid) == 0

    # «Видео разборы» купившему → ссылка; если уже в канале — без новой ссылки
    s.clear()
    await h.click(uid, "m:videos")
    assert any("Твоя ссылка" in t for t in s.texts(uid))
    s.members.add((-100500, uid))
    s.clear()
    await h.click(uid, "m:videos")
    assert content.ACCESS_ALREADY_MEMBER in s.texts(uid) and not s.of(CreateChatInviteLink)

    # второй пользователь дошёл до оплаты и не купил
    await h.text(777, "/start")
    await h.click(777, "go:continue")
    await h.click(777, "f:offer")

    # статистика и списки
    s.clear()
    await h.click(ADMIN, "adm:stats:all")
    stats = [r.text for r in s.requests if hasattr(r, "text") and r.text and "Статистика" in r.text][0]
    assert "Купили: <b>1</b>" in stats and "Выручка: <b>1 490 ₽</b>" in stats
    assert "дошли до оплаты и не купили (всего): <b>1</b>" in stats
    assert "insta: 1 / 1" in stats

    s.clear()
    await h.click(ADMIN, "adm:list:checkout:0")
    listing = [r.text for r in s.requests if hasattr(r, "text") and r.text and "не купили" in r.text][0]
    assert "user777" in listing and "user501" not in listing

    # рассылка тем, кто не купил
    s.clear()
    await h.click(ADMIN, "adm:bc")
    await h.click(ADMIN, "adm:bc_to:unpaid")
    await h.text(ADMIN, "Скидка только сегодня!")
    await h.click(ADMIN, "adm:bc_go")
    await asyncio.sleep(0.5)
    targets = {r.chat_id for r in s.of(CopyMessage)}
    assert 777 in targets and uid not in targets
    assert any("Рассылка завершена" in t for t in s.texts(ADMIN))

    # ручная выдача доступа по @username
    s.clear()
    await h.click(ADMIN, "adm:grant")
    await h.text(ADMIN, "@user777")
    assert (await db.get_user(777))["paid"]
    assert any("Доступ выдан" in t for t in s.texts(ADMIN))

    # не-админ не попадает в админку
    s.clear()
    await h.text(uid, "/admin")
    assert s.texts(uid) == [content.FALLBACK]


async def scenario_payment_link(h: Harness) -> None:
    db, s, uid = h.db, h.s, 620
    await h.text(uid, "/start")
    s.clear()
    await h.click(uid, "f:offer")
    rows = [r for r in s.of(SendMessage, uid) if "Стоимость" in r.text][0].reply_markup.inline_keyboard
    assert rows[0][0].text == "💳 Оплатить по СБП" and rows[0][0].callback_data == "pay:sbp"
    # отдельное меню оплаты: «Оплатить» (ссылка) и «Я оплатил»
    s.clear()
    await h.click(uid, "pay:sbp")
    menu = s.of(SendMessage, uid)[0]
    assert "Оплата по СБП" in menu.text and "1490 ₽" in menu.text
    rows = menu.reply_markup.inline_keyboard
    assert rows[0][0].text == "💳 Оплатить" and rows[0][0].url == "https://paydex.pro/pay/link-abc"
    assert rows[1][0].callback_data == "pay:sent" and rows[2][0].callback_data == "f:offer"
    await h.click(uid, "pay:sent")
    assert any("Оплата на проверку #1" in t for t in s.texts(ADMIN))
    await h.click(ADMIN, "adm:pay_ok:1")
    assert (await db.get_user(uid))["paid"]
    assert any("СПАСИБО ЗА ПОКУПКУ" in t for t in s.texts(uid))


async def scenario_admin_retest(h: Harness) -> None:
    """Админ может проходить покупку сколько угодно раз и всегда видит оффер."""
    db, s = h.db, h.s
    await h.text(ADMIN, "/start")
    for n in (1, 2):
        await h.click(ADMIN, "pay:sent")
        s.clear()
        await h.click(ADMIN, f"adm:pay_ok:{n}")
        assert any("СПАСИБО ЗА ПОКУПКУ" in t for t in s.texts(ADMIN)), n
    assert (await db.get_user(ADMIN))["paid"]
    # купил, но «Видеоразборы» всё равно показывают оффер
    s.clear()
    await h.click(ADMIN, "m:videos")
    assert any("Стоимость" in t for t in s.texts(ADMIN))
    # кнопка старого нижнего меню тоже работает и заменяет его одной кнопкой «Меню»
    s.clear()
    await h.text(ADMIN, "👑 Видео разборы")
    assert any("Стоимость" in t for t in s.texts(ADMIN))
    hint = [r for r in s.of(SendMessage, ADMIN) if r.text == content.MENU_HINT][0]
    assert [[b.text for b in row] for row in hint.reply_markup.keyboard] == [["Меню"]]


async def scenario_group_silence(h: Harness) -> None:
    """В группе (бот там админ) бот ничего не отвечает и не заносит людей в базу."""
    s = h.s
    for text in ("привет", "/start"):
        entities = [MessageEntity(type="bot_command", offset=0, length=6)] if text == "/start" else None
        msg = Message(message_id=next(_ids), date=datetime.now(),
                      chat=Chat(id=-100777, type="supergroup", title="ПДД | Правила"),
                      from_user=h._user(970), text=text, entities=entities)
        await h._feed(message=msg)
    assert not s.of(SendMessage)
    assert await h.db.get_user(970) is None


async def scenario_cleanup(h: Harness) -> None:
    db, s = h.db, h.s
    for uid in (801, 802, 803):
        await h.text(uid, "/start")
        await h.click(uid, "f:offer")
    await h.click(803, "pay:sent")
    await db.add_review("Анна", "Супер")

    # удалить одного по @username: сначала подтверждение, потом удаление
    await h.click(ADMIN, "adm:clean_one")
    s.clear()
    await h.text(ADMIN, "@user802")
    ask = s.of(SendMessage, ADMIN)[0]
    assert "user802" in ask.text and ask.reply_markup.inline_keyboard[0][0].callback_data == "adm:clean_one_yes:802"
    assert await db.get_user(802)
    await h.click(ADMIN, "adm:clean_one_yes:802")
    assert await db.get_user(802) is None and not await db.has_event(802, "checkout")
    assert await db.get_user(801)

    # очистить всё
    await h.click(ADMIN, "adm:clean_all_yes")
    assert (await db.event_counts(0)) == {}
    assert await db.pending_payment_requests() == []
    assert await db.get_user(801) is None and await db.get_user(803) is None
    assert (await db.reviews())[1] == 1  # отзывы остаются


async def scenario_followup_videos(h: Harness) -> None:
    s, uid = h.s, 990
    await h.click(ADMIN, "adm:m_fu")
    await h.video(ADMIN, "fu_a")
    await h.video(ADMIN, "fu_b")
    assert [m["file_id"] for m in await h.db.get_setting("followup_media")] == ["fu_a", "fu_b"]

    await h.text(uid, "/start")
    s.clear()
    for _ in range(3):
        assert await h.force_followup(uid) == 1
    sent = s.of(SendVideo, uid)
    assert [v.video for v in sent] == ["fu_a", "fu_b", "fu_a"]
    assert "Почему зубрёжка" in sent[0].caption
    assert sent[0].reply_markup.inline_keyboard[0][0].text == "👑 ПОЛУЧИТЬ ДОСТУП"
    assert not s.of(SendMessage, uid)

    # видео удалили → снова обычный текст
    await h.click(ADMIN, "adm:m_fu_clear")
    s.clear()
    assert await h.force_followup(uid) == 1
    assert s.of(SendMessage, uid) and not s.of(SendVideo, uid)


async def scenario_reject(h: Harness) -> None:
    db, s, uid = h.db, h.s, 601
    await h.text(uid, "/start")
    await h.click(uid, "go:continue")
    await h.click(uid, "f:offer")
    await h.click(uid, "pay:sbp")
    await h.click(uid, "pay:sent")
    s.clear()
    await h.click(ADMIN, "adm:pay_no:1")
    assert not (await db.get_user(uid))["paid"]
    assert any("Платёж не найден" in t and "техподдержку" in t and "@support_lite" in t for t in s.texts(uid))
    # после отклонения можно снова нажать «Я оплатил»
    s.clear()
    await h.click(uid, "pay:sent")
    assert content.PAYMENT_CHECKING in s.texts(uid)


async def scenario_browse_followups(h: Harness) -> None:
    s, uid = h.s, 701
    await h.text(uid, "/start")
    # первое сообщение — через 6 часов, не раньше
    first_at = (await h.db.get_user(uid))["next_followup_at"]
    assert first_at - time.time() >= 6 * 3600 - 5
    assert await process_due(h.bot, h.db, h.config) == 0

    s.clear()
    total = 0
    while await h.force_followup(uid):
        total += 1
        assert total < 50
    texts = s.texts(uid)
    # все сообщения по порядку, без повторов и без напоминаний про подписку
    assert "Почему зубрёжка не работает" in texts[0]
    assert not any("подписал" in t for t in texts)
    assert total == len(content.FOLLOWUP_ORDER) == len(set(texts))
    assert "последнее сообщение" in texts[-1]
    assert (await h.db.get_user(uid))["next_followup_at"] is None

    # админу напоминания не приходят
    await h.text(ADMIN, "/start")
    s.clear()
    assert await h.force_followup(ADMIN) == 0
    assert s.texts(ADMIN) == [] and (await h.db.get_user(ADMIN))["next_followup_at"] is None


def test_quiet_hours():
    from zoneinfo import ZoneInfo
    from bot.followups import next_followup_time
    cfg = make_config("x")
    tz = ZoneInfo("Europe/Moscow")
    at = lambda h, m=0: int(datetime(2026, 10, 12, h, m, tzinfo=tz).timestamp())
    local = lambda ts: datetime.fromtimestamp(ts, tz)
    assert local(next_followup_time(at(10), cfg)).hour == 16           # днём — ровно +6 ч
    night = local(next_followup_time(at(18), cfg))                     # 00:00 → утро
    assert (night.day, night.hour, night.minute) == (13, 9, 0)
    early = local(next_followup_time(at(1), cfg))                      # 07:00 → 09:00
    assert (early.day, early.hour) == (12, 9)


async def scenario_chat_setup(h: Harness) -> None:
    """Бота добавили админом в группу → админ выбирает роль чата кнопкой."""
    db, s = h.db, h.s

    async def added_to(chat_id: int) -> None:
        await h._feed(my_chat_member=ChatMemberUpdated(
            chat=Chat(id=chat_id, type="supergroup", title="ПДД | Правила"),
            from_user=h._user(ADMIN), date=datetime.now(),
            old_chat_member=ChatMemberLeft(user=User(id=42, is_bot=True, first_name="b")),
            new_chat_member=admin_member(42),
        ))

    await added_to(-100777)
    question = [r for r in s.of(SendMessage, ADMIN) if "Меня сделали админом" in r.text][0]
    assert "ПДД | Правила" in question.text
    buttons = [b.callback_data for row in question.reply_markup.inline_keyboard for b in row]
    assert "setchat:private:-100777" in buttons

    await h.click(ADMIN, "setchat:private:-100777")
    assert (await db.get_setting("private_chat"))["id"] == -100777

    await added_to(-100900)
    await h.click(ADMIN, "setchat:channel:-100900")
    assert (await db.get_setting("channel"))["url"] == "https://t.me/lite_channel"

    # новый пользователь видит кнопку «Подписаться» на выбранный канал
    s.clear()
    await h.text(901, "/start")
    welcome = s.of(SendMessage, 901)[0]
    assert welcome.reply_markup.inline_keyboard[0][0].url == "https://t.me/lite_channel"
    await h.click(901, "go:continue")

    # после выдачи доступа ссылка создаётся в выбранной группе
    s.clear()
    await h.click(ADMIN, "adm:grant")
    await h.text(ADMIN, "901")
    assert s.of(CreateChatInviteLink)[0].chat_id == -100777

    # канал можно убрать из приветствия
    await h.click(ADMIN, "setchat:channel_off:0")
    s.clear()
    await h.text(902, "/start")
    assert any("Меня зовут Автор Тестов" in t for t in s.texts(902))


async def scenario_menu(h: Harness) -> None:
    s, uid = h.s, 950
    await h.text(uid, "/start")
    await h.click(uid, "go:continue")

    # внизу — одна кнопка «Меню»
    hint = [r for r in s.of(SendMessage, uid) if r.text == content.MENU_HINT][0]
    assert [[b.text for b in row] for row in hint.reply_markup.keyboard] == [["Меню"]]

    # «Меню» → «Добро пожаловать!» с инлайн-кнопками разделов (у клиента нет «Админки»)
    s.clear()
    await h.text(uid, "Меню")
    menu = s.of(SendMessage, uid)[0]
    assert menu.text == content.MAIN_MENU
    assert [[b.callback_data for b in row] for row in menu.reply_markup.inline_keyboard] == [
        ["m:videos"], ["m:training", "m:reviews"], ["m:faq", "m:info"], ["m:support"]]
    assert buttons_of(menu)[0].text == "👑 Видеоразборы"
    s.clear()
    await h.text(ADMIN, "/menu")
    assert buttons_of(s.of(SendMessage, ADMIN)[0])[-1].callback_data == "adm:menu"

    def is_back(row):
        return row[0].callback_data == "menu:back" and row[0].text == content.BACK

    # раздел открывается в том же сообщении, последняя кнопка — «Назад»
    for name in ("training", "reviews", "faq", "info", "support"):
        s.clear()
        screen = await h.section(uid, name)
        assert isinstance(screen, EditMessageText) and is_back(screen.reply_markup.inline_keyboard[-1]), name

    # «Видеоразборы» — оффер с кнопками оплаты и «Назад»
    s.clear()
    rows = (await h.section(uid, "videos")).reply_markup.inline_keyboard
    assert rows[0][0].callback_data == "pay:sbp" and is_back(rows[-1])

    # «Частые вопросы» — с юзернеймом поддержки
    faq = (await h.section(uid, "faq")).text
    assert "Частые вопросы" in faq and "@support_lite" in faq and "Эльдар" not in faq

    # «Информация» — имя автора из настроек, ссылки на документы
    msg = await h.section(uid, "info")
    assert "Меня зовут Автор Тестов" in msg.text and "Эльдар" not in msg.text
    urls = [r[0].url for r in msg.reply_markup.inline_keyboard[:2]]
    assert urls == [AGREEMENT, PRIVACY]

    # «Назад» превращает сообщение раздела обратно в главное меню
    s.clear()
    await h.click(uid, "menu:back")
    edits = [r for r in s.requests if isinstance(r, EditMessageText)]
    assert edits and edits[0].text == content.MAIN_MENU
    assert edits[0].reply_markup.inline_keyboard[0][0].callback_data == "m:videos"

    # отзывы: пусто → админ добавляет → пользователь видит → админ удаляет
    assert (await h.section(uid, "reviews")).text == content.REVIEWS_EMPTY
    for i in range(7):
        await h.click(ADMIN, "adm:rev_add")
        await h.text(ADMIN, f"Сдал с первого раза <{i}>")
        await h.text(ADMIN, f"@student{i}")
    page1 = await h.section(uid, "reviews")
    assert "@student6" in page1.text and "&lt;6&gt;" in page1.text and "(стр. 1/2)" in page1.text
    assert page1.reply_markup.inline_keyboard[0][0].callback_data == "rev:1"
    s.clear()
    await h.click(uid, "rev:1")
    page2 = [r for r in s.requests if isinstance(r, EditMessageText)][0]
    assert "@student0" in page2.text and "@student6" not in page2.text
    await h.click(ADMIN, "adm:rev_del:1")
    rows, total = await h.db.reviews()
    assert total == 6 and all(r["id"] != 1 for r in rows)

    # оффер из воронки (не из меню) — без «Назад»
    s.clear()
    await h.click(uid, "f:offer")
    assert not is_back(s.of(SendMessage, uid)[-1].reply_markup.inline_keyboard[-1])


async def scenario_tickets(h: Harness) -> None:
    import json
    db, s, uid = h.db, h.s, 960
    await h.text(uid, "/start")

    def questions_sent():
        return [t for t in s.texts(uid) if t.startswith("<b>Вопрос ")]

    # список билетов: только те, что есть в базе
    s.clear()
    await h.click(uid, "tk:list")
    grid = s.of(SendMessage, uid)[0].reply_markup.inline_keyboard
    numbers = [b.callback_data for row in grid for b in row if b.callback_data.startswith("tk:")]
    assert numbers == [f"tk:{n}" for n in QUESTIONS.ticket_numbers()]

    # билет 3: вопросы по порядку, 3 ошибки не прерывают, в конце итог и оффер
    s.clear()
    await h.click(uid, "tk:3")
    ticket = QUESTIONS.ticket(3)
    for i, q in enumerate(ticket):
        assert json.loads((await db.get_user(uid))["exam_ids"])[i] == q.id
        answer = q.correct if i >= 3 else (q.correct + 1) % len(q.answers)
        await h.click(uid, f"ex:{i}:{answer}")
    assert len(questions_sent()) == 20 and "Вопрос 1 из 20" in questions_sent()[0]
    result = [r for r in s.of(SendMessage, uid) if "Билет 3 пройден" in r.text][0]
    assert "17 из 20" in result.text and "ошибок: 3" in result.text
    assert result.reply_markup.inline_keyboard[0][0].callback_data == "f:offer"

    # картинки из билета отправляются фото
    assert s.of(SendPhoto, uid)

    # ссылка из поста в группе: /start ticket_4 сразу запускает билет 4
    s.clear()
    await h.text(uid, "/start ticket_4")
    first = QUESTIONS.ticket(4)[0]
    assert json.loads((await db.get_user(uid))["exam_ids"])[0] == first.id
    assert (await db.get_user(uid))["exam_mode"] == "ticket:4"

    # билета нет в базе
    s.clear()
    await h.text(uid, "/start ticket_39")
    assert content.TICKET_NOT_FOUND in s.texts(uid)


async def scenario_exam(h: Harness) -> None:
    db, s, uid = h.db, h.s, 888
    # канал не задан → сразу «о себе»
    await h.text(uid, "/start")
    assert any("Меня зовут Автор Тестов" in t for t in s.texts(uid))

    s.clear()
    assert "Экзамен" in (await h.section(uid, "training")).text

    # 3 ошибки → экзамен окончен, предлагаем доступ
    await h.click(uid, "ex:start")
    for i in range(3):
        user = await db.get_user(uid)
        q = QUESTIONS.by_id[__import__("json").loads(user["exam_ids"])[i]]
        await h.click(uid, f"ex:{i}:{(q.correct + 1) % len(q.answers)}")
    fail = [t for t in s.texts(uid) if "Экзамен не сдан" in t]
    assert fail and "3 ошибки на 3 вопросах" in fail[0]
    assert await db.add_event(uid, "exam_fail") is False  # событие уже записано

    # повторное нажатие после окончания
    s.clear()
    await h.click(uid, "ex:0:0")
    assert "уже ответил" in s.alerts()[-1]

    # без ошибок → короткая похвала
    s.clear()
    await h.click(uid, "ex:start")
    import json
    ids = json.loads((await db.get_user(uid))["exam_ids"])
    for i, qid in enumerate(ids):
        await h.click(uid, f"ex:{i}:{QUESTIONS.by_id[qid].correct}")
    assert any(f"Экзамен сдан!</b> {len(ids)} из {len(ids)}" in t for t in s.texts(uid))


def test_purchase():
    asyncio.run(with_db(scenario_purchase))


def test_payment_link():
    asyncio.run(with_db(scenario_payment_link, payment_url="https://paydex.pro/pay/link-abc"))


def test_admin_retest():
    asyncio.run(with_db(scenario_admin_retest))


def test_group_silence():
    asyncio.run(with_db(scenario_group_silence))


def test_cleanup():
    asyncio.run(with_db(scenario_cleanup))


def test_followup_videos():
    asyncio.run(with_db(scenario_followup_videos))


def test_reject():
    asyncio.run(with_db(scenario_reject))


def test_browse_followups():
    asyncio.run(with_db(scenario_browse_followups))


def test_chat_setup():
    asyncio.run(with_db(scenario_chat_setup, channel_url="", private_channel_id=None))


def test_menu():
    asyncio.run(with_db(scenario_menu))


def test_tickets():
    asyncio.run(with_db(scenario_tickets, channel_url=""))


def test_exam():
    asyncio.run(with_db(scenario_exam, channel_url=""))


# ------------------------------------------------- тексты, настройки, кнопки

def last_text(s: MockSession, chat_id: int) -> str:
    return s.texts(chat_id)[-1]


async def scenario_text_edit(h: Harness) -> None:
    db, s, uid = h.db, h.s, 1201

    # раздел «Тексты» в админке: разделы → список → текущий текст с плейсхолдерами
    s.clear()
    await h.text(ADMIN, "/admin")
    menu = s.of(SendMessage, ADMIN)[-1]
    assert "txt:menu" in [b.callback_data for b in buttons_of(menu)]
    await h.click(ADMIN, "txt:menu")
    await h.click(ADMIN, "txt:c:funnel")
    s.clear()
    await h.click(ADMIN, "txt:v:about")
    shown = s.of(SendMessage, ADMIN)
    assert shown[0].text == content.ABOUT and "{author}" in shown[0].text   # шаблон как есть
    assert "стандартный" in shown[1].text and "{author}" in shown[1].text

    # замена: жирный текст из Telegram сохраняется как HTML, {author} подставляется
    await h.click(ADMIN, "txt:e:about")
    s.clear()
    await h.rich(ADMIN, "Привет! Я {author}, & это новый текст",
                 [MessageEntity(type="bold", offset=0, length=7)])
    preview, ask = s.of(SendMessage, ADMIN)[-2:]
    assert preview.text == "<b>Привет!</b> Я Автор Тестов, &amp; это новый текст"
    assert [b.callback_data for b in buttons_of(ask)] == ["txt:s:about", "txt:v:about"]
    assert "about" not in (await db.get_setting("texts", {}))  # до подтверждения не сохраняем
    await h.click(ADMIN, "txt:s:about")
    assert (await db.get_setting("texts"))["about"] == "<b>Привет!</b> Я {author}, &amp; это новый текст"

    s.clear()
    await h.text(uid, "/start")
    await h.click(uid, "go:continue")
    assert "<b>Привет!</b> Я Автор Тестов, &amp; это новый текст" in s.texts(uid)

    # ошибки: неизвестный плейсхолдер, непарная скобка, пропал обязательный {link}, сломанная разметка
    await h.click(ADMIN, "txt:e:offer")
    s.clear()
    await h.text(ADMIN, "Цена {price_line}, скидка {discount}")
    assert "не сохранён" in last_text(s, ADMIN) and "{discount}" in last_text(s, ADMIN)
    await h.text(ADMIN, "Цена {price_line")
    assert "Непарная фигурная скобка" in last_text(s, ADMIN)
    await h.text(ADMIN, "СЛОМАТЬ {price_line}")
    assert "Telegram не принял текст" in last_text(s, ADMIN)
    assert "offer" not in await db.get_setting("texts")
    await h.click(ADMIN, "txt:e:access_granted")
    await h.text(ADMIN, "Спасибо!")
    assert "{link}" in last_text(s, ADMIN) and "access_granted" not in await db.get_setting("texts")

    # оффер с плейсхолдерами цены и поддержки
    await h.click(ADMIN, "txt:e:offer")
    await h.text(ADMIN, "Всего {price_line}! Вопросы{contact}{legal}")
    await h.click(ADMIN, "txt:s:offer")
    s.clear()
    await h.click(uid, "f:offer")
    offer = s.texts(uid)[0]
    assert offer.startswith("Всего 1490₽! Вопросы: @support_lite") and AGREEMENT in offer

    # дожимающее сообщение
    await h.click(ADMIN, "txt:e:fu_why_not_cram")
    await h.text(ADMIN, "Новый дожим за {price} ₽")
    await h.click(ADMIN, "txt:s:fu_why_not_cram")
    await db.update_user(uid, followups_sent="[\"checkout\"]")
    s.clear()
    assert await h.force_followup(uid) == 1
    assert s.texts(uid) == ["Новый дожим за 1490 ₽"]

    # описание бота — без HTML, сразу уходит в Telegram
    await h.click(ADMIN, "txt:e:bot_short_description")
    s.clear()
    await h.text(ADMIN, "Лайт ПДД — теория без зубрёжки")
    await h.click(ADMIN, "txt:s:bot_short_description")
    assert any(getattr(r, "short_description", None) == "Лайт ПДД — теория без зубрёжки" for r in s.requests)

    # сброс к стандартному
    s.clear()
    await h.click(ADMIN, "txt:r:about")
    assert "about" not in await db.get_setting("texts")
    await h.text(uid + 1, "/start")
    await h.click(uid + 1, "go:continue")
    assert any("Меня зовут Автор Тестов" in t for t in s.texts(uid + 1))

    # не-админ не может менять тексты
    await h.click(uid, "txt:e:info")
    await h.text(uid, "взлом")
    assert "info" not in await db.get_setting("texts")


def test_templates_valid():
    """Все стандартные тексты проходят собственную проверку."""
    for key, d in content.TEXTS.items():
        errors, rendered = content.check_template(key, d.default)
        assert not errors and rendered, (key, errors)


async def scenario_settings(h: Harness) -> None:
    db, s, uid = h.db, h.s, 1301
    await h.text(uid, "/start")

    # имя автора из админки важнее, чем из .env
    await h.click(ADMIN, "set:menu")
    await h.click(ADMIN, "set:e:author_name")
    await h.text(ADMIN, "Мария")
    assert (await db.get_setting("profile"))["author_name"] == "Мария"
    assert "Меня зовут Мария" in (await h.section(uid, "info")).text

    # username поддержки: проверка формата, @ и ссылка t.me убираются
    await h.click(ADMIN, "set:e:manager_username")
    s.clear()
    await h.text(ADMIN, "@ab")
    assert "4–32" in last_text(s, ADMIN)
    await h.text(ADMIN, "https://t.me/new_support")
    s.clear()
    await h.click(uid, "f:offer")
    assert "@new_support" in s.texts(uid)[0]
    # вернуть значение из .env
    await h.click(ADMIN, "set:env:manager_username")
    s.clear()
    await h.click(uid, "f:offer")
    assert "@support_lite" in s.texts(uid)[0]

    # ссылку на соглашение убрали → ни кнопки, ни упоминания
    await h.click(ADMIN, "set:clear:agreement_url")
    assert [b.url for b in buttons_of(await h.section(uid, "info")) if b.url] == [PRIVACY]
    s.clear()
    await h.click(uid, "f:offer")
    offer = s.texts(uid)[0]
    assert AGREEMENT not in offer and PRIVACY in offer and "политикой конфиденциальности" in offer

    # ссылка на оплату из админки
    await h.click(ADMIN, "set:e:payment_url")
    await h.text(ADMIN, "не ссылка")
    assert "payment_url" not in await db.get_setting("profile")
    await h.text(ADMIN, "https://paydex.pro/pay/new")
    s.clear()
    await h.click(uid, "pay:sbp")
    assert buttons_of(s.of(SendMessage, uid)[0])[0].url == "https://paydex.pro/pay/new"


async def scenario_buttons(h: Harness) -> None:
    db, s, uid = h.db, h.s, 1401

    # раздел «Оформление»: группы → кнопки
    s.clear()
    await h.click(ADMIN, "adm:menu")
    assert "btn:menu" in [b.callback_data for b in buttons_of(s.screen(ADMIN))]
    await h.click(ADMIN, "btn:menu")
    assert "Оформление" in s.screen(ADMIN).text
    await h.click(ADMIN, "btn:g:nav")
    assert [b.callback_data for b in buttons_of(s.screen(ADMIN))][:2] == ["btn:b:menu_videos", "btn:b:menu_training"]

    # цвет группы «Воронка» → зелёный, кнопки «Меню» внизу → синий
    await h.click(ADMIN, "btn:gc:cta")
    await h.click(ADMIN, "btn:s:cta:success")
    await h.click(ADMIN, "btn:s:home:primary")
    await h.click(ADMIN, "btn:s:cta:purple")  # такого цвета в Telegram нет — игнорируем
    assert await db.get_setting("button_styles") == {"cta": "success", "home": "primary"}

    s.clear()
    await h.text(uid, "/start")
    welcome = buttons_of(s.of(SendMessage, uid)[0])
    assert welcome[0].style is None and welcome[1].style == "success"    # канал обычный, «Продолжить» зелёный
    await h.click(uid, "go:continue")
    hint = [r for r in s.of(SendMessage, uid) if r.text == content.MENU_HINT][0]
    assert all(b.style == "primary" for b in buttons_of(hint))
    s.clear()
    await h.click(uid, "f:offer")
    pay = buttons_of(s.of(SendMessage, uid)[0])
    assert pay[0].text == "💳 Оплатить по СБП" and pay[0].style is None   # группа «Оплата» не менялась

    # своя настройка кнопки важнее группы: «Показать» — красная, «Продолжить» — обычная
    await h.click(ADMIN, "btn:b:show")
    await h.click(ADMIN, "btn:c:show")
    await h.click(ADMIN, "btn:cs:show:danger")
    await h.click(ADMIN, "btn:cs:continue:plain")
    preview = buttons_of(s.screen(ADMIN))[0]   # первая кнопка экрана — пример, как её видят клиенты
    assert preview.style is None and preview.text == "➡️ Продолжить"
    s.clear()
    await h.click(uid, "go:continue")
    assert buttons_of([r for r in s.of(SendMessage, uid) if r.reply_markup and "Рад тебя" in r.text][0])[0].style == "danger"
    await h.click(ADMIN, "btn:cs:show:group")   # «как у группы»
    assert await db.get_setting("button_key_styles") == {"continue": "plain"}

    # текст кнопки: «Подробнее» вместо «Показать»; переименовали кнопку «Меню» — она работает по-новому и по-старому
    await h.click(ADMIN, "btn:t:show")
    await h.text(ADMIN, "многострочный\nтекст")
    assert "одной строкой" in last_text(s, ADMIN)
    await h.text(ADMIN, "🔎 Подробнее")
    s.clear()
    await h.click(uid, "go:continue")
    assert buttons_of([r for r in s.of(SendMessage, uid) if r.reply_markup and "Рад тебя" in r.text][0])[0].text == "🔎 Подробнее"
    await h.click(ADMIN, "btn:t:home")
    await h.text(ADMIN, "🏠 Домой")
    s.clear()
    await h.text(uid, "/start ticket_1")
    hint = [r for r in s.of(SendMessage, uid) if r.text == content.MENU_HINT][0]
    assert buttons_of(hint)[0].text == "🏠 Домой"
    for label in ("🏠 Домой", "Меню"):
        s.clear()
        await h.text(uid, label)
        assert s.texts(uid) == [content.MAIN_MENU], label
    await h.click(ADMIN, "btn:tr:show")
    assert await db.get_setting("button_texts") == {"home": "🏠 Домой"}

    # премиум-эмодзи на «Получить доступ»: присылаем эмодзи → бот проверяет и сохраняет
    await h.click(ADMIN, "btn:b:get_access")
    await h.click(ADMIN, "btn:i:get_access")
    await h.text(ADMIN, "просто текст")
    assert "button_icons" not in await db.get_settings(("button_icons",))
    s.clear()
    await h.rich(ADMIN, "🔥", [MessageEntity(type="custom_emoji", offset=0, length=2,
                                            custom_emoji_id="5368324170671202286")])
    sample = [r for r in s.of(SendMessage, ADMIN) if r.text == "Пример кнопки 👇"][0]
    assert buttons_of(sample)[0].icon_custom_emoji_id == "5368324170671202286"
    assert (await db.get_setting("button_icons")) == {"get_access": "5368324170671202286"}

    s.clear()
    await h.click(uid, "f:show")
    btn = buttons_of(s.of(SendMessage, uid)[0])[0]
    assert btn.text == "ПОЛУЧИТЬ ДОСТУП" and btn.icon_custom_emoji_id == "5368324170671202286"
    assert btn.style == "success"

    # ID числом тоже можно
    await h.click(ADMIN, "btn:i:paid")
    await h.text(ADMIN, "5368324170671202299")
    assert (await db.get_setting("button_icons"))["paid"] == "5368324170671202299"

    # без Premium у владельца бота: эмодзи не сохраняется, а уже заданные не ломают отправку
    s.reject_premium = True
    await h.click(ADMIN, "btn:i:promo")
    s.clear()
    await h.text(ADMIN, "5368324170671202311")
    assert "Не сохранено" in last_text(s, ADMIN) and "Premium" in last_text(s, ADMIN)
    assert "promo" not in await db.get_setting("button_icons")
    s.clear()
    await h.click(uid, "f:show")
    delivered = [r for r in s.of(SendMessage, uid) if not has_premium(r)]
    assert delivered and buttons_of(delivered[0])[0].icon_custom_emoji_id is None
    await h.click(ADMIN, "btn:menu")
    assert "не пропустил премиум-эмодзи" in [r.text for r in s.requests if isinstance(r, (SendMessage, EditMessageText))][-1]

    # премиум-эмодзи в тексте при отказе Telegram заменяются обычными
    s.reject_premium = False
    await h.click(ADMIN, "txt:e:main_menu")
    await h.rich(ADMIN, "🔥 Главное", [MessageEntity(type="custom_emoji", offset=0, length=2,
                                                    custom_emoji_id="5368324170671202286")])
    await h.click(ADMIN, "txt:s:main_menu")
    assert "<tg-emoji" in (await db.get_setting("texts"))["main_menu"]
    s.reject_premium = True
    s.clear()
    await h.click(uid, "menu:back")
    edits = [r for r in s.requests if isinstance(r, EditMessageText)]
    assert edits[-1].text == "🔥 Главное"

    # сброс цвета и эмодзи
    s.reject_premium = False
    await h.click(ADMIN, "btn:s:cta:none")
    await h.click(ADMIN, "btn:x:get_access")
    s.clear()
    await h.click(uid, "f:show")
    btn = buttons_of(s.of(SendMessage, uid)[0])[0]
    assert btn.text == "👑 ПОЛУЧИТЬ ДОСТУП" and btn.style is None and btn.icon_custom_emoji_id is None


async def scenario_no_links(h: Harness) -> None:
    """Ссылки (канал, группа, оплата, документы) и автор ещё не заданы — бот всё равно работает."""
    s, uid = h.s, 1501
    await h.text(uid, "/start")
    texts = s.texts(uid)
    assert any("Рад тебя видеть" in t for t in texts) and not any("{" in t for t in texts)
    s.clear()
    await h.click(uid, "f:offer")
    offer = s.of(SendMessage, uid)[0]
    assert "соглашением" not in offer.text and "{" not in offer.text
    assert "пиши мне в личные сообщения</i>" in offer.text
    assert [b.callback_data for b in buttons_of(offer)] == ["pay:sbp", "pay:promo"]
    s.clear()
    await h.click(uid, "pay:sbp")
    assert "Оплата скоро будет подключена" in s.texts(uid)[0]
    assert [b.callback_data for b in buttons_of(await h.section(uid, "info"))] == ["menu:back"]
    assert (await h.section(uid, "support")).text == content.SUPPORT_NOT_SET
    # покупка без закрытой группы → «пришлю ссылку позже», админу — предупреждение
    await h.click(uid, "pay:sent")
    s.clear()
    await h.click(ADMIN, "adm:pay_ok:1")
    assert content.ACCESS_MANUAL in s.texts(uid)
    assert any("Закрытая группа не настроена" in t for t in s.texts(ADMIN))


def test_text_edit():
    asyncio.run(with_db(scenario_text_edit))


def test_settings():
    asyncio.run(with_db(scenario_settings))


def test_buttons():
    asyncio.run(with_db(scenario_buttons))


def test_no_links():
    asyncio.run(with_db(scenario_no_links, channel_url="", private_channel_id=None, manager_username="",
                        payment_url="", payment_details="", author_name="", agreement_url="", privacy_url=""))


if __name__ == "__main__":
    test_purchase()
    test_payment_link()
    test_admin_retest()
    test_group_silence()
    test_cleanup()
    test_followup_videos()
    test_reject()
    test_browse_followups()
    test_quiet_hours()
    test_chat_setup()
    test_menu()
    test_tickets()
    test_exam()
    test_text_edit()
    test_templates_valid()
    test_settings()
    test_buttons()
    test_no_links()
    print("OK")
