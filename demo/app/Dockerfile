# Dependencies only. The application code is mounted read-only at /app from the generated
# git repository (demo/democtl.py), so injecting a fault is a real commit plus a restart.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && useradd --uid 10001 --create-home shop
USER 10001
WORKDIR /app
EXPOSE 8000
CMD ["uvicorn", "shop.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
