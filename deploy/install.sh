#!/usr/bin/env bash
# Установка бота «Лайт ПДД» на VPS (Ubuntu 22.04+ / Debian 12+) одной командой:
#   curl -fsSL https://raw.githubusercontent.com/reshatmemetov01/litepdd/claude/litepdd-bot/deploy/install.sh | sudo bash
#
# Если репозиторий приватный — нужен GitHub-токен с правом чтения (Fine-grained token → Contents: Read):
#   T=ghp_...; curl -fsSL -H "Authorization: token $T" \
#     https://raw.githubusercontent.com/reshatmemetov01/litepdd/claude/litepdd-bot/deploy/install.sh | sudo GITHUB_TOKEN=$T bash
#
# Можно ставить на тот же сервер, где уже работает другой бот (например, /opt/pdd-bot):
# у «Лайт ПДД» своя папка, свой systemd-сервис и свой системный пользователь — боты друг другу не мешают.
# Повторный запуск безопасен: обновит код и перезапустит бота, настройки (.env) не трогает.
set -euo pipefail

REPO="${REPO:-https://github.com/reshatmemetov01/litepdd.git}"
BRANCH="${BRANCH:-claude/litepdd-bot}"
DIR="/opt/litepdd-bot"
SERVICE="litepdd-bot"
BOT_USER="litepddbot"

if [[ $EUID -ne 0 ]]; then
  echo "Запусти от root: sudo bash install.sh"; exit 1
fi

ask() {  # ask "Вопрос" "значение по умолчанию" → ответ
  local answer
  read -r -p "$1${2:+ [$2]}: " answer < /dev/tty
  echo "${answer:-$2}"
}

echo "==> Устанавливаю Python и git"
apt-get update -qq
apt-get install -y -qq python3 python3-venv git > /dev/null

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Нужен Python 3.10 или новее (сейчас: $(python3 --version)). Поставь Ubuntu 22.04+ или Debian 12+."
  exit 1
fi

id "$BOT_USER" &> /dev/null || useradd --system --home "$DIR" --shell /usr/sbin/nologin "$BOT_USER"

echo "==> Скачиваю код ($REPO, ветка $BRANCH)"
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  # Приватный репозиторий: токен сохраняется в $DIR/.git/config (доступен только root и боту),
  # чтобы потом работал update.sh
  REPO="https://x-access-token:${GITHUB_TOKEN}@${REPO#https://}"
  if [[ -d "$DIR/.git" ]]; then
    git -c safe.directory="$DIR" -C "$DIR" remote set-url origin "$REPO"
  fi
fi
if [[ -d "$DIR/.git" ]]; then
  git -c safe.directory="$DIR" -C "$DIR" fetch -q origin "$BRANCH"
  git -c safe.directory="$DIR" -C "$DIR" checkout -q -B "$BRANCH" "origin/$BRANCH"
  git -c safe.directory="$DIR" -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  mkdir -p "$DIR"
  git clone -q --branch "$BRANCH" "$REPO" "$DIR"
fi

echo "==> Ставлю зависимости"
[[ -d "$DIR/venv" ]] || python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q --upgrade pip
"$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"

if [[ ! -f "$DIR/.env" ]]; then
  echo
  echo "==> Настройка «Лайт ПДД» (Enter — оставить значение в скобках)"
  echo "    Нужен ОТДЕЛЬНЫЙ бот от @BotFather: один токен нельзя запускать в двух местах."
  while true; do
    BOT_TOKEN=$(ask "Токен бота от @BotFather" "")
    if [[ -z "$BOT_TOKEN" ]]; then
      echo "Токен обязателен."
    elif grep -qsxF "BOT_TOKEN=$BOT_TOKEN" /opt/*/.env; then
      echo "Этот токен уже используется другим ботом на этом сервере ($(grep -lsxF "BOT_TOKEN=$BOT_TOKEN" /opt/*/.env | head -1))."
      echo "Создай для «Лайт ПДД» нового бота в @BotFather и вставь его токен."
    else
      break
    fi
  done
  ADMIN_IDS=$(ask "Telegram ID админов через запятую (узнать: @userinfobot)" "")
  while [[ ! "$ADMIN_IDS" =~ ^[0-9]+([[:space:]]*,[[:space:]]*[0-9]+)*$ ]]; do
    ADMIN_IDS=$(ask "Нужен числовой ID (например 123456789)" "")
  done
  BOT_NAME=$(ask "Название бота" "Лайт ПДД")
  AUTHOR_NAME=$(ask "Имя автора (в текстах «о себе» и «Информация»; можно позже в админке)" "")
  MANAGER=$(ask "Username поддержки, без @ (можно позже в админке)" "")
  cat > "$DIR/.env" <<ENV
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
BOT_NAME=$BOT_NAME
AUTHOR_NAME=$AUTHOR_NAME
MANAGER_USERNAME=${MANAGER#@}
CHANNEL_URL=
PRIVATE_CHANNEL_ID=
ACCESS_LINK=
PAYMENT_URL=
PAYMENT_DETAILS=
AGREEMENT_URL=
PRIVACY_URL=
PRICE=1490
OLD_PRICE=
DB_PATH=$DIR/litepdd_bot.db
QUESTIONS_PATH=data/questions.json
TIMEZONE=Europe/Moscow
FOLLOWUP_DELAY_SCALE=1
ENV
  echo "Настройки сохранены в $DIR/.env (поменять: sudo nano $DIR/.env)"
fi

chown -R "$BOT_USER:$BOT_USER" "$DIR"
chmod 600 "$DIR/.env"
chmod 700 "$DIR/.git"

echo "==> Включаю автозапуск"
sed -e "s|/opt/litepdd-bot|$DIR|g" -e "s|User=litepddbot|User=$BOT_USER|" "$DIR/deploy/$SERVICE.service" \
  > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable -q "$SERVICE"
systemctl restart "$SERVICE"
sleep 3

if systemctl is-active -q "$SERVICE"; then
  echo
  echo "✅ «Лайт ПДД» запущен и будет сам стартовать после перезагрузки сервера."
  echo "   Логи:        sudo journalctl -u $SERVICE -f"
  echo "   Обновление:  sudo bash $DIR/deploy/update.sh"
else
  echo "❌ Бот не запустился. Последние строки лога:"
  journalctl -u "$SERVICE" -n 30 --no-pager
  exit 1
fi
