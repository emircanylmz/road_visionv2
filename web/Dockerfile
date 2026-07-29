FROM python:3.11-slim

WORKDIR /srv/roadvision-web

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# --proxy-headers: nginx'in X-Forwarded-For/Proto başlıklarını uygular;
# güvenilen proxy adresleri FORWARDED_ALLOW_IPS ortam değişkeniyle sınırlanır
# (compose varsayılanı "*": api yalnız compose ağı + 127.0.0.1'e bağlıdır).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
