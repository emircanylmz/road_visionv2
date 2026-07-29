# RoadVision Web Paneli

Masaüstü uygulamanın PostgreSQL'e yazdığı günlük, tespit ve görüntüleri
sunan; tespit doğrulama ve dataset üretimini yöneten ayrı web servisi.
Tasarım sözleşmesi: [../WEB_PLANI.md](../WEB_PLANI.md). Bu klasör şu an
**Faz 0 + Faz 1** kapsamını içerir: DB temeli, migration runner'ı,
kimlik/oturum katmanı ve yönetici onay akışı.

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

## Faz 1 — kimlik ve yönetici

İlk yönetici (parola etkileşimli sorulur; komut satırına yazılmaz):

```bash
export ROADVISION_WEB_DSN="postgresql://roadvision_web:PAROLA@127.0.0.1:5433/roadvision"
python3 web/scripts/create_admin.py admin@kurum.tr --full-name "Saha Yöneticisi"
```

Uç özeti (tam sözleşme: WEB_PLANI.md §6): `POST /api/auth/register|login|logout`,
`GET /api/auth/me`; yönetici: `GET /api/admin/users?status=`,
`POST /api/admin/users/{id}/approve|reject|disable`,
`GET /api/admin/sessions`, `DELETE /api/admin/sessions/{id}`,
`GET /api/admin/audit`.
Durum değiştiren istekler `rv_csrf` çerezindeki değeri
`X-RoadVision-CSRF` başlığında geri göndermelidir.

Faz 1 kabulü (çalışan API + yönetici hesabı gerekir):

```bash
ROADVISION_WEB_ADMIN_EMAIL=admin@kurum.tr \
ROADVISION_WEB_ADMIN_PASSWORD=... \
python3 web/scripts/verify_faz1.py
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
