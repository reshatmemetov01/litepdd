"""Настройки из админки, которые действуют прямо сейчас: тексты, цвета и эмодзи кнопок, имена.

Загружаются из базы на каждое обновление (см. middleware.py) и на каждый проход
дожимающих сообщений, поэтому правки из админки применяются сразу, без перезапуска.
Хранятся в таблице settings:
    texts          — {ключ текста: шаблон}       (значения по умолчанию — в content.py)
    button_styles  — {группа кнопок: стиль}      (primary / success / danger)
    button_icons   — {кнопка: id премиум-эмодзи}
    profile        — {поле Config: значение}     (имя бота/автора, поддержка, ссылки)
"""
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

from .config import EDITABLE_FIELDS, Config
from .db import Database

SETTING_KEYS = ("texts", "button_styles", "button_icons", "profile")


@dataclass(frozen=True)
class Runtime:
    texts: dict[str, str] = field(default_factory=dict)
    styles: dict[str, str] = field(default_factory=dict)
    icons: dict[str, str] = field(default_factory=dict)
    # Плейсхолдеры, доступные во всех текстах
    globals: dict[str, str] = field(default_factory=dict)


_current: ContextVar[Runtime] = ContextVar("runtime", default=Runtime())


def current() -> Runtime:
    return _current.get()


def activate(rt: Runtime) -> None:
    _current.set(rt)


def effective_config(config: Config, profile: dict) -> Config:
    """Config с учётом значений из админки («⚙️ Настройки»)."""
    overrides = {k: v for k, v in (profile or {}).items() if k in EDITABLE_FIELDS}
    return replace(config, **overrides) if overrides else config


def globals_for(config: Config) -> dict[str, str]:
    return {"bot_name": config.bot_name, "author": config.author_name}


async def load(db: Database, config: Config) -> tuple[Runtime, Config]:
    """Читает настройки из базы, делает их текущими и возвращает (runtime, итоговый config)."""
    saved = await db.get_settings(SETTING_KEYS)
    cfg = effective_config(config, saved.get("profile") or {})
    rt = Runtime(
        texts=saved.get("texts") or {},
        styles=saved.get("button_styles") or {},
        icons=saved.get("button_icons") or {},
        globals=globals_for(cfg),
    )
    activate(rt)
    return rt, cfg
