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

# ①-2 DINOv2 가중치를 이미지에 미리 (11-15) — 배포 서버가 첫 요청 때 Hugging Face 에서 받느라 기다리지 않게,
#       받기가 실패해도 서버가 뜨게. 약 90MB
ENV HF_HOME=/opt/hf
RUN python -c "from transformers import AutoImageProcessor, AutoModel; \
AutoImageProcessor.from_pretrained('facebook/dinov2-small'); AutoModel.from_pretrained('facebook/dinov2-small')"
# 받아 둔 뒤로는 실행 중에 Hugging Face 에 묻지 않는다
ENV HF_HUB_OFFLINE=1

# ② 코드 복사 — 화면(frontend/)도 같이: main.py 가 ../frontend 를 / 에 마운트한다 (없으면 API 만 뜬다)
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# ③ 실행 환경 (로컬 venv 와 동일 조건)
WORKDIR /app/backend
ENV PYTHONPATH=/app/backend
EXPOSE 8000

# --proxy-headers 는 일부러 안 쓴다: 모든 출처를 믿으면 uvicorn 이 client.host 를 x-forwarded-for 맨 왼쪽(위조 가능)으로
# 바꿔 IP 상한이 뚫린다. 사용자 IP 는 quota.client_ip 가 cf-connecting-ip 로 읽는다 (11-15 리뷰)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
