FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
COPY data ./data
# База хранится в томе, чтобы не терялась при пересборке
ENV DB_PATH=/db/litepdd_bot.db
VOLUME /db
CMD ["python", "-m", "bot"]
