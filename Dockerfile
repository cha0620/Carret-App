# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# ① 의존성 먼저 (레이어 캐시 핵심: 코드만 바뀌면 재설치 skip)
COPY requirements.txt .
# torch/torchvision을 CPU 전용 wheel로 먼저 깔아둔다 — 이 서버엔 GPU가 없는데
# 기본 PyPI index로 받으면 CUDA 런타임(수GB)까지 딸려온다. requirements.txt에
# 이미 같은 버전 제약이 있어서, 아래 설치가 끝난 뒤 -r requirements.txt는
# "이미 만족됨"으로 스킵한다.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# ② 코드 복사
COPY backend/ ./backend/

# ③ 실행 환경 (로컬 venv 와 동일 조건)
WORKDIR /app/backend
ENV PYTHONPATH=/app/backend
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
