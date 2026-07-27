# Tespit Arşivi — düzeltilmiş uygulama planı (v1.2.2)

Bu belge, PostgreSQL şema 3'te saklanan tekil tespitleri RoadVision içinden
okuyan **Tespit Arşivi** sekmesinin uygulanmış tasarım sözleşmesidir.
Uygulama sürümü v1.2.2'ye yükselir; veri modeli değişmediği için DB şema
sürümü 3 olarak kalır.

## 1. Kesinleşen ürün kararları

- Arşiv, sağdaki Notebook içinde üçüncü sekmedir.
- Varsayılan zaman aralığı **Son 24 saat**tir.
- Filtre ağacı model → tespit türü hiyerarşisindedir ve model satırı üç
  durumlu seçim gösterir.
- Semantic model için özel bir sentetik özet üretilmez; `detected_objects`
  tablosunda ne kaydedildiyse o gösterilir.
- CSV/JSON dışa aktarma v1.4 kapsamına bırakılmıştır.
- Arşiv fact satırı tekil nesnedir. Aynı event/capture birden fazla nesne
  içeriyorsa aynı görüntü işareti birden fazla satırda görülebilir.
- Arşiv her video karesinin ham dökümü değildir; journal tekrar bastırmadan
  geçerek PostgreSQL'e yazılmış tespitleri gösterir.

## 2. Veri sözleşmesi

Liste ve sayım sorguları aynı FROM/WHERE üreticisini kullanır:

```sql
FROM detected_objects AS o
JOIN detection_events AS e ON e.id = o.event_id
JOIN detection_types AS t ON t.type_id = o.type_id
LEFT JOIN roadvision_model_catalog AS m ON m.model_id = o.model_id
LEFT JOIN media_captures AS mc ON mc.capture_id = e.capture_id
WHERE o.type_id = ANY(%(type_ids)s::integer[])
```

Zaman, minimum güven ve run koşulları yalnız ilgili değer doluysa WHERE
bölümüne eklenir. `Tümü` zaman seçeneğinde yapay minimum/maksimum tarih
kullanılmaz. Özel tarih alanları yerel timezone-aware `datetime` değerine
çevrilir; başlangıç dahil, bitiş hariçtir ve `from < to` doğrulanır.

“Yalnız görüntüsü olanlar” filtresi `e.capture_id IS NOT NULL` kullanmaz.
Medya henüz yazılmamış veya saklama politikasıyla silinmiş olabileceğinden
gerçek `mc.capture_id IS NOT NULL` koşulunu kullanır. Satıra verilecek
capture kimliği de `mc.capture_id` değeridir.

Model görünen adı `COALESCE(m.display_name, o.model_id)`, tür görünen adı
`t.display_name` üzerinden gelir. Katalogda olmayan modeller
`detection_types` tabanlı ağaç sorgusuyla kaybedilmez; model kimliği görünen
ad olarak kullanılır ve pasif/katalog dışı işaretlenir. Şema 3'te model sıra
kolonu bulunmadığından sıra açıkça `active DESC, display_name, model_id`
olarak tanımlanır.

Tür sayımları; zaman, güven, run ve görüntü filtrelerini uygular fakat kendi
facet'i olan `type_ids` seçimini uygulamaz. Böylece işareti kaldırılmış bir
türün mevcut filtrelerdeki sayısı ağaçta görünmeye devam eder.

## 3. Sıralama ve keyset sayfalama

Desteklenen görünür sıralamalar:

- zaman
- güven
- alan oranı
- model görünen adı
- tür görünen adı

Sort kolonu kullanıcı girdisinden SQL'e doğrudan eklenmez. Enum değerleri
yalnız sabit, allowlist'li SQL ifadelerine eşlenir. Yön de yalnız `ASC` veya
`DESC` sabitlerinden üretilir. `id` her zaman aynı yönde ikinci eşitlik
bozucudur.

Tek bir `(kolon, id) < (...)` kalıbı bütün kolonlara uygulanmaz:

