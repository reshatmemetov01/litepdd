#!/usr/bin/env bash
# Обновить «Лайт ПДД» до последней версии с GitHub:  sudo bash /opt/litepdd-bot/deploy/update.sh
# Трогает только этого бота (папка /opt/litepdd-bot, сервис litepdd-bot) — другие боты на сервере не затрагиваются.
set -euo pipefail

DIR="/opt/litepdd-bot"
SERVICE="litepdd-bot"
BOT_USER="litepddbot"

[[ $EUID -eq 0 ]] || { echo "Запусти от root: sudo bash $0"; exit 1; }

BRANCH="$(git -c safe.directory="$DIR" -C "$DIR" rev-parse --abbrev-ref HEAD)"

echo "==> Скачиваю обновления ($BRANCH)"
git -c safe.directory="$DIR" -C "$DIR" fetch -q origin "$BRANCH"
OLD="$(git -c safe.directory="$DIR" -C "$DIR" rev-parse --short HEAD)"
git -c safe.directory="$DIR" -C "$DIR" reset -q --hard "origin/$BRANCH"
NEW="$(git -c safe.directory="$DIR" -C "$DIR" rev-parse --short HEAD)"

"$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
chown -R "$BOT_USER:$BOT_USER" "$DIR"

systemctl restart "$SERVICE"
sleep 3
if systemctl is-active -q "$SERVICE"; then
  [[ "$OLD" == "$NEW" ]] && echo "✅ Обновлений не было, бот перезапущен." || echo "✅ Обновлено ($OLD → $NEW), бот перезапущен."
else
  echo "❌ Бот не запустился после обновления:"
  journalctl -u "$SERVICE" -n 30 --no-pager
  exit 1
fi
