FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Tokyo \
    STOCKALERT_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data
EXPOSE 8000
VOLUME ["/app/data"]

CMD ["python3", "run.py"]
