# RoadVision Web Paneli

Masaüstü uygulamanın PostgreSQL'e yazdığı günlük, tespit ve görüntüleri
sunan; tespit doğrulama ve dataset üretimini yöneten ayrı web servisi.
Tasarım sözleşmesi: [../WEB_PLANI.md](../WEB_PLANI.md). Bu klasör şu an
**Faz 0–5** kapsamını içerir: DB temeli, migration runner'ı, kimlik/
oturum katmanı, yönetici onay akışı, log ve tespit arşivi API'leri,
doğrulama + copy-on-verify dataset katmanı ve nginx arkasında sunulan
React SPA.

## Kurulum — compose ile (önerilen)

```bash
# .env içine ROADVISION_WEB_PASSWORD ekleyin (bkz. .env.example)
docker compose up -d --build api frontend

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

## Faz 5 — dataset export ve istatistik

Panelde **Dataset** sekmesi: model × tür × karar kırılımı, YOLO export
işleri ve indirme. Export arka plan işidir; zip **veritabanında** saklanır
ve `GET /api/datasets/exports/{id}/download` ile indirilir. Pozitif kapsam
`final_*` etiketleriyle normalize YOLO etiketi üretir; `wrong` kapsamı boş
etiketli hard-negative/background görüntüleri verir. Aynı model + kapsam
için iş bitmeden ikinci istek 409 döner. İş başına en çok 5000 kare
zip'e girer (aşan kısım manifest'te işaretlenir).

Faz 5 kabulü:

```bash
ROADVISION_WEB_ADMIN_EMAIL=... ROADVISION_WEB_ADMIN_PASSWORD=... \
ROADVISION_WEB_DSN=... ROADVISION_DB_DSN=... \
python3 web/scripts/verify_faz5.py --seed
# fikstürü geri almak için: --cleanup-seed
```

## Faz 4 — doğrulama ve dataset

Panelde **Doğrula** sekmesi: karar bekleyen kuyruğu (en eski önce) +
editör. Klavye: `D` doğru, `E` düzelt (kutu sürükleme + aynı model
sözlüğünden etiket; kutuyu değiştirmek zorunlu değildir), `Y` yanlış,
`→` atla. Örneğin `pothole` modelinde Çukur etiketi Rögar kapağı
(`manhole_cover`) olarak değiştirilebilir. Her karar tek transaction'da
karar satırı + copy-on-verify görüntü kopyası + karar × model bölümüne
düşen dataset örneği yazar; karar satırı olmayan tespit doğrulanmamıştır.
Uçlar: `GET /api/verify/queue`, `POST /api/reviews`, `POST
/api/reviews/bulk`, `PATCH /api/reviews/{object_id}` (kararı veren veya
yönetici). `corrected_bbox` her zaman kare koordinatındadır (§4.6).

Faz 4 kabulü:

```bash
ROADVISION_WEB_ADMIN_EMAIL=... ROADVISION_WEB_ADMIN_PASSWORD=... \
ROADVISION_WEB_DSN=... ROADVISION_DB_DSN=... \
python3 web/scripts/verify_faz4.py --seed
# fikstürü geri almak için: --cleanup-seed
```

## Faz 3 — tespit arşivi

Panelde **Arşiv** sekmesi: masaüstü Tespit Arşivi ile aynı sorgu
sözleşmesi + doğrulandı/doğrulanmadı etiketi (webapp v2
`detection_reviews`; karar satırı olmayan tespit doğrulanmamıştır).
Uçlar: `GET /api/archive/types`, `GET /api/archive/detections`
(model_id/type_id/review_status/run_id/capture_id/ts_from/ts_to/
min_confidence/only_with_image, keyset `cursor`, `limit≤200`),
`GET /api/captures/{id}`, `GET /api/media/{id}` (`ETag`, 304).
Arşiv, masaüstünün en az bir kez şema v3 migration'ını çalıştırmış
olmasını ister; aksi hâlde uçlar 409 `archive_unavailable` döner.
Medya yalnız güvenli raster MIME türlerinde, SHA-256/byte bütünlüğü
doğrulanarak sunulur. `private, no-cache`, çıkıştan sonra yerel önbelleğin
oturum doğrulamasını atlamasını engeller.

Faz 3 kabulü:

```bash
ROADVISION_WEB_ADMIN_EMAIL=... ROADVISION_WEB_ADMIN_PASSWORD=... \
ROADVISION_WEB_DSN=... ROADVISION_DB_DSN=... \
python3 web/scripts/verify_faz3.py --seed   # boş arşive fikstür ekler
# fikstürü geri almak için: --cleanup-seed
```

## Faz 2 — log görüntüleyici ve SPA

Compose ile: `docker compose up -d --build api frontend` → panel
`http://127.0.0.1:8080` (nginx, SPA + `/api` proxy). Host üzerinde
frontend geliştirme için Node 22.12+ gerekir:

```bash
cd web/frontend
npm ci             # sürümler package-lock.json'dan; bağımlılık değişince lock'u birlikte commit edin
npm audit --audit-level=moderate
npm run dev        # http://127.0.0.1:5173, /api → 127.0.0.1:8800 proxy
```

Uçlar: `GET /api/logs` (level/category/model_id/run_id/ts_from/ts_to,
`cursor` keyset, `limit≤500`, `order=asc|desc`), `GET /api/logs/{id}`
(payload dahil), `GET /api/meta/models`.
SPA'nın küçük History API yönlendiricisi bağımlılıksızdır; nginx tüm
uygulama yollarını `index.html`e geri düşürür.

Faz 2 kabulü (100k kayıt + p95 gecikme + keyset doğruluğu + nginx):

```bash
ROADVISION_WEB_ADMIN_EMAIL=... ROADVISION_WEB_ADMIN_PASSWORD=... \
ROADVISION_WEB_DSN=... ROADVISION_DB_DSN=... \
ROADVISION_WEB_HTTP_URL=http://127.0.0.1:8080 \
python3 web/scripts/verify_faz2.py --seed
# sentetik kayıtları geri almak için: --cleanup-seed
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
- Kimliksiz kayıt IP başına dakikada 3; giriş hesap başına dakikada 5 ve
  IP başına dakikada 30 denemeyle Argon2 çalıştırılmadan önce sınırlanır.
  Eşikler `ROADVISION_WEB_*_RATE_PER_MINUTE` değişkenleriyle ayarlanabilir.
- Arşiv görüntüsünün ETag eşleşen 304 yolu blob baytlarını PostgreSQL'den
  taşımaz; 200 yanıtında byte boyutu ve SHA-256 bütünlüğü yine doğrulanır.
- Parolalar `.env` dosyasındadır ve komut satırına yazılmaz;
  `bootstrap_db.sh` parolayı psql değişkeni olarak aktarır.
- API konteyneri yalnız `127.0.0.1:8800`e yayınlanır; dış erişim Faz 2'de
  nginx üzerinden sağlanır. Uzak erişimde nginx TLS yapılandırması
  etkinleştirilmeli ve `ROADVISION_WEB_COOKIE_SECURE=true` yapılmalıdır.
- nginx varsayılanı CSP, clickjacking/MIME-sniffing/referrer/COOP
  başlıklarını ve 1 MiB istek gövdesi sınırını uygular.
