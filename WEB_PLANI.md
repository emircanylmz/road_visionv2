# RoadVision Web Paneli — tasarım ve uygulama planı (RVU-0004)

Durum: **Faz 0–5 tamamlandı**
Revizyon: 30 Temmuz 2026 (r9 — Faz 5 tamamlandı)

## 1. Amaç ve kapsam

Masaüstü uygulamanın PostgreSQL'e yazdığı günlükleri, tekil tespitleri ve
tespit görüntülerini tarayıcıdan sunan; tespitlerin insan eliyle
**doğru / düzeltildi / yanlış** olarak sınıflandırılmasını ve bu kararların
model eğitimi için dataset tablolarında biriktirilmesini sağlayan ayrı bir
web servisi.

Kapsam içi: üyelik + yönetici onayı, oturum yönetimi, log görüntüleyici,
görüntülü tespit arşivi, doğrulama iş akışı (kutu düzeltme ve sınıf
değiştirme dahil), dataset tabloları ve YOLO formatında dışa aktarma.

Kapsam dışı: masaüstü uygulamada herhangi bir değişiklik, canlı inference,
model eğitimi. Web servisi masaüstünün varlığından haberdar değildir; tek
temas noktası PostgreSQL'dir.

## 2. İlk plandan revizyonlar

Bu bölüm, ilk taslakta yer alıp gözden geçirmede değiştirilen kararları ve
gerekçelerini kaydeder (bkz. MEDYA_TASARIM_PLANI.md §2 ile aynı disiplin).

1. **Alembic yerine sürüm-kapılı SQL runner.** İlk taslak `webapp` şeması
   için Alembic öneriyordu. Masaüstü tarafında zaten kanıtlanmış bir düzen
   var: `schema_info` sürüm kapısı + `pg_advisory_xact_lock` + tekrar
   çalıştırılabilir SQL (`roadvision/db.py ensure_schema`). Web tarafında
   aynı deseni kullanmak tek tip operasyon modeli sağlar, SQLAlchemy
   bağımlılığını ortadan kaldırır ve migration'ların davranışı masaüstüyle
   birebir aynı testlerle doğrulanabilir. Alembic terk edildi;
   `web/app/migrations.py` içindeki `ensure_webapp_schema` kullanılacak.
2. **`webapp` şemasından `public` tablolarına FK verilmez.** İlk taslakta
   `detection_reviews.object_id` için `REFERENCES public.detected_objects`
   vardı. Masaüstünün retention/prune hattı (`prune_media`) kendi
   tablolarında satır siler; web tarafından eklenen bir FK bu silmeleri
   engelleyebilir veya web şemasını masaüstü VACUUM/şema kararlarına
   bağımlı kılar. Karar: web tabloları `public` kimliklerini **düz değer**
   olarak taşır (`object_id BIGINT`), bütünlük API katmanında ve
   "masaüstü `detected_objects` satırı silmez" sözleşmesiyle korunur. Bu
   sayede web rolüne `REFERENCES` yetkisi de verilmez; erişim saf
   `SELECT`te kalır.
3. **Karar (verdict) ikiliden üçlüye çıktı.** Kutu düzeltme ve sınıf
   değiştirme isteğiyle `correct / corrected / wrong` üçlüsü tanımlandı.
   `corrected`, "nesne gerçek ama kutusu ve/veya sınıfı hatalı" durumudur
   ve eğitim açısından pozitif örnektir.
4. **Dataset bölümlemesi `positive/wrong` olarak adlandırıldı.** İlk
   taslaktaki `dataset_correct` bölümü, `corrected` kararını da
   kapsadığından `dataset_positive` adını aldı; `correct` ve `corrected`
   aynı fiziksel bölümde, `wrong` ayrı bölümde tutulur.

## 3. Mimari ve teknoloji

```text
Tarayıcı (React SPA)
   │ HTTPS, HttpOnly session cookie
   ▼
nginx  ── statik SPA + /api reverse proxy        (Faz 2)
   ▼
FastAPI (web/app)  ── psycopg3 AsyncConnectionPool
   │ rol: roadvision_web
   ▼
PostgreSQL 17 (mevcut compose servisi)
   ├── public  şeması  → yalnız SELECT (masaüstünün alanı)
   └── webapp şeması  → sahibi roadvision_web (webin alanı)
```

| Katman | Seçim | Gerekçe |
| --- | --- | --- |
| Backend | Python 3.11 + FastAPI | Projeyle aynı dil; Pydantic ile sözleşme-öncelikli doğrulama; otomatik OpenAPI |
| DB erişimi | psycopg3 + psycopg_pool | Masaüstüyle aynı sürücü; `roadvision.archive` filtre/keyset üreticileri yeniden kullanılabilir |
| Web migration | Sürüm-kapılı SQL runner (§2/1) | Masaüstü `ensure_schema` deseniyle bire bir aynı disiplin |
| Frontend | React 18 + TypeScript + Vite | Yoğun etkileşimli arşiv/doğrulama ekranları |
| Veri çekme | TanStack Query + semantik HTML tabloları | Keyset sayfalama ve aralıklı yenileme; Faz 2'nin sabit kolonlarında ek tablo bağımlılığı yok |
| Stil | Tailwind CSS | Hız; masaüstü v2 temasının renkleri değişkenlere taşınır |
| Kimlik | HttpOnly cookie + DB-backed session, Argon2id | Dahili panel için JWT'den basit ve anında iptal edilebilir |
| Dağıtım | Mevcut compose'a `api` (+Faz 2'de `nginx`) | Postgres zaten compose'ta |

## 4. Veritabanı sözleşmesi

### 4.1 Erişim modeli ve rol

