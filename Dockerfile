FROM node:22-bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PUBLIC_MODE=1 WORKSPACE_DIR=/data/workspaces
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv ffmpeg chromium fonts-noto-cjk tini && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN npm install -g hyperframes
COPY app.py ./
RUN mkdir -p /data/workspaces && chown -R node:node /app /data
USER node
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
