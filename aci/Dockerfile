FROM python:3.11

WORKDIR /app

# Only copy requirements first so Docker can cache pip install step
COPY requirements.txt ./
COPY constraints.txt ./

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt -c constraints.txt --use-deprecated=legacy-resolver && \
    pip install pygooglenews --no-deps

RUN apt-get update && apt-get install -y libmagic1

COPY . ./aci/
COPY alembic.ini .
COPY .env .
COPY start.sh .

RUN python -m aci.voice_agent.agent download-files

RUN crawl4ai-setup

CMD ["uvicorn", "aci.server.main:app", "--proxy-headers", "--forwarded-allow-ips=*", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
