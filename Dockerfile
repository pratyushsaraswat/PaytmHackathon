FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY static ./static
WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# Render/Railway/Fly inject $PORT; fall back to 8000 locally
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
