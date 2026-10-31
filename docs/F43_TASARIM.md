# F4-3 — Tarayıcıda Patron Odası: Tasarım (taslak)

Durum: **onaylandı** (2026-10-31): B1–B8 önerildiği gibi; F4-3a Claude yapar. Kaynak: `docs/ARASTIRMA2_ORTAK.md` F4-3 ve K2.
K2 kararı: "Claude dışı modellerle Patron önce yalnızca tarayıcıda denenir".

## 1. Amaç

Bilgisayarına hiçbir şey kurmayan biri, "Tarayıcı ajanı"na kendi anahtarını girer ve Patron'a iş verir: plan,
onay, uzmanlar, değerlendirme, rapor. Bugün Patron yalnızca bağlayıcıda ve yalnızca Claude Code ile çalışıyor.

## 2. Ana fikir: aynı protokol, yeni arka uç

- Tarayıcı cihazı (`browser_device.js`) bağlayıcıyla **aynı `boss.*` mesajlarını** cevaplar: `boss.get`,
  `boss.ask`, `boss.approve`, `boss.cancel`, `boss.reset`. Olaylar da aynıdır: `boss.state`, `boss.plan`,
  `boss.report`, `boss.event`.
- Böylece mevcut Patron paneli (`boss.js`), Görev Tahtası kartı ve Patron sahnesi (`boss_scene.js`) **değişmeden**
  çalışır. Yeni arayüz yazılmaz.
- Cihaz bilgisi `features` listesine `boss` ve `bossDepartments` ekler. Panel, departman listesini her zamanki gibi
  `device.info.bossDepartments`'tan okur.

## 3. Bağlayıcıyla birebir aynı kalması gereken kurallar

Güvenlik kuralları iki ayrı yerde (Python ve JS) yaşayacak. Ayrışmamaları için:

| Kural | Nasıl aynı tutulur |
|---|---|
| Plan şeması (`validate_plan`: alanlar, uzunluklar, gizli karakter, dosya kalıbı, `after` sırası, en fazla 4 iş emri, düzeltme turunda 2) | Python testlerindeki plan örnekleri bir JSON test vektörü dosyasına (`tests/boss_plan_vectors.json`) çıkarılır. Aynı vektörleri hem Python hem JS testi geçer. |
| Roller ve departmanlar (adlar, profiller, talimat metinleri) | Yeni `web/js/boss_roles.js` sabit veri. Bir Python testi bunu `boss.DEPARTMENTS` / `ALL_ROLES` ile karşılaştırır. Biri değişirse test kırılır. |
| Planlama ve değerlendirme istemleri (işaret satırları, nonce) | Aynı metinler `boss_roles.js`'te; parite testi kapsar. |
| Planı yanıttan çıkarma (`_json_block`: tek blok, yinelenen anahtar yok, boyut sınırı) | JS'e taşınır, aynı vektörlerle test edilir. |

## 4. Tarayıcıya özgü farklar (onay gereken kararlar)

| # | Karar | Önerim | Neden |
|---|---|---|---|
| B1 | Hangi roller kullanılabilir? | `readonly` ve `edit` profilli roller. **Test rolü (`dev`) yok**, plan onu isterse `role_unavailable` olur (bağlayıcıdaki "profil kapalı" davranışının aynısı). | Tarayıcıda komut çalıştırılamaz. |
| B2 | İş emirleri paralel mi çalışsın? | **Hayır, sırayla.** | Ücretsiz katmanların dakika sınırları düşük. Sıralı çalışma daha az hata ve daha öngörülebilir maliyet demek. |
| B3 | Tavanlar | İş başına en fazla 4 iş emri (+1 düzeltme turu, en fazla 2). Adım başına D7 tavanları (20 tur, 40 araç çağrısı, 8.000 çıkış jetonu). **İşin toplamı en fazla 60.000 çıkış jetonu ve 30 dakika.** Her adım saatlik 30 görev sayacına girer. | Para tavanı konamaz (sağlayıcı fiyatı bilinmiyor). Jeton ve süre tavanı bunun yerini tutar. |
| B4 | Planı kim onaylar? | Yalnızca bu tarayıcıdaki kullanıcı (panelde "Onayla"). Onaysız hiçbir uzman çalışmaz. 15 dakika cevap yoksa reddedilmiş sayılır (bağlayıcıdaki gibi). | Aynı güvenlik sözü. |
| B5 | Patron ve uzmanlar hangi modeli kullanır? | İşi veren seçer: tek sağlayıcı ve tek model, Patron ve uzmanlar için ortak. Panelde "Bilgisayar" seçimi "Bu tarayıcı" olduğunda bir model seçicisi çıkar. | En basit hâli. Rol başına model seçimi ileride eklenebilir. |
| B6 | Hafıza (`boss.reset`) | Patron'un konuşma geçmişi sanal klasör başına tarayıcıda tutulur; "Hafızayı sıfırla" onu siler. | Bağlayıcıdaki davranışla aynı. |
| B7 | Tek iş emrini yeniden deneme (`boss.retry_step`) | **İlk sürümde yok.** `bossRetry` özelliği ilan edilmez, panel düğmeyi zaten göstermez. | Kapsamı küçük tutmak için. |
| B8 | İş akışı şablonları (F4-3'ün ikinci yarısı) | **Ayrı iş olarak (F4-3b)** sonra yapılır. Önce Patron (F4-3a). | Patron daha çok kişiye hitap ediyor; şablonların tarayıcıdaki karşılığı ayrı bir tasarım. |

## 5. Güvenlik

- **Plan ve model çıktısı güvenilmezdir.** Plan, yukarıdaki parite testli doğrulayıcıdan geçmeden kullanıcıya
  gösterilmez. Metinler panelde zaten `textContent` ile yazılıyor.
- **Uzmanların araçları** E4'teki sanal klasör araçlarıdır (`list_dir`, `read_file`, `write_file`). Komut ve ağ
  yoktur. İş emrinin `files` alanı bilgi amaçlıdır: bağlayıcıdaki gibi istemde yer alır, araç seviyesinde zorlanmaz.
- **Anahtar** yalnızca `keystore.js`'ten alınır, hiçbir `boss.*` mesajına, plana, rapora ya da hata metnine girmez.
  Mevcut kanarya testleri genişletilir.
- **Oda sınırı (D6) aynen geçerli.** Patron işi yalnızca bu tarayıcıda görünür, odaya gönderilmez.
- **Maliyet:** Para kullanıcının kendi anahtarından çıkar. Plan kartında dolar tahmini yerine "en fazla N iş emri,
  en fazla 60.000 çıkış jetonu" yazar.

## 6. İş bölümü ve büyüklük

| Parça | Dosyalar | Büyüklük |
|---|---|---|
| Ortak kurallar ve parite testleri | yeni `web/js/boss_roles.js`, `tests/boss_plan_vectors.json`, Python ve JS testleri | M |
| Plan doğrulayıcı ve yanıt ayrıştırıcı (JS) | yeni `web/js/browser_boss.js` | M |
| İş yürütücü: planlama → onay → sıralı iş emirleri → değerlendirme → rapor | `browser_boss.js` + `browser_device.js` (mesaj yönlendirme) | M–L |
| Panelde "Bu tarayıcı" için model seçici | `web/js/boss.js` (küçük ekleme) | S |

Toplam yaklaşık L. Antigravity 11 Kasım'a kadar yok. Onaylarsan **F4-3a'yı baştan sona ben yaparım**. Bittiğinde
Antigravity'ye yalnızca bağımsız bir inceleme kalır (Doğrulayıcı rolü, AGENTS.md §6).
