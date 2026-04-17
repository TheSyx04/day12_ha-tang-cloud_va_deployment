FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY 05-scaling-reliability/production/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY 05-scaling-reliability/production/app.py ./app.py
COPY 05-scaling-reliability/production/utils ./utils

EXPOSE 8000

CMD ["python", "app.py"]
