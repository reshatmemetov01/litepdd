import json
import random
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile

from .. import content, keyboards
from ..db import Database
from ..questions import Question, QuestionBank

router = Router()

CAPTION_LIMIT = 1024
# file_id уже загруженных картинок, чтобы не отправлять файл каждый раз заново
_photo_cache: dict[str, str] = {}


def question_text(q: Question, index: int, total: int) -> str:
    options = "\n".join(f"{i + 1}. {escape(a)}" for i, a in enumerate(q.answers))
    return f"<b>Вопрос {index + 1} из {total}</b>\n\n{escape(q.question)}\n\n{options}"


async def send_question(bot: Bot, chat_id: int, q: Question, index: int, total: int) -> None:
    text = question_text(q, index, total)
    markup = keyboards.exam_answers(index, len(q.answers))
    if not q.image:
        await bot.send_message(chat_id, text, reply_markup=markup)
        return

    key = str(q.image)
    photo = _photo_cache.get(key) or FSInputFile(q.image)
    if len(text) <= CAPTION_LIMIT:
        sent = await bot.send_photo(chat_id, photo, caption=text, reply_markup=markup)
    else:
        sent = await bot.send_photo(chat_id, photo)
        await bot.send_message(chat_id, text, reply_markup=markup)
    if sent.photo:
        _photo_cache[key] = sent.photo[-1].file_id


async def start_session(bot: Bot, db: Database, user_id: int, picked: list[Question], mode: str) -> None:
    ids = [q.id for q in picked]
    await db.update_user(user_id, exam_ids=json.dumps(ids), exam_mode=mode,
                         exam_index=0, exam_correct=0, exam_errors=0)
    await send_question(bot, user_id, picked[0], 0, len(ids))


async def start_ticket(bot: Bot, db: Database, user_id: int, questions: QuestionBank, number: int) -> bool:
    picked = questions.ticket(number)
    if not picked:
        return False
    await db.add_event(user_id, "ticket_start")
    await start_session(bot, db, user_id, picked, f"ticket:{number}")
    return True


@router.callback_query(F.data == "ex:start")
async def exam_start(callback: CallbackQuery, bot: Bot, db: Database, user: dict,
                     questions: QuestionBank) -> None:
    await callback.answer()
    if not questions.questions:
        await callback.message.answer(content.EXAM_NO_QUESTIONS)
        return
    picked = random.sample(questions.questions, min(content.EXAM_QUESTIONS, len(questions.questions)))
    await db.add_event(user["user_id"], "exam_start")
    await callback.message.edit_reply_markup(reply_markup=None)
    await start_session(bot, db, user["user_id"], picked, "exam")


@router.callback_query(F.data == "tk:list")
async def ticket_list(callback: CallbackQuery, questions: QuestionBank) -> None:
    await callback.answer()
    numbers = questions.ticket_numbers()
    if not numbers:
        await callback.message.answer(content.EXAM_NO_QUESTIONS)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(content.TICKETS_CHOOSE, reply_markup=keyboards.tickets(numbers))


@router.callback_query(F.data.regexp(r"^tk:\d+$"))
async def ticket_start(callback: CallbackQuery, bot: Bot, db: Database, user: dict,
                       questions: QuestionBank) -> None:
    await callback.answer()
    if not await start_ticket(bot, db, user["user_id"], questions, int(callback.data.split(":")[1])):
        await callback.message.answer(content.TICKET_NOT_FOUND, reply_markup=keyboards.tickets(questions.ticket_numbers()))
        return
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.regexp(r"^ex:\d+:\d+$"))
async def exam_answer(callback: CallbackQuery, bot: Bot, db: Database, user: dict,
                      questions: QuestionBank) -> None:
    _, index_s, answer_s = callback.data.split(":")
    index, answer = int(index_s), int(answer_s)
    ids = json.loads(user["exam_ids"] or "[]")
    # Защита от повторных нажатий и кнопок из старого экзамена
    if index != user["exam_index"] or index >= len(ids) or ids[index] not in questions.by_id:
        await callback.answer("На этот вопрос ты уже ответил 🙂")
        return
    await callback.answer()

    q = questions.by_id[ids[index]]
    is_correct = answer == q.correct
    correct = user["exam_correct"] + int(is_correct)
    errors = user["exam_errors"] + int(not is_correct)
    await db.update_user(user["user_id"], exam_index=index + 1, exam_correct=correct, exam_errors=errors)

    if is_correct:
        verdict = "✅ <b>Верно!</b>"
    else:
        verdict = f"❌ <b>Неверно.</b> Правильный ответ: {q.correct + 1}. {escape(q.answers[q.correct])}"
    if q.hint:
        verdict += f"\n<i>{escape(q.hint)}</i>"

    # Показываем результат в том же сообщении, а если не влезает — отдельным
    msg = callback.message
    current = msg.html_text if (msg.text or msg.caption) else ""
    new_text = f"{current}\n\n{verdict}" if current else verdict
    try:
        if msg.photo:
            if len(new_text) > CAPTION_LIMIT:
                raise ValueError("caption too long")
            await msg.edit_caption(caption=new_text, reply_markup=None)
        else:
            await msg.edit_text(new_text, reply_markup=None)
    except Exception:
        await msg.edit_reply_markup(reply_markup=None)
        await msg.answer(verdict)

    uid = user["user_id"]
    answered = index + 1
    mode = user.get("exam_mode") or "exam"
    if mode.startswith("ticket:"):
        if answered < len(ids):
            await send_question(bot, uid, questions.by_id[ids[answered]], answered, len(ids))
            return
        ticket = int(mode.split(":")[1])
        await db.add_event(uid, "ticket_done")
        good = errors <= 2
        show_offer = not good and not user["paid"]
        verdict = content.t("ticket_verdict_good" if good
                            else "ticket_verdict_bad_unpaid" if show_offer else "ticket_verdict_bad")
        await bot.send_message(
            uid, content.TICKET_RESULT.format(ticket=ticket, correct=correct, total=len(ids),
                                              errors=errors, verdict=verdict),
            reply_markup=keyboards.ticket_finish(ticket, show_offer),
        )
        return

    if errors >= content.EXAM_MAX_ERRORS:
        await db.add_event(uid, "exam_fail")
        await bot.send_message(
            uid, content.t("exam_fail_paid" if user["paid"] else "exam_fail", errors=errors, answered=answered),
            reply_markup=keyboards.exam_finish(failed=True, paid=bool(user["paid"])),
        )
    elif answered == len(ids):
        await db.add_event(uid, "exam_pass")
        await bot.send_message(
            uid, content.t("exam_pass", correct=correct, total=len(ids), errors=errors),
            reply_markup=keyboards.exam_finish(failed=False, paid=bool(user["paid"])),
        )
    else:
        await send_question(bot, uid, questions.by_id[ids[answered]], answered, len(ids))
