FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    CATVTON_PROJECT_DIR=/opt/CatVTON \
    PORT=8000 \
    PORT_HEALTH=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-catvton.txt ./
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r requirements-catvton.txt

RUN git clone https://github.com/Zheng-Chong/CatVTON.git /opt/CatVTON && \
    cd /opt/CatVTON && \
    git checkout 7818397f25613beedb3d861a34769f607cfcf3b1

COPY . .

RUN chmod +x /app/start-server.sh

EXPOSE 8000

CMD ["/app/start-server.sh"]
