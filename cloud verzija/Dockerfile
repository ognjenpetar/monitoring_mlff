FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper.py notifier.py service.py stats.py alerts.py reports.py telegram_poll.py ./

CMD ["python", "-u", "service.py"]
