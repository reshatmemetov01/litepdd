"""Конвертирует билеты из текстового формата в data/questions.json + data/images/.

Формат билета (raw/biletN.txt):
    Вопрос 1
    Картинка: 2.jpg            ← необязательно
    Текст вопроса
    1. Вариант
    2. Вариант
    Ответ: 2
    Пояснение: ...

Картинки билета N лежат в raw/kartinkiN/.

Запуск:  python tools/import_tickets.py            (все найденные билеты)
         python tools/import_tickets.py --check    (только проверка, без записи)
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT_JSON = ROOT / "data" / "questions.json"
OUT_IMAGES = ROOT / "data" / "images"

Q_HEADER = re.compile(r"^Вопрос\s+(\d+)\s*$")
OPTION = re.compile(r"^(\d+)[.)]\s*(.+)$")


def parse_ticket(path: Path) -> list[dict]:
    blocks, current = [], None
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        m = Q_HEADER.match(line)
        if m:
            current = {"num": int(m.group(1)), "lines": []}
            blocks.append(current)
        elif current is not None:
            current["lines"].append(line)

    questions = []
    for b in blocks:
        image, text, options, answer, hint = None, [], [], None, []
        in_hint = False
        for line in b["lines"]:
            if in_hint:
                if line:
                    hint.append(line)
                continue
            if not line:
                continue
            if line.startswith("Картинка:"):
                image = line.split(":", 1)[1].strip() or None
            elif line.startswith("Ответ:"):
                answer = int(re.findall(r"\d+", line)[0])
            elif line.startswith("Пояснение:"):
                in_hint = True
                rest = line.split(":", 1)[1].strip()
                if rest:
                    hint.append(rest)
            elif (m := OPTION.match(line)) and answer is None and int(m.group(1)) == len(options) + 1:
                options.append(m.group(2).strip())
            elif not options:
                text.append(line)
            else:
                options[-1] += " " + line  # перенос строки внутри варианта
        questions.append({
            "num": b["num"], "image": image, "question": " ".join(text),
            "answers": options, "answer": answer, "hint": re.sub(r"\s+", " ", " ".join(hint)).strip(),
        })
    return questions


def image_dir(ticket: int) -> Path | None:
    path = RAW / f"kartinki{ticket}"
    return path if path.is_dir() else None


def main() -> int:
    check_only = "--check" in sys.argv
    tickets = sorted(int(m.group(1)) for p in RAW.glob("bilet*.txt") if (m := re.match(r"bilet(\d+)$", p.stem)))
    result, problems = [], []
    skipped = []
    for t in tickets:
        qs = parse_ticket(RAW / f"bilet{t}.txt")
        imgs = image_dir(t)
        missing = [q["image"] for q in qs if q["image"] and not (imgs and (imgs / q["image"]).exists())]
        if missing:
            skipped.append(t)
            problems.append(f"билет {t} пропущен — нет картинок: {', '.join(missing)}")
            continue
        if len(qs) != 20:
            problems.append(f"билет {t}: {len(qs)} вопросов вместо 20")
        for q in qs:
            where = f"билет {t}, вопрос {q['num']}"
            if not q["question"]:
                problems.append(f"{where}: нет текста вопроса")
            if len(q["answers"]) < 2:
                problems.append(f"{where}: меньше 2 вариантов")
            if q["answer"] is None or not 1 <= q["answer"] <= len(q["answers"]):
                problems.append(f"{where}: ответ {q['answer']} вне вариантов (их {len(q['answers'])})")
            image_rel = None
            if q["image"]:
                src = imgs / q["image"]
                if True:
                    image_rel = f"images/{t}_{q['num']}{src.suffix.lower()}"
                    if not check_only:
                        OUT_IMAGES.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, ROOT / "data" / image_rel)
            result.append({
                "id": t * 100 + q["num"], "ticket": t, "number": q["num"], "image": image_rel,
                "question": q["question"], "answers": q["answers"],
                "correct": (q["answer"] or 1) - 1, "hint": q["hint"],
            })
        used = {q["image"] for q in qs if q["image"]}
        if imgs:
            unused = sorted(p.name for p in imgs.iterdir() if p.name not in used)
            if unused:
                problems.append(f"билет {t}: лишние картинки, не упомянутые в вопросах: {', '.join(unused)}")

    print(f"Билетов: {len(tickets) - len(skipped)}, вопросов: {len(result)}, с картинками: {sum(1 for q in result if q['image'])}")
    for p in problems:
        print("⚠️ ", p)
    if not check_only:
        OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Записано: {OUT_JSON.relative_to(ROOT)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
