from aiogram import Router

from . import admin, admin_content, chats, exam, fallback, funnel, menu, start


def setup_routers() -> Router:
    root = Router()
    # Порядок важен: /start и кнопки меню срабатывают в любом состоянии,
    # fallback — последним.
    root.include_routers(chats.router, start.router, menu.router, admin.router, admin_content.router, funnel.router, exam.router, fallback.router)
    return root
