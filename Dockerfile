FROM node:22-slim AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
# Vite writes the bundle to ../agent_platform/static
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY agent_platform ./agent_platform
COPY --from=ui /src/agent_platform/static ./agent_platform/static

EXPOSE 8000
ENTRYPOINT ["python", "app.py"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
