# RoadVision Web Paneli

Masaüstü uygulamanın PostgreSQL'e yazdığı günlük, tespit ve görüntüleri
sunan; tespit doğrulama ve dataset üretimini yöneten ayrı web servisi.
Tasarım sözleşmesi: [../WEB_PLANI.md](../WEB_PLANI.md). Bu klasör şu an
**Faz 0** kapsamını içerir: DB temeli, migration runner'ı ve API iskeleti.

## Kurulum — compose ile (önerilen)

```bash
# .env içine ROADVISION_WEB_PASSWORD ekleyin (bkz. .env.example)
docker compose up -d --build api

# Var olan (dolu) bir PostgreSQL volume'ünde web rolü bir kez elle kurulur;
# yeni volume'lerde initdb bunu otomatik yapar:
./web/scripts/bootstrap_db.sh

curl http://127.0.0.1:8800/healthz
```

## Kurulum — host üzerinde geliştirme

```bash
python3 -m venv web/.venv
source web/.venv/bin/activate
pip install -r web/requirements.txt

./web/scripts/bootstrap_db.sh
export ROADVISION_WEB_DSN="postgresql://roadvision_web:PAROLA@127.0.0.1:5433/roadvision"
uvicorn app.main:app --app-dir web --reload --port 8800
```

## Faz 0 kabul doğrulaması

Web rolünün `public` şemasını okuyup **yazamadığını** makinede kanıtlar:

```bash
ROADVISION_WEB_DSN=... python3 web/scripts/verify_foundation.py
```

## Testler

Migration runner testleri psycopg/torch gerektirmez:

```bash
python3 -m unittest discover -s web/tests -t web -v
```

## Güvenlik notları

- `ROADVISION_WEB_DSN` yalnız `roadvision_web` (salt-okunur public)
  rolünü taşır; masaüstünün sahip DSN'i web servisine verilmez.
- Parolalar `.env` dosyasındadır ve komut satırına yazılmaz;
  `bootstrap_db.sh` parolayı psql değişkeni olarak aktarır.
- API konteyneri yalnız `127.0.0.1:8800`e yayınlanır; dış erişim Faz 2'de
  nginx + TLS ile açılacaktır.
