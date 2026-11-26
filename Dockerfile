# Runs the bot on Linux (x86_64 or ARM64).
# Build context is the DiscordBot folder:
#   docker build -f Dockerfile DiscordBot
FROM python:3.14-slim-trixie

# yt-dlp needs a JavaScript runtime to read YouTube's player code. Deno is the first one MyBot.py asks for.
COPY --from=denoland/deno:bin-2.5.6 /deno /usr/local/bin/deno

# ffmpeg streams audio into voice. libgl1 and libglib2.0 are needed by OpenCV.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0t64 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FACE_MODEL_PATH=/data/models/face_detection_yunet_2023mar.onnx

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fail the build early if a native library is missing on this architecture.
RUN python -c "import cv2, discord, nacl, yt_dlp; cv2.FaceDetectorYN; print('imports ok')" \
    && deno --version \
    && ffmpeg -version | head -n 1

COPY MyBot.py .

# Run as a normal user. /data holds the face model so it survives container restarts.
RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /data/models \
    && chown -R bot:bot /data
USER bot
VOLUME ["/data"]

CMD ["python", "MyBot.py"]