- DESC için birincil karşılaştırma `<`, ASC için `>` kullanılır.
- `confidence` ve `area_ratio` NULL olabilir ve her iki yönde `NULLS LAST`
  kullanılır.
- İmleç `last_value`, `last_id` ve `last_is_null` taşır.
- Non-NULL sayfadan sonra NULL bölümü açık koşulla erişilebilir.
- NULL bölümünde yalnız `column IS NULL` ve `id` karşılaştırması kullanılır.

“Önceki” düğmesi mevcut sayfanın son satır imlecini değil, o sayfayı
getirirken kullanılan request cursor'ını yığına iter. Filtre veya sıralama
değişince cursor yığını sıfırlanır.

Sayfa boyutu yalnız 25, 50 veya 100 olabilir. DB sorgusu `page_size + 1`
satır ister; fazlalık satır yalnız `has_more` belirlemek için kullanılır.

## 4. Katmanlar

### `roadvision/archive.py`

Tk ve thread bağımlılığı olmayan veri/sorgu katmanıdır:

- immutable filtre, sort, cursor, model/tür ve sonuç veri sınıfları
- DB şema sürümü en az 3 capability kontrolü
- ortak, parametreli FROM/WHERE üretimi
- `fetch_type_tree`
- `fetch_detections`
- `fetch_type_counts`

Bu fonksiyonlar transaction açmaz, commit veya rollback yapmaz. Bağlantı ve
transaction yaşam döngüsü fetcher'a aittir.

### `roadvision/archive_fetcher.py`

Tek salt-okunur worker kullanır:

- `request_tree()` ayrı tree generation üretir.
- `request_refresh(filter, sort, cursor, page_size, include_counts)` tek
  refresh revision üretir; page ve counts birbirini bayatlatmaz.
- Bekleyen işler sınırsız queue değildir: bir latest tree ve bir latest
  refresh slotu tutulur.
- Request kabulü, pending-slot değişimi ve kapanış aynı Condition kilidi
  altında atomiktir.
- UI hiçbir zaman bloklayan `put()` çağırmaz.
- Sonuç kuyruğu sınırlıdır ve UI her 33 ms turunda sınırlı sayıda sonuç işler.

Page ve counts aynı `REPEATABLE READ, READ ONLY` transaction'da çalışır.
Bağlantıda 5 saniyelik `statement_timeout` uygulanır ve her iş sonunda
rollback ile transaction kesin kapatılır. Yalnız bağlantı seviyesindeki
geçici Operational/Interface hataları, aynı revision güncelse bir kez yeni
bağlantıyla denenir. SQL, şema ve timeout hataları otomatik tekrarlanmaz.

Kapanış iki aşamalıdır: UI kapanmaya başladığında yeni istek kabulü ve
pending işler atomik kesilir; çalışan sorgunun tamamlanması Tk thread'inde
beklenmez. `shutdown_complete` sonrasında bounded join uygulanır.

### `roadvision/archive_state.py`

Tk'den bağımsız seçim, filtre ve sayfalama durumudur:

- `TypeSelectionModel`
- `PaginationState`
- `ArchiveState`

Üç durumlu model seçimi, tek callback üretimi, cursor stack'i,
draft/applied filtre ayrımı, loading/error/empty/disabled durumları ve
filtre değişiminde sayfalama sıfırlama burada test edilir.

### `roadvision/ui/archive_page.py`

İnce Tk render/adaptör katmanıdır:

- `ttk.Panedwindow` ile dar ekrana uyum
- tri-state tür ağacı
- zaman/güven/run/görüntü filtre çubuğu
- yatay ve dikey scrollbar'lı sonuç tablosu
- kolon sıralaması, 25/50/100 sayfa boyutu ve cursor tabanlı gezinme
- açık loading, empty, error, disabled ve stale-content durumları

Debounce gerçek `after_cancel` kullanır; yeni değişiklik ve widget kapanışı
eski callback'i iptal eder. Parent seçimi çocuk başına ayrı callback
üretmez. Sekme ilk açıldığında önce tree gelir, başlangıç seçimi kurulur,
ardından ilk refresh istenir.

### `roadvision/ui/app.py`

