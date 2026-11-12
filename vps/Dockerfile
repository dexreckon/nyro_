FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --no-create-home nyro && mkdir /data && chown nyro /data
WORKDIR /app
COPY vps/requirements.txt /app/vps/requirements.txt
RUN pip install --no-cache-dir -r vps/requirements.txt
COPY vps /app/vps
COPY pwa /app/pwa

USER nyro
ENV NYRO_DB=/data/relay.sqlite3 NYRO_PWA_DIR=/app/pwa
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"
# one worker: the hub keeps live sockets in memory
CMD ["uvicorn", "vps.relay:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "524288"]
