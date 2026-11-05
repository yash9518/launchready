# LaunchReady — container image for hosting (Hugging Face Spaces, Render, Railway, any Docker host).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

# OpenCV (pulled in by RapidOCR) needs these system libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs containers as user 1000.
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY --chown=user requirements-lock.txt requirements.txt ./
RUN pip install -r requirements-lock.txt

COPY --chown=user . .
RUN mkdir -p .runs .cache && chown -R user:user /home/user/app

USER user
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
