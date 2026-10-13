"""Загрузка базы вопросов для тренажёра.

Формат data/questions.json — список объектов:
    {
      "id": 1,                     # уникальный номер вопроса
      "ticket": 1,                 # номер билета (необязательно)
      "question": "Текст вопроса",
      "image": "images/1_1.jpg",   # путь к картинке относительно файла с вопросами, или null
      "answers": ["Вариант 1", "Вариант 2", "Вариант 3"],
      "correct": 0,                # индекс правильного ответа, с нуля
      "hint": "Пояснение"          # необязательно
    }
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Question:
    id: int
    question: str
    answers: tuple[str, ...]
    correct: int
    image: Path | None = None
    hint: str = ""
    ticket: int | None = None


class QuestionBank:
    def __init__(self, questions: list[Question]):
        self.questions = questions
        self.by_id = {q.id: q for q in questions}

    def ticket_numbers(self) -> list[int]:
        return sorted({q.ticket for q in self.questions if q.ticket})

    def ticket(self, number: int) -> list[Question]:
        """Вопросы билета по порядку (id = билет * 100 + номер вопроса)."""
        return sorted((q for q in self.questions if q.ticket == number), key=lambda q: q.id)

    @classmethod
    def load(cls, path: str) -> "QuestionBank":
        file = Path(path)
        if not file.exists():
            log.warning("Файл с вопросами %s не найден — тренажёр отключён", path)
            return cls([])
        raw = json.loads(file.read_text(encoding="utf-8"))
        questions = []
        for item in raw:
            image = file.parent / item["image"] if item.get("image") else None
            if image and not image.exists():
                log.warning("Нет картинки %s для вопроса %s", image, item["id"])
                image = None
            q = Question(
                id=int(item["id"]),
                question=item["question"].strip(),
                answers=tuple(a.strip() for a in item["answers"]),
                correct=int(item["correct"]),
                image=image,
                hint=(item.get("hint") or "").strip(),
                ticket=item.get("ticket"),
            )
            if not 0 <= q.correct < len(q.answers):
                raise ValueError(f"Вопрос {q.id}: неверный индекс правильного ответа")
            questions.append(q)
        log.info("Загружено вопросов: %d", len(questions))
        return cls(questions)