App düzeyinde ortak `_open_snapshot_capture(capture_id, capture_time)`
controller'ı viewer'ı oluşturur/öne getirir, retry state'ini kurar ve
SnapshotFetcher isteğini başlatır. Hem Oturum Günlüğü hem ArchivePage bu
metodu kullanır; `_request_snapshot` doğrudan UI callback'i değildir.

Archive sonuçları mevcut 33 ms `_poll_events` turunda tüketilir. Arşiv
render hataları yakalanır ve ana heartbeat'in yeniden planlanmasını
engellemez. `run_finished` bir UI olayı olmadığı için `stopped` veya
`source_ended` arşivi dirty işaretleyip hızlı yenilemeyi başlatır. Reaper,
`run_finished` sonrasında hem `EventJournal` hem `MediaRecorder` için
checkpoint ister. Journal checkpoint'i o ana kadarki kayıtlar
`PostgresSink` tarafından commit edilince; medya checkpoint'i o ana kadarki
kabul edilmiş capture işleri store denemesini tamamlayınca çözülür. İkisi de
başarılı olduğunda engine `archive_ready` olayı üretir ve UI sabit süre
tahmini kullanmadan kesin yenileme yapar. DB retry/backoff sürüyorsa bu olay
commit gerçekleşene kadar bekler; hiçbir bekleme UI veya inference thread'ine
taşınmaz. Görünmeyen sekme sonraki aktivasyonda, minimize edilip geri
getirilen seçili sekme ise `<Map>` olayıyla yenilenir.

Snapshot sonucu yalnız generation ile değil `capture_id` ile de doğrulanır.
Böylece yeni istek daha kuyruğa girmeden hata verse bile eski capture sonucu
aktif görüntüleyiciye uygulanmaz. Başarısız sıralama isteğinde eski satırlar
ve onların uygulanmış sıralama oku birlikte korunur. Sistem IANA saat dilimi
verisi sağlamıyorsa İstanbul özel tarih filtresi sabit UTC+3'e güvenli biçimde
geri döner.

## 5. Performans sınırı

Keyset pagination OFFSET kaynaklı sayfa kaymasını ve büyük offset taramasını
önler. Ancak mevcut şema 3 indeksleri bütün sıralama seçeneklerinde sabit
süre garantisi vermez. Varsayılan son-24-saat + zaman sıralaması mevcut
zaman/type indekslerinden yararlanır; confidence, area veya görünen ad
sıralamaları eşleşen kümeyi sıralayabilir.

Milyonlarca satırda bütün sıralamaları belirli bir SLA ile sunmak gerekirse
temsili veri üzerinde `EXPLAIN (ANALYZE, BUFFERS)` ölçümü yapılıp ayrı DB
şema 4 migration'ında seçici composite indeksler eklenir. Bu v1.2.2'nin
işlevsel kabul koşulu değildir.

## 6. Kabul ve testler

- Bütün ASC/DESC sortlar; eşit sort değerlerinde `id` tie-break
- NULL confidence/area bölümünde kayıpsız, tekrarsız ileri/geri sayfalama
- gerçek PostgreSQL integer-array adaptasyonu
- katalog dışı tür ve katalog dışı model ağacı
- dangling `detection_events.capture_id` satırının görüntü filtresinden
  dışlanması
- page/counts ortak filtre ve snapshot tutarlılığı
- tree ve refresh nesillerinin birbirini geçersiz kılmaması
- mixed pending iş coalescing, stale in-flight sonuç, retry ve close yarışları
- debounce iptali, tri-state seçim ve request-cursor yığını
- arşiv callback'inin viewer'ı gerçekten açması
- poll/render hatasından sonra 33 ms UI heartbeat'inin sürmesi
- map/unmap/map yaşam döngüsü, journal/media checkpoint sonrası kesin
  yenileme ve capture kimliğiyle bayat snapshot reddi
- DSN yok, şema eski, DB kapalı, sonuç yok ve gerçek görüntü açma durumları

Belgeler ve `APP_CONFIG.build` son fazda v1.2.2'ye birlikte yükseltilir.