`roadvision_web` rolü `public` şemasında yalnız `USAGE` + `SELECT` alır;
`ALTER DEFAULT PRIVILEGES` ile masaüstünün ileride oluşturacağı tablolar da
otomatik `SELECT` kapsamına girer. `webapp` şemasının sahibi
`roadvision_web`tir; masaüstü migration'ları (`public.schema_info` kapısı)
bu şemayı hiç görmez. Kurulum `web/db/bootstrap.sql` ile masaüstü şemasının
sahibi rol tarafından bir kez yapılır (bkz. §9 Faz 0).

Advisory-lock sabitleri çakışmayı önlemek için ayrıktır:

| Kilit | Sabit | Kullanan |
| --- | --- | --- |
| Masaüstü migration | 1385428466 | `db/roadvision_schema_v1_2_1.sql`, `ensure_schema` |
| Web migration | 1385428467 | `web/app/migrations.py` |
| Web bootstrap | 1385428468 | `web/db/bootstrap.sql` |

### 4.2 Kimlik ve denetim (Faz 1 migration'ı)

```sql
CREATE TABLE webapp.users (
    user_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,                 -- Argon2id
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member'
                  CHECK (role IN ('member', 'admin')),
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected', 'disabled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at   TIMESTAMPTZ,
    approved_by   BIGINT REFERENCES webapp.users(user_id)
);
CREATE UNIQUE INDEX users_email_lower_uq ON webapp.users (lower(email));

CREATE TABLE webapp.sessions (
    session_id   UUID PRIMARY KEY,
    user_id      BIGINT NOT NULL
                 REFERENCES webapp.users(user_id) ON DELETE CASCADE,
    csrf_token   TEXT NOT NULL,       -- oturuma bağlı double-submit belirteci (§8)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,          -- mutlak 12 saat
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 30 dk hareketsizlik izi
    ip           INET,
    user_agent   TEXT
);
CREATE INDEX sessions_user_idx ON webapp.sessions (user_id, expires_at);

CREATE TABLE webapp.admin_audit (
    audit_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id   BIGINT NOT NULL REFERENCES webapp.users(user_id),
    action     TEXT NOT NULL,        -- approve_user, reject_user, disable_user,
                                     -- revoke_session, change_review, ...
    target     TEXT NOT NULL,        -- 'user:12', 'review:9812' gibi
    detail     JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Kayıt olan herkes `pending` başlar; giriş yalnız `approved` için kabul
edilir. İlk yönetici `web/scripts/create_admin.py` (Faz 1) ile açılır.

### 4.3 Doğrulama kayıtları: satır yokluğu = doğrulanmadı

Tekil tespit başına en fazla bir karar satırı tutulur; karar satırı olmayan
her `public.detected_objects` kaydı tanım gereği **doğrulanmadı**dır. Yeni
gelen tespitler böylece kendiliğinden doğrulanmamış kuyruğuna düşer ve
masaüstü yazım hattına tek satır dokunulmaz.

```sql
CREATE TABLE webapp.detection_reviews (
    object_id         BIGINT PRIMARY KEY,   -- public.detected_objects.id (FK'sız, bkz. §2/2)
    verdict           TEXT NOT NULL
                      CHECK (verdict IN ('correct', 'corrected', 'wrong')),
    corrected_bbox    REAL[]
                      CHECK (corrected_bbox IS NULL
                             OR array_length(corrected_bbox, 1) = 4),
    corrected_type_id INTEGER,              -- public.detection_types.type_id (FK'sız)
    reviewer_id       BIGINT NOT NULL REFERENCES webapp.users(user_id),
    reviewed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    note              TEXT,
    -- corrected kararı en az bir düzeltme taşımalı; diğer kararlar taşımamalı.
    CONSTRAINT corrected_payload CHECK (
        (verdict = 'corrected')
        = (corrected_bbox IS NOT NULL OR corrected_type_id IS NOT NULL)
    )
);
CREATE INDEX detection_reviews_reviewed_idx
    ON webapp.detection_reviews (reviewed_at);
```

`object_id PRIMARY KEY`, iki kullanıcının aynı tespiti aynı anda
karara bağlamasını veritabanı seviyesinde engeller; ikinci istek 409 alır.

Tablo, planın ilk revizyonundan farklı olarak **webapp v2 migration'ı ile
Faz 3'te açılır**: arşiv sayfasının doğrulandı/doğrulanmadı etiketi ve
`review_status` filtresi "satır yokluğu = doğrulanmadı" semantiğine, yani
bu tabloya LEFT JOIN'e dayanır. Faz 3 tabloya hiçbir satır yazmaz; yazım
uçları (`/reviews*`) Faz 4'tedir.

Düzeltme kuralları:

- `corrected_type_id`, **tespitin geldiği modelin** sınıf sözlüğünden
  seçilmelidir (`detection_types.model_id` eşleşmesi). Modeller ayrı
  dataset'lerle eğitildiğinden çapraz-model düzeltme anlamsızdır; kural API
  katmanında doğrulanır, Faz 4 migration'ında trigger ile de bağlanır.
- `corrected_bbox`, orijinal `detected_objects.bbox` ile aynı koordinat
  sistemindedir: modelin işlediği kare pikselinde `xyxy` (bkz. §4.6).
- Semantic model (`roadline`) kutusuz olduğundan yalnız
  `correct`/`wrong` alabilir; arayüz düzeltme modunu bu modelde gizler,
  API `corrected` isteğini reddeder.

### 4.4 Kalıcı görüntü kopyası (copy-on-verify)

Masaüstünün `prune_media` hattı `media_blobs`u retention süresi ve toplam
boyut kotasıyla siler. Doğrulanmış bir örneğin görüntüsü dataset için
kalıcı olmalıdır; bu yüzden karar anında JPEG baytları webin kendi
deposuna kopyalanır:

```sql
CREATE TABLE webapp.dataset_media (
    sha256    TEXT PRIMARY KEY,   -- public.media_blobs.sha256 ile aynı özet
    bytes     BYTEA NOT NULL,
    width     INTEGER,
    height    INTEGER,
    byte_size INTEGER NOT NULL
);
```

Özet anahtar `media_blobs` ile aynı olduğundan aynı capture'dan gelen
ikinci tespitin kopya maliyeti sıfırdır (`ON CONFLICT DO NOTHING`).
Görüntüsü retention nedeniyle çoktan silinmiş eski bir tespit
doğrulanırsa karar yine kaydedilir; örnek `original_sha = NULL` ile
görüntüsüz işaretlenir ve arayüz bunu açıkça gösterir. Hacim 10 GB'ı
aşarsa `bytes` kolonu nesne deposu yoluna (MinIO/S3) taşınabilir; şema
buna hazırdır (bkz. §10).

### 4.5 Dataset tabloları: karar × model bölümlemesi

"Türe ve doğruluğa göre ayrı tablolar" gereksinimi, elle 40+ tablo yerine
bildirimsel bölümlemeyle karşılanır: sorgular tek mantıksal tabloya
yazılır, fiziksel olarak karar ve model başına gerçek ayrı tablolar oluşur.
Çalışma zamanında `detection_types`e yeni sınıf ekleyen mevcut davranış
(`is_catalogued=false`) DDL değişikliği gerektirmeden çalışmaya devam eder;
tür, indeksli kolondur.

```sql
CREATE TABLE webapp.dataset_samples (
    sample_id        BIGINT GENERATED ALWAYS AS IDENTITY,
    object_id        BIGINT NOT NULL,
    verdict          TEXT NOT NULL,
    model_id         TEXT NOT NULL,     -- roadline / traffic_sign / pothole / marking_damage
    -- Tespit anındaki özgün değerler (dondurulmuş kopya):
    type_id          INTEGER NOT NULL,
    class_name       TEXT NOT NULL,
    confidence       REAL,
    bbox             REAL[],
    area_ratio       REAL,
    -- Eğitimde kullanılacak nihai etiket (correct'te özgünün kopyası,
    -- corrected'da düzeltilmiş değer, wrong'da özgünün kopyası):
    final_type_id    INTEGER NOT NULL,
    final_class_name TEXT NOT NULL,
    final_bbox       REAL[],
    frame_w          INTEGER,
    frame_h          INTEGER,
    detected_at      TIMESTAMPTZ NOT NULL,
    run_id           BIGINT,
    capture_id       UUID,
    original_sha     TEXT REFERENCES webapp.dataset_media(sha256),
    annotated_sha    TEXT REFERENCES webapp.dataset_media(sha256),
    reviewed_at      TIMESTAMPTZ NOT NULL,
    reviewer_id      BIGINT NOT NULL,
    PRIMARY KEY (verdict, model_id, sample_id)
) PARTITION BY LIST (verdict);

CREATE TABLE webapp.dataset_positive PARTITION OF webapp.dataset_samples
    FOR VALUES IN ('correct', 'corrected') PARTITION BY LIST (model_id);
CREATE TABLE webapp.dataset_wrong PARTITION OF webapp.dataset_samples
    FOR VALUES IN ('wrong') PARTITION BY LIST (model_id);

-- 2 karar grubu × 4 model = 8 yaprak tablo:
CREATE TABLE webapp.ds_positive_roadline       PARTITION OF webapp.dataset_positive FOR VALUES IN ('roadline');
CREATE TABLE webapp.ds_positive_traffic_sign   PARTITION OF webapp.dataset_positive FOR VALUES IN ('traffic_sign');
CREATE TABLE webapp.ds_positive_pothole        PARTITION OF webapp.dataset_positive FOR VALUES IN ('pothole');
CREATE TABLE webapp.ds_positive_marking_damage PARTITION OF webapp.dataset_positive FOR VALUES IN ('marking_damage');
CREATE TABLE webapp.ds_wrong_roadline          PARTITION OF webapp.dataset_wrong    FOR VALUES IN ('roadline');
CREATE TABLE webapp.ds_wrong_traffic_sign      PARTITION OF webapp.dataset_wrong    FOR VALUES IN ('traffic_sign');
CREATE TABLE webapp.ds_wrong_pothole           PARTITION OF webapp.dataset_wrong    FOR VALUES IN ('pothole');
CREATE TABLE webapp.ds_wrong_marking_damage    PARTITION OF webapp.dataset_wrong    FOR VALUES IN ('marking_damage');

CREATE INDEX dataset_samples_type_ts_idx
    ON webapp.dataset_samples (type_id, detected_at);
CREATE INDEX dataset_samples_object_idx
    ON webapp.dataset_samples (object_id);
```

Katalogda olmayan yeni bir model kimliği görülürse `DEFAULT` bölüm yerine
Faz 4 migration'ı bilinen dört modeli açar; beşinci model ancak bilinçli
bir migration ile eklenir (masaüstü model kataloğu da aynı disiplindedir).

Tür bazında "tablo" görünümü istendiğinde sınıf başına view üretimi
yeterlidir; örnek:

```sql
CREATE VIEW webapp.v_ds_dur_positive AS
SELECT * FROM webapp.dataset_positive
WHERE model_id = 'traffic_sign' AND final_class_name = 'dur';
```

`wrong` örnekleri eğitimde iki biçimde değerlidir: yanlış-pozitif analizi
ve YOLO hard-negative/background görüntüsü üretimi. Export bunu destekler
(§6, `/datasets/export`).

Karar değişikliği (geri alma) tek transaction'dır: `detection_reviews`
güncellenir, ilgili `dataset_samples` satırı yeni bölüme taşınır (verdict
partition key olduğundan `UPDATE` PostgreSQL'de satırı otomatik taşır) ve
`admin_audit`e yazılır. Değiştirme yetkisi kararı veren kullanıcı ile
yöneticilerdedir.

### 4.6 Koordinat ve ölçek kuralı

`detected_objects.bbox`, modelin işlediği kare pikselindedir;
`ROADVISION_MEDIA_MAX_EDGE=1280` nedeniyle saklanan JPEG bu kareden küçük
olabilir. Sözleşme:

- `dataset_samples.frame_w/frame_h` **işlenen karenin** boyutudur ve karar
  anında `media_captures.frame_w/frame_h`den (yoksa görüntüden) doldurulur.
- Arayüz kutuyu çizerken `görüntü_boyu / frame_boyu` oranıyla ölçekler;
  düzeltilmiş kutu geri kaydedilirken aynı oranla frame pikseline çevrilir.
  Yani `corrected_bbox` her zaman frame koordinatındadır.
- YOLO export normalize koordinat ister; `final_bbox / frame` bölümü bunu
  ölçekten bağımsız üretir.

Faz 4 kabul testi, bilinçli küçültülmüş bir görüntüyle gidiş-dönüş
ölçeklemenin ±1 piksel içinde kaldığını doğrular.

### 4.7 Web migration düzeni

`web/app/migrations.py`:

- `webapp.schema_info (version, applied_at)` sürüm tablosu.
- `pg_advisory_xact_lock(1385428467)` altında, tek transaction'da,
  sıra atlaması yasak (`current+1` zorunlu) migration uygulaması.
- `webapp` şeması yoksa bootstrap'e yönlendiren açık Türkçe hata.
- Uygulama açılışında (`lifespan`) bir kez çalışır; birden çok API
  replikası aynı anda açılsa da kilit sayesinde tek uygulayıcı olur.

| webapp sürümü | İçerik | Faz |
| --- | --- | --- |
| 1 | users, sessions, admin_audit | 1 |
| 2 | detection_reviews | 3 |
| 3 | dataset_media, dataset_samples + bölümler | 4 |
| 4 | export_jobs | 5 |

## 5. Doğrulama iş akışı

Karar semantiği:

- **Doğru (`correct`)** — tespit olduğu gibi geçerli. Nihai etiket = özgün
  etiket.
- **Düzeltildi (`corrected`)** — nesne gerçek; kutu kaydırılmış/boyutu
  hatalı ve/veya sınıf yanlış. Kullanıcı kutuyu sürükleme tutamaçlarıyla
  düzeltir ve/veya aynı modelin sınıf listesinden yeni sınıf seçer. Nihai
  etiket = düzeltilmiş değerler; eğitim açısından pozitif örnektir.
- **Yanlış (`wrong`)** — yanlış pozitif; nesne yok ya da bambaşka bir şey.

Doğrulama sayfası akışı: varsayılan filtre "karar yok"; tür/model/tarih/
güven ile filtrelenip sıralanabilir kuyruk. Merkezde görüntü
(Orijinal / İşaretli / Yan yana), seçili tespitin kutusu istemci tarafında
vurgulanır. Klavye: **D** doğru, **E** düzeltme modu (kutu tutamaçları +
sınıf listesi açılır; Enter kaydeder, Esc iptal), **Y** yanlış, **→**
atla. Karar anında tek transaction: review insert → görüntü kopyası
(varsa) → dataset satırı. Kuyruk TanStack Query ile 5 sn'de bir "son
gördüğüm ts'den yenileri" keyset sorgusuyla tazelenir; yeni tespitler
kendiliğinden akar.

Kutu editörü Faz 4'te kütüphanesiz saf SVG overlay olarak yazılır (dört
köşe + kenar tutamacı, sürükleyerek taşıma); gerekirse `react-konva`
yedek plandır.

## 6. API sözleşmesi

Tüm uçlar `/api` altındadır; `auth` dışındakiler `approved` oturum ister.
Hatalar `{ "error": { "code", "message" } }` biçimindedir. Listeler
masaüstü arşivindeki keyset sözleşmesini kullanır: `cursor` + `limit`,
`OFFSET` yok; sıralama allowlist'lidir.

| Uç | Yöntem | Açıklama | Faz |
| --- | --- | --- | --- |
| `/auth/register` | POST | Kayıt; `pending` oluşturur | 1 |
| `/auth/login` `/auth/logout` | POST | Oturum aç/kapat (HttpOnly cookie) | 1 |
| `/auth/me` | GET | Aktif kullanıcı | 1 |
| `/admin/users` | GET | Duruma göre kullanıcı listesi | 1 |
| `/admin/users/{id}/approve|reject|disable` | POST | Onay akışı + audit | 1 |
| `/admin/sessions` | GET | Aktif oturum listesi | 1 |
| `/admin/sessions/{id}` | DELETE | Oturum iptali + audit | 1 |
| `/admin/audit` | GET | Denetim kayıtları (audit_id keyset) | 1 |
| `/logs` | GET | level/category/model/run/zaman filtreli keyset liste | 2 |
| `/logs/{id}` | GET | Tek kayıt + `payload` JSON | 2 |
| `/meta/models` | GET | Şema v3 model kataloğu (arayüz filtreleri için) | 2 |
| `/archive/types` | GET | Model → tür ağacı + sayımlar | 3 |
| `/archive/detections` | GET | Masaüstü filtre sözleşmesi + `review_status` filtresi (`unreviewed/correct/corrected/wrong`) | 3 |
| `/captures/{capture_id}` | GET | Orijinal+işaretli medya kimlikleri, frame boyutu | 3 |
| `/media/{media_id}` | GET | Oturum korumalı raster görüntü; `ETag: sha256`, `Cache-Control: private, no-cache` | 3 |
| `/verify/queue` | GET | Karar bekleyenler (tür/tarih/güven filtreli) | 4 |
| `/reviews` | POST | Tek karar; gövde: `object_id`, `verdict`, `corrected_bbox?`, `corrected_class?`, `note?` | 4 |
| `/reviews/bulk` | POST | Aynı gövdeden dizi; kısmi başarı raporu | 4 |
| `/reviews/{object_id}` | PATCH | Karar değişikliği (sahip/adminde) | 4 |
| `/datasets/summary` | GET | model × tür × karar kırılımlı sayımlar | 5 |
| `/datasets/export` | POST | YOLO zip üreten arka plan işi; gövde: `model_id`, `verdict=positive|wrong` | 5 |
| `/datasets/exports` | GET | İş listesi (zip gövdesiz) | 5 |
| `/datasets/exports/{id}` | GET | İş durumu | 5 |
| `/datasets/exports/{id}/download` | GET | Bitmiş işin zip'i; hazır değilse 409 | 5 |
| `/stats/overview` | GET | Panel kartları: günlük tespit, doğrulama hızı, model dağılımı | 5 |
| `/healthz` | GET | DB + şema sürümleri | 0 |

`POST /reviews` doğrulamaları: tespit var mı, daha önce kararlanmamış mı
(PK ihlali → 409), `corrected` ise model `detect` mi ve sınıf aynı modelin
sözlüğünde mi, bbox `x1<x2, y1<y2` ve frame sınırları içinde mi.

## 7. Sayfalar

**Giriş / Kayıt:** kayıt sonrası "hesabınız yönetici onayı bekliyor"
ekranı; onaysız giriş aynı mesajı döner (hesap varlığı sızdırılmaz).

**Yönetici:** bekleyen üyelikler, onay/ret, devre dışı bırakma, aktif
oturumlar ve iptal, audit listesi.

**Loglar:** seviye/kategori/model/run/zaman filtreli sanal-kaydırmalı
tablo; satır detayında `payload` çekmecesi. Masaüstü Oturum Günlüğü
kolonlarıyla aynı adlandırma kullanılır.

**Arşiv:** model → tür üç durumlu ağaç, zaman/güven/run filtreleri, keyset
sayfalama; her satırda `Doğrulanmadı / Doğru / Düzeltildi / Yanlış`
rozeti ve karar filtresi. Satır detayında Orijinal / İşaretli / Yan yana
görünüm.

**Doğrulama:** §5'teki kuyruk + editör.

**Dataset:** model × tür × karar kırılım tablosu, örnek galeri, export
işleri ve indirme bağlantıları.

## 8. Güvenlik

- Parolalar Argon2id (argon2-cffi varsayılan parametreleri) ile saklanır.
- Oturum çerezi: `HttpOnly; Secure; SameSite=Lax`; sunucu tarafı kayıt
  `webapp.sessions`ta, admin anında iptal edebilir; mutlak 12 saat + 30 dk
  hareketsizlik süresi.
- CSRF: SameSite=Lax'a ek olarak durum değiştiren isteklerde
  `X-RoadVision-CSRF` başlığı ile double-submit çerezi (Faz 1).
- Giriş ucu IP+e-posta bazlı oran sınırlamasının (dakikada 5) yanında
  benzersiz e-posta selinin Argon2 maliyetini kesen IP tavanı (dakikada
  30); kayıt ucu Argon2'den önce IP başına dakikada 3 deneme. Limiter
  anahtar kapasitesi dolduğunda aktif kovaları silmez, yeni anahtarları
  pencere açılana kadar fail-closed reddeder.
- `/media` yalnız oturumla erişilir; `Cache-Control: private`.
- DB en az yetki: `roadvision_web` public'e yazamaz (Faz 0 kabul testi
  bunu makinede doğrular); parolalı DSN'ler yalnız `.env`te tutulur,
  komut satırına yazılmaz (mevcut `--dsn` uyarı disipliniyle aynı).
- API konteyneri yalnız `127.0.0.1:8800`e yayınlanır; dış erişim Faz 2'de
  nginx + TLS ile açılır.

## 9. Fazlar ve kabul ölçütleri

**Faz 0 — DB temeli ve API iskeleti** *(29 Temmuz 2026'da tamamlandı)*
Teslimat: `web/db/bootstrap.sql` (+ compose init için
`web/db/001-webapp-bootstrap.sh`), `web/scripts/bootstrap_db.sh`,
`web/app/{config,db,migrations,main}.py`, `web/Dockerfile`,
`web/requirements.txt`, compose `api` servisi, `.env.example` alanları,
`web/tests/test_migrations.py`, `web/scripts/verify_foundation.py`.
Kabul: (a) `verify_foundation.py` web DSN ile PASS verir — public
SELECT çalışır, public INSERT/CREATE `InsufficientPrivilege` ile reddedilir,
webapp'e yazılabilir; (b) `GET /healthz` iki şema sürümünü döndürür;
(c) migration testleri torch/psycopg olmadan geçer.

Kabul sonucu: web migration ve bootstrap güvenlik testleri 7/7 geçti;
`roadvision_web` rolünün `public` SELECT yetkisi ile çalıştığı, INSERT/CREATE
işlemlerinin reddedildiği ve `webapp` şemasına yazabildiği PostgreSQL 17.10
üzerinde doğrulandı. Docker API imajı üretildi; API ve PostgreSQL
konteynerleri sağlıklı çalışırken `/healthz`, `public=3` ve `webapp=0`
sürümlerini döndürdü.

**Faz 1 — Kimlik ve yönetici** *(29 Temmuz 2026'da tamamlandı)* — webapp v1
migration'ı (users/sessions/admin_audit); Argon2id + zamanlama-eşitlemeli
giriş; oturuma bağlı CSRF (double-submit, `X-RoadVision-CSRF`); IP+e-posta
bazlı giriş oran sınırı; onay/ret/devre dışı akışı (geçiş kuralları hem saf
fonksiyonda hem UPDATE..WHERE'de); devre dışı bırakmada oturum iptaliyle tek
transaction'da audit; `create_admin.py` (parola getpass/ortamdan, komut
satırından asla) ve HTTP kabul betiği `verify_faz1.py`.
Kabul: (a) onaysız kullanıcı hiçbir korumalı uca erişemez ve onaysız giriş
403 `pending_approval` döner; (b) onay/ret/devre dışı `admin_audit`e düşer;
(c) CSRF başlıksız durum-değiştiren istek 403 alır; (d) devre dışı bırakılan
kullanıcının açık oturumu anında geçersizdir — dördü de `verify_faz1.py`
ile makinede doğrulanır.

Kabul sonucu: web birim testleri 28/28, masaüstü regresyon paketi 255/255
geçti. Çalışan PostgreSQL üzerinde `webapp=1`, `public=3` korundu;
`verify_faz1.py` kayıt, onaysız giriş, yönetici onayı, CSRF reddi, durum
geçişi yarışı, audit, rol yalıtımı, aktif oturum listesi ve devre dışı
bırakmada anlık oturum iptalini gerçek HTTP istekleriyle PASS olarak
doğruladı. API ve PostgreSQL konteynerleri sağlıklı kaldı.

**Faz 2 — Log görüntüleyici + nginx/SPA temeli** *(29 Temmuz 2026'da
tamamlandı)* — `logquery.py` saf sorgu üreticisi (masaüstü `archive`
disiplini: allowlist'li sıralama, `(ts,id)` keyset, DB'siz test);
`/api/logs`, `/api/logs/{id}`, `/api/meta/models` uçları; React 18 +
TypeScript + Vite + Tailwind v4 + TanStack Query SPA'sı — giriş/kayıt,
ikon-raylı kabuk, seviye çipleri ve taslak/uygula filtreli Loglar sayfası
(payload çekmecesiyle), Üyeler/Oturumlar/Denetim sekmeli Yönetim sayfası;
tasarım token'ları `roadvision/qt/theme.py`'den birebir taşınır. nginx,
SPA'yı sunar ve `/api`'yi proxy'ler; api imajı `--proxy-headers` ile gerçek
istemci IP'sini alır (`FORWARDED_ALLOW_IPS`). TLS örneği `nginx.conf`
içinde yorumludur; sertifika kuruluma özgüdür ve TLS açıldığında
`ROADVISION_WEB_COOKIE_SECURE=true` yapılır. `package-lock.json`
depodadır; imaj derlemesi sürümleri kilitten okuyan `npm ci` ile
deterministiktir. İstemci yönlendirmesi, üretim bağımlılığı güvenlik
denetimini temiz tutan küçük bir History API yönlendiricisiyle sağlanır.
Kabul: (a) 100k+ kayıtta filtreli ve filtresiz keyset sayfalamada p95
< 100 ms; (b) sayfalar arasında id tekrarı/atlaması yok, `(ts,id)`
sıralaması monoton; (c) seviye filtresi yalnız istenen seviyeleri döndürür
ve `has_payload` işaretli kaydın ayrıntısı payload ile gelir; (d) nginx
SPA kökünü sunar ve `/api` proxy'si çalışır — hepsi `verify_faz2.py` ile
makinede doğrulanır (`--seed`, sahip DSN'iyle `faz2-seed` damgalı sentetik
kayıt ekler; `--cleanup-seed` siler); (e) `npm run build` strict tsc ile
üretim çıktısı verir.

Kabul sonucu: web birim testleri 38/38, masaüstü regresyon paketi 255/255
ve strict TypeScript üretim derlemesi geçti; üretim bağımlılık denetimi
0 açık verdi. Gerçek PostgreSQL tablosu 99.629 geri alınabilir sentetik
kayıtla 100.000 satıra tamamlandı. Filtresiz 30 sayfada p95 9,4 ms,
`warning+error` filtresinde p95 34,8 ms ölçüldü; `(ts,id)` sırası,
tekrarsız keyset akışı, model kataloğu ve payload ayrıntısı doğrulandı.
Sentetik kayıtlar kabulden sonra silindi. API/PostgreSQL/nginx konteynerleri
sağlıklı kaldı; nginx kökü, `/api` proxy'si ve güvenlik başlıkları ile
gerçek tarayıcıda giriş, seviye filtresi, log ayrıntısı, yönetim ve çıkış
akışları konsol hatası olmadan tamamlandı.

**Faz 3 — Arşiv** *(29 Temmuz 2026'da tamamlandı)* — webapp v2 migration'ı
(`detection_reviews`, yalnız tablo; bkz. §4.3); masaüstü
`roadvision/archive.py` FROM/WHERE sözleşmesini birebir taşıyan saf
`archivequery.py` (+ web'in tek eklemesi: `detection_reviews` LEFT JOIN'i
ve SQL'de türetilen `review_status`); `/api/archive/types` (katalog +
yalnız çalışma zamanında görülmüş modellerle tür ağacı, tür × karar
sayımları), `/api/archive/detections` (model/tür/doğrulama durumu/run/
capture/zaman/güven/görüntü filtreleri, `(o.ts,o.id)` keyset, limit+1
sözleşmesi), `/api/captures/{id}` ve `ETag: "sha256"` +
`Cache-Control: private, no-cache` başlıklı, her kullanımda oturumu yeniden
doğrulayan ve `If-None-Match`te gövdesiz 304 dönen `/api/media/{id}`.
Medya byte boyutu/SHA-256 bütünlüğü ve güvenli raster MIME allowlist'i
sunucu tarafında uygulanır. SPA'ya Arşiv sayfası: tür ağacı ve sayım
çipleri, doğrulama durumu filtresi, işaretli kare kartları, orijinal/
işaretli geçişli ayrıntı çekmecesi; görüntüsü retention ile silinmiş
tespit açıkça işaretlenir. Masaüstünden bilinçli API farkı: tür seçimi
yokken masaüstü boş liste, API filtre-yok davranır (arayüz varsayılanı
zaten "tümü"dür).
Kabul: (a) aynı filtre kümesi için API'nin gezdiği satır sayısı doğrudan
SQL sayımına eşittir (masaüstü paritesi); (b) keyset sayfaları tekrar/
atlama üretmez ve `review_status` alanı her satırda geçerlidir — yeni
tespitler kendiliğinden `unreviewed` görünür; (c) medya ucu ETag döndürür
ve `If-None-Match` eşleşmesinde gövdesiz 304 verir; (d) şema v3 yoksa
arşiv uçları 409 `archive_unavailable` döner — hepsi `verify_faz3.py`
ile makinede doğrulanır (`--seed` boş arşive sahip DSN'iyle `faz3-seed`
fikstürü ekler; `--cleanup-seed` siler).

Kabul sonucu: additive migration çalışan PostgreSQL'de `webapp=2`,
`public=3` durumunu üretti; `detection_reviews` boş açıldı. Web testleri
55/55, masaüstü regresyon paketi 255/255, Vite 8 strict üretim derlemesi
ve geliştirme bağımlılıkları dahil `npm audit` 0 açıkla geçti. Kabul
betiği 173 gerçek tespiti tekrar/atlama olmadan gezdi; model, tür, run,
capture, zaman, güven, görüntü ve doğrulama filtreleri tam satır paritesi
verdi. 87 capture/123 JPEG üzerinde oturum zorunluluğu, SHA-256/byte
bütünlüğü, ETag ve gövdesiz 304 doğrulandı. Gerçek tarayıcıda tür sayımları,
3 sütunlu görüntü kartları, birleşik filtreler ve orijinal/işaretli kare
çekmecesi yatay taşma veya konsol hatası olmadan çalıştı. Başarılı tarayıcı
çıkışının sunucu oturumunu sildiği veritabanında 0 açık oturumla doğrulandı.

**Faz 4 — Doğrulama + dataset** *(30 Temmuz 2026'da tamamlandı)* — webapp v3
migration'ı (v2 tablosu Faz 3'te açıldı): `dataset_media` +
karar × model bölümlü `dataset_samples` (2 grup × 4 model = 8 yaprak;
PK `(verdict, model_id, sample_id)`) ve §4.3'te söz verilen
corrected_type_id "aynı model sözlüğü" trigger'ı. Kural doğrulaması saf
modüllerde: `geometry.py` (§4.6 ölçek/`validate_bbox`, ±1 px gidiş-dönüş
testte sabit) ve `reviewrules.py` (semantic reddi, `unknown_class`,
`no_change`, `frame_unavailable`, `final_*` çözümü). Uçlar:
`/api/verify/queue` (varsayılan en eskiden yeniye — FIFO), `POST
/api/reviews` (tek transaction: karar + copy-on-verify + bölüme düşen
örnek; PK ihlali 409), `/api/reviews/bulk` (öğe başına transaction, kısmi
başarı raporu), `PATCH /api/reviews/{id}` (sahip/admin; verdict partition
key olduğundan örnek satırı UPDATE ile yeni bölüme taşınır; `admin_audit`e
`change_review`). Kare boyutu §4.6 "(yoksa görüntüden)" dalıyla orijinal
blobun boyutundan alınır (masaüstü `media_captures` frame_w/h saklamaz).
Bölümleme dışı model kimliği partition hatasına düşmeden 422
`unsupported_model` ile reddedilir. SPA'ya Doğrulama sayfası: kuyruk +
editör, klavye D/E/Y/→, SVG kutu editörü (viewBox = kare boyutu; sürükleme
kare pikselinde), aynı model sözlüğünden sınıf seçimi; semantic modelde
düzeltme gizlenir; görüntüsüz tespit karara açıktır ve örnek görüntüsüz
işaretlenir. Detect modellerde etiket düzeltmesi kutudan bağımsızdır:
aynı model sözlüğünden nihai etiket seçilir (ör. `pothole` →
`manhole_cover / Rögar kapağı`) ve kutu taşınmadan da `corrected`
dataset örneği üretilebilir.
Kabul: (a) karar transaction'ı atomiktir — review+medya+sample ya hep ya
hiç ve örnek doğru yaprakta doğrulanır; (b) çifte karar 409, CSRF'siz
yazım 403; (c) ölçek gidiş-dönüşü ±1 px (`final_bbox` üzerinde); (d)
semantic modelde `corrected` ve çapraz-model sınıf reddedilir; (e) PATCH
örneği yeni bölüme taşır ve audit'e düşer; (f) bulk kısmi başarı raporu
verir — hepsi `verify_faz4.py` ile makinede doğrulanır (`--seed` /
`--cleanup-seed`).

Kabul sonucu: web birim testleri 80/80, masaüstü regresyon paketi 255/255,
strict TypeScript denetimi ve Vite 8 üretim derlemesi geçti. Mevcut
`model_v2` PostgreSQL volume'unda `webapp=3`, `public=3` ve sekiz
karar × model yaprak tablosu doğrulandı. Güvenli dört tespitlik
`faz4-seed` fikstürüyle CSRF reddi, atomik üçlü yazım, çifte karar 409,
çapraz-model/semantic düzeltme reddi, ±1 px kutu gidiş-dönüşü, PATCH ile
bölüm taşıma + audit ve bulk kısmi başarı gerçek HTTP/DB akışında PASS
verdi; fikstür, örnek, medya kopyası ve audit kayıtları ardından sıfırlandı.
Gerçek tarayıcıda kuyruk, görüntü, detect düzeltme formu, aynı model sınıf
sözlüğü ve semantic düzeltme gizleme; yatay taşma veya konsol hatası
olmadan doğrulandı.

**Faz 5 — Export + istatistik** *(30 Temmuz 2026'da tamamlandı)* — webapp v4
migration'ı: `export_jobs` (pending→running→done/failed; **zip çıktısı da
BYTEA olarak DB'de** — §2 "tek temas PostgreSQL": konteynerler geçicidir,
servisin paylaştığı tek durum veritabanıdır). YOLO üretim kuralları saf
`exportbuild.py` modülünde: sınıf haritası deterministik (`class_index`
NULLS LAST → `class_name`; katalog dışı sınıflar sözlüğün sonunda),
etiket satırı `final_bbox / frame` bölümüyle normalize (§4.6'dan
ölçekten bağımsız), aynı kare birden çok örnek taşırsa tek görüntü + çok
satırlı tek etiket, `wrong` kapsamı **boş etiket dosyalı** hard-negative/
background görüntüleri üretir, görüntüsüz veya (pozitifte) kutusuz örnek
atlanır ve manifest'e sayılır. İş FastAPI arka plan görevinde havuzdan
kendi bağlantısıyla koşar; aynı model + kapsam için ikinci istek iş
bitmeden 409 `export_in_progress`, bitmemiş işin indirilmesi 409
`export_not_ready` alır. Bellek emniyeti: iş başına en çok
`MAX_EXPORT_IMAGES=5000` kare (aşan kısım manifest'te `truncated_at`).
`/datasets/summary` model × tür × karar kırılımını, `/stats/overview`
panel kartlarını (tespit hacmi, doğrulama kapsaması, model dağılımı,
aktif işler) verir. SPA'ya Dataset sayfası: istatistik kartları, kırılım
tablosu, kapsam + model seçimli export başlatma, koşan iş varken 2,5 sn
aralıklı yenilenen iş listesi ve indirme bağlantıları; örnek galerisi
için Arşiv sayfasına (karar filtreli görünüm) bağlanır.
Kabul: (a) YOLO zip'i `final_*` etiketleriyle üretilir ve normalize
koordinatlar DB'deki `final_bbox / frame` bölümüyle 1e-4 içinde eşleşir;
(b) `wrong` export'u ayrı seçilebilir ve boş etiketli background üretir;
(c) iş yaşam döngüsü 202 → done → indirilebilir zip'tir, erken indirme ve
çifte istek 409 alır; (d) `data.yaml` sözlüğü modelin `detection_types`
sözlüğüyle birebirdir — hepsi `verify_faz5.py` ile makinede doğrulanır
(`--seed` fikstür + API'den karar üretir; `--cleanup-seed` geri alır).

Kabul sonucu: web birim testleri 101/101, masaüstü regresyon paketi 255/255,
strict TypeScript denetimi ve Vite 8 üretim derlemesi geçti. Mevcut
`model_v2` PostgreSQL volume'unda `webapp=4`, `public=3` ve aktif iş
tekilliğini yarış koşulunda da koruyan `export_jobs_active_uq` partial
unique indeksi doğrulandı. İzole `faz5-seed` akışında pozitif export
23 görüntü/36 örnekle üretildi; zip düzeni, görüntü/etiket eşleşmesi,
`detection_types` ile birebir `data.yaml`, `final_bbox/frame` normalize
koordinatları (1e-4 tolerans), iş sürerken erken indirme ve çifte istek
409'ları, 3 karelik boş etiketli `wrong` export'u ve istatistik ucu gerçek
HTTP/DB üzerinde PASS verdi. Audit ile yalnız kabul işlerini hedefleyen
temizlikten sonra seed eventleri, export işaretleri, export işleri ve
test oturumları sıfırlandı. Gerçek tarayıcıda Dataset sayfası,
kapsam/model seçimi ve export düğümü 1280×720 görünümde yatay taşma veya
konsol hatası olmadan doğrulandı; tarayıcı test oturumu kapatıldı.
Güvenlik/performans sertleştirmesi sonrasında gerçek kayıt ucu üç normal
çakışmanın ardından dördüncü isteği `429 Retry-After: 60` ile kesti;
Faz 3 medya 200/ETag/gövdesiz-304/oturumsuz-401 kabulü ve event-loop dışı
zip üretimiyle Faz 5 pozitif/wrong export kabulü yeniden PASS verdi.

Kaba süre (tek geliştirici): Faz 0–1 ≈ 3–4 gün, 2–3 ≈ 3 gün, 4 ≈ 3–4 gün,
5 ≈ 2 gün.

## 10. Açık noktalar

- E-posta doğrulaması gerekli mi, yönetici onayı yeterli mi? (Şimdilik
  yalnız onay; SMTP eklemek Faz 1'de opsiyonel bırakıldı.)
- `dataset_media.bytes` için nesne deposu eşiği: toplam > 10 GB olursa
  MinIO'ya geçiş migration'ı planlanır.
- Canlı akış: `detected_objects` üzerine NOTIFY trigger'ı public şemaya
  dokunan tek istisna olacağından ertelendi; 5 sn'lik keyset yenileme
  yeterli görülürse hiç yapılmayabilir.
- Kutu editöründe çoklu-kutu (aynı karede komşu tespitleri birlikte
  gösterme) Faz 4 sonunda değerlendirilecek.
