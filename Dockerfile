FROM alpine:3.23
RUN apk add --no-cache bash curl ffmpeg python3
# yt-dlp straight from its releases: distro builds lag and YouTube breaks old versions.
# ADD re-downloads on every build, so `make deploy` also updates it.
ADD --chmod=755 https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp /usr/local/bin/yt-dlp
COPY unplayer /usr/local/bin/unplayer
COPY web/server.py /app/server.py
EXPOSE 8080
CMD ["python3", "-u", "/app/server.py"]
