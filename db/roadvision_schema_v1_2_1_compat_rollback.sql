\set ON_ERROR_STOP on

/*
RoadVision DB şema 3 -> uygulama v1.2.0 uyumluluk geri dönüşü

V3 migration eklemelidir: v2 kolonlarını ve verilerini silmez. Bu nedenle
eski uygulamayı geçici olarak yeniden çalıştırmak için yalnız sürüm kapısı
2'ye indirilir. type_id, sözlük, trigger ve görünümler bilinçli olarak
yerinde bırakılır; v1.2 INSERT'lerinde trigger type_id değerini doldurmaya
devam eder. V1.2.1 yeniden açıldığında migration güvenle tekrar uygulanır.
*/

BEGIN;

SELECT pg_advisory_xact_lock(1385428466);

DO $$
BEGIN
    IF to_regclass('schema_info') IS NULL
       OR to_regclass('detected_objects') IS NULL
       OR to_regclass('detection_types') IS NULL THEN
        RAISE EXCEPTION
            'RoadVision v3 nesneleri bulunamadı; uyumluluk geri dönüşü uygulanmadı';
    END IF;

    IF EXISTS (SELECT 1 FROM detected_objects WHERE type_id IS NULL) THEN
        RAISE EXCEPTION
            'NULL type_id bulundu; uyumluluk geri dönüşü uygulanmadı';
    END IF;
END $$;

DELETE FROM schema_info
WHERE version = 3;

COMMIT;

SELECT version, applied_at
FROM schema_info
ORDER BY version;
