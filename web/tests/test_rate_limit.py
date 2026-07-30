"""SlidingWindowLimiter sözleşme testleri (saat enjeksiyonlu, DB'siz)."""

from __future__ import annotations

import unittest

from app.rate_limit import SlidingWindowLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SlidingWindowLimiterTests(unittest.TestCase):
    def test_sinira_kadar_izin_verir_sonra_bekletir(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(3, 60.0, now_fn=clock)
        key = ("1.2.3.4", "a@b.c")
        for _ in range(3):
            self.assertEqual(limiter.check(key), 0.0)
        wait = limiter.check(key)
        self.assertGreater(wait, 0.0)
        self.assertLessEqual(wait, 60.0)

    def test_pencere_dolunca_yeniden_izin_verir(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(2, 60.0, now_fn=clock)
        key = "k"
        limiter.check(key)
        limiter.check(key)
        self.assertGreater(limiter.check(key), 0.0)
        clock.now += 61.0
        self.assertEqual(limiter.check(key), 0.0)

    def test_anahtarlar_bagimsizdir(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(1, 60.0, now_fn=clock)
        self.assertEqual(limiter.check("a"), 0.0)
        self.assertGreater(limiter.check("a"), 0.0)
        self.assertEqual(limiter.check("b"), 0.0)

    def test_bekleme_suresi_en_eski_olaya_goredir(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(2, 60.0, now_fn=clock)
        limiter.check("k")          # t=1000
        clock.now += 30.0
        limiter.check("k")          # t=1030
        clock.now += 10.0           # t=1040; en eski olay 1000 → 20 sn kaldı
        self.assertAlmostEqual(limiter.check("k"), 20.0, places=6)

    def test_gecersiz_parametreler_reddedilir(self):
        with self.assertRaises(ValueError):
            SlidingWindowLimiter(0, 60.0)
        with self.assertRaises(ValueError):
            SlidingWindowLimiter(1, 0.0)
        with self.assertRaises(ValueError):
            SlidingWindowLimiter(1, 60.0, max_tracked_keys=0)

    def test_kapasitede_suresi_dolan_anahtarlar_kayipsiz_ayiklanir(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(2, 60.0, now_fn=clock, max_tracked_keys=4)
        for key in ("a", "b", "c", "d"):
            self.assertEqual(limiter.check(key), 0.0)
        self.assertEqual(limiter.tracked_keys, 4)
        clock.now += 61.0  # dördü de pencere dışı kaldı
        # Kapasitedeki yeni anahtar, aktif pencereye dokunmadan yalnız
        # bayatları temizletir; eski clear() davranışının aksine tablo
        # sıfırlanmaz, yeni anahtar kabul edilir.
        self.assertEqual(limiter.check("e"), 0.0)
        self.assertEqual(limiter.tracked_keys, 1)

    def test_benzersiz_anahtar_seli_hedefin_penceresini_sifirlatamaz(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(2, 60.0, now_fn=clock, max_tracked_keys=50)
        self.assertEqual(limiter.check("hedef"), 0.0)
        self.assertEqual(limiter.check("hedef"), 0.0)
        self.assertGreater(limiter.check("hedef"), 0.0)  # hedef kilitli
        # Eski davranışta bu sel tabloyu clear() ile sıfırlayıp hedefin
        # kilidini kaldırıyordu; kapasite dolunca yeni anahtarlar fail-closed
        # reddedilir ve hâlâ sınır uygulayan hedef kova hayatta kalır.
        for index in range(500):
            clock.now += 0.01
            limiter.check(("sel", index))
        self.assertGreater(limiter.check("hedef"), 0.0)
        self.assertLessEqual(limiter.tracked_keys, 50)

    def test_tum_kovalar_dolu_olsa_da_aktif_hedef_ayiklanmaz(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(2, 60.0, now_fn=clock, max_tracked_keys=4)
        for key in ("hedef", "sel-1", "sel-2", "sel-3"):
            self.assertEqual(limiter.check(key), 0.0)
            self.assertEqual(limiter.check(key), 0.0)
        self.assertGreater(limiter.check("yeni-ip"), 0.0)
        self.assertEqual(limiter.tracked_keys, 4)
        self.assertGreater(limiter.check("hedef"), 0.0)


if __name__ == "__main__":
    unittest.main()
