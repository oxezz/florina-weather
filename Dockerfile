# Καιρός · Φλώρινα — container image
#
# The application is pure standard library, so there is nothing to install
# except the IANA timezone database: slim Python images ship without it, and
# without it zoneinfo falls back to the UTC offset reported by Open-Meteo.
# One tiny package keeps DST transitions inside the 7-day forecast correct.
FROM python:3.13-slim

RUN pip install --no-cache-dir tzdata

WORKDIR /app

# Only the files the server actually serves.
COPY app.py greek.py report.py sources.py ./
COPY template.html style.css app.js theme.js favicon.svg ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLORINA_HOST=0.0.0.0 \
    FLORINA_PORT=8000

# Platforms such as Render and Fly inject PORT; app.py honours it as a fallback.
EXPOSE 8000

RUN useradd --create-home --uid 1000 florina
USER florina

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('FLORINA_PORT', os.environ.get('PORT','8000'))+'/api/health', timeout=4)"

CMD ["python", "app.py"]
