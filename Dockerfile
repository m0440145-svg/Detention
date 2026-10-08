FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN useradd --create-home --uid 10001 app && mkdir -p media staticfiles && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["sh", "deploy/start.sh"]
