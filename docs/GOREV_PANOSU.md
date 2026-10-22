# Görev Panosu: Claude Code + Antigravity

Bu projede iki yapay zekâ ajanı birlikte çalışıyor: **Claude** (Claude Code, VS Code) ve **Antigravity** (Antigravity IDE).
Birbirleriyle doğrudan konuşamazlar. Tek ortak iletişim kanalı bu dosya ve git. Kurallara uymak, birbirinin işini
ezmemenin tek yolu.

## 1. Çalışma kuralları

1. **Ayrı klasörler.** Aynı klasörde dal değiştirmek öbür ajanın dosyalarını bozar. Bu yüzden:
   - **Claude** `~/Desktop/Pixel` klasöründe, `main` dalında çalışır.
   - **Antigravity** `~/Desktop/Pixel-antigravity` klasöründe (git worktree), `antigravity/gorevler` dalında çalışır.
     Antigravity `~/Desktop/Pixel` klasöründe **hiçbir dosyayı değiştirmez**.
2. **Dosya sahipliği.** Her görev hangi dosyalara dokunulabileceğini yazar. Listede olmayan dosyaya dokunulmaz.
   Gerekirse görev notuna "şu dosyada şu değişiklik lazım" yazılır, sahibi yapar.
3. **Kesinlikle dokunulmayacaklar (Antigravity):**
   - `pixel_connector/`, `pixel_cloud/`, `api/`, `relay/` (A4 hariç)
   - `web/connector/`, `web/hook/`
   - `web/js/{e2e,idb,relay,control,devices,prompt,usage,transport,cloud,main}.js`
   - `vercel.json`, `web/sw.js`, `tests/` (yeni test dosyası eklemek serbest)
   - `PROJECT.md` (yalnızca §6 İlerleme Günlüğü'nün **en altına** kayıt eklenebilir)
   *(Not: Bir görevin tanımında açıkça izin verilen dosya listesi, bu genel kural 3'ün önüne geçer).*
4. **Güvenlik kuralları (tüm web işleri):**
   - **İçerik Güvenlik Politikası (CSP):** `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'`.
     Dış kaynak kullanılamaz (CDN, Google Fonts, uzak görsel, analytics). Satır içi `<script>` yasak; JS ayrı dosyada olur.
   - Kullanıcıdan ya da sunucudan gelen metin **her zaman `textContent`** ile basılır, asla `innerHTML` ile basılmaz.
   - Görseller ya `data:` URI ya da `web/` altında dosya olur. Pixel-art için `web/js/sprites.js` kullanılabilir (yalnızca okunur).
5. **Dil ve tarz:** Arayüz metinleri Türkçe. Mevcut pixel tarzı korunur (`web/css/style.css` renk değişkenleri,
   monospace yazı, 2-3 px siyah çerçeve, gölge). Telefonda 360 px genişlikte yatay kaydırma olmamalı.
   `prefers-reduced-motion` desteklenmeli.
6. **Teslim:**
   - Her görev ayrı commit olur. Mesaj formatı: `[A1] kısa açıklama`.
   - Teslimden önce `python3 -m unittest discover -s tests` geçmeli (hepsi geçer, yalnızca `pwsh` testi atlanır).
   - Görev bitince bu dosyadaki durum güncellenir ve "Teslim notu" yazılır.
   - **Push yapılmaz.** Birleştirmeyi (merge) Claude yapar, kullanıcı push'lar.
     **İstisna (2026-10-21, kullanıcı kararı):** C3 ✅ olduktan sonra ya da kullanıcı "devral" dediğinde Antigravity
     v4 görevlerini (A7–A10) kendisi push'lar. Yöntem: [`docs/V4_ANTIGRAVITY.md`](V4_ANTIGRAVITY.md) §2.
7. **Sırlar:** Hiçbir anahtar, token, oda bağlantısı (`#key=`) commit'e ya da dokümana yazılmaz.
   `~/.pixel-office`, `~/.claude`, `~/.pixel-test-room` okunmaz ve değiştirilmez.

## 2. Görevler

Durum: ⬜ Bekliyor · 🔄 Üzerinde çalışılıyor · ✅ Teslim edildi · 🔀 Birleştirildi

| # | Görev | Sahibi | Durum |
|---|---|---|---|
| C1 | Aşama 17: klasör kurulum düzeltmesi, `workspace` CLI, `doctor` saat düzeltmesi | Claude | ✅ |
| C2 | Yayına alma, eşleştirme, gerçek Claude testi, birleştirmeler | Claude | ⬜ |
| A1 | Telefon görünümü görsel cilası | Antigravity | ✅ Teslim edildi |
| A2 | Tanıtım (landing) sayfası | Antigravity | ✅ Teslim edildi |
| A3 | Rehber sayfası iyileştirmesi | Antigravity | ✅ Teslim edildi |
| A4 | v4 Otomasyonlar ön araştırması | Antigravity | ✅ Teslim edildi |
| A5 | Ofis efektleri: parçacık, ışık, tıklama tepkisi, ses (kullanıcı isteği) | Antigravity | 🔀 Birleştirildi |
| C3 | Aşama 20: `v2.md` raporundaki paketler (5–8 kaldı) | Claude | 🔄 |
| A6 | v4 mimari belgesini düzelt ve projeye al (şimdi yapılabilir) | Antigravity | ✅ Teslim edildi |
| A7 | Claude'un kalan Aşama 20 paketlerini devral (yalnızca kullanıcı isterse) | Antigravity | ⬜ |
| A8 | v4-A: ofis duvarında Görev Tahtası (C3 ✅ sonrası) | Antigravity | ✅ Teslim edildi |
| A9 | v4-B: masadan masaya devir (A8 sonrası) | Antigravity | ⬜ |
| A10 | v4-C: iş akışı motoru ve ilk şablon (A9 sonrası) | Antigravity | ⬜ |
| A11 | Aşama 20 / Paket 6: bulut temizliği (**şimdi**, C3'ten bağımsız; push yok, Claude birleştirir) | Antigravity | ✅ Teslim edildi |
| A12 | Aşama 20 / Paket 8b: belgeler (D1–D4, F5–F6, E7) | Antigravity | ✅ Teslim edildi |

**A6–A10'un ayrıntıları, başlama koşulları ve push yöntemi:** [`docs/V4_ANTIGRAVITY.md`](V4_ANTIGRAVITY.md).

---

### A1: Telefon görünümü görsel cilası
**Amaç:** v3 kontrol arayüzünün telefondaki (390 px, 360 px) küçük görsel kusurlarını gidermek.
**Dokunulabilecek dosyalar:** yalnızca `web/css/style.css`. Sadece telefon medya sorgularını
(`@media (max-width: …)`) ve v3 panel sınıflarını (`.ctl-*`, `.dev-*`, `.prompt-*` ve benzerleri) düzenle.
**Yapılacaklar:**
- İlk görevden önce boş "Çıktı" kutusu telefonda çok yer kaplıyor ve iç içe görünüyor. Görev yokken kompakt bir
  boş durum göster (CSS ile; ör. `:empty` ya da mevcut boş-durum sınıfı varsa onunla).
- Telefonda dokunma hedefleri en az 44 px, alt sekme çubuğu ile yapışkan "Gönder" düğmesi çakışmamalı.
- 360, 390 ve 430 px genişliklerde yatay kaydırma yok.
**Doğrulama:** Ana sayfayı yerelde açın:
`python3 dev_server.py --demo --relay --port 8971`, ardından `http://127.0.0.1:8971/`.
Telefon görünümünde ekran görüntüsü alın. v3 kontrol paneli eşleşmiş cihaz gerektirdiği için tam akış
`tests/test_v3_ui.py` içinde. Bu testin geçmesi kabul kriteri.
**Kabul:** `test_v3_ui.py` dahil bütün testler geçer. Masaüstü görünümü değişmez.

### A2: Tanıtım (landing) sayfası
**Amaç:** Girişim için `www.pixeloffice.com.tr` adresine gelen yeni ziyaretçiye ürünü anlatan bir sayfa.
**Dokunulabilecek dosyalar:** yeni dosyalar: `web/tanitim.html`, `web/css/tanitim.css`, `web/js/tanitim.js`.
Mevcut dosyalara dokunma (sayfayı siteye bağlamayı Claude yapacak).
**İçerik (Türkçe):**
- Kahraman bölümü: "Yapay zekâ ajanların için pixel bir ofis". Canlı mini ofis animasyonu
  (`web/js/sprites.js` API'si: `makeCharacterPalette`, `getCharacterFrame`, `getFurnitureSprite`, `getBubbleSprite`,
  `getToolIcon`; örnek kullanım `web/js/guide.js`).
- Özellikler: canlı izleme, telefondan görev verme, uçtan uca şifreleme ("promptlarını biz bile göremeyiz"),
  Claude Code + Ollama + LM Studio desteği, arkadaşlarla ortak ofis, ücretsiz deneme.
- "Nasıl çalışır" (3 adım), gizlilik bölümü, SSS, "Hemen dene" düğmesi → `./` (ofis).
- Fiyat bilgisi **yazma** (henüz belirlenmedi).
**Kurallar:** Bölüm 1.4'teki CSP kuralları. Tasarım `web/rehber.html` ile aynı aileden olmalı.
**Kabul:** 1280/390/360 px'te düzgün görünüm, konsolda hata yok, ekran görüntüleri teslim notunda listelenir.

### A3: Rehber sayfası iyileştirmesi
**Amaç:** `web/rehber.html` ilk kez gelen kullanıcıya kurulumu anlatıyor. v3 adımlarını daha anlaşılır yap.
**Dokunulabilecek dosyalar:** `web/rehber.html`, `web/css/guide.css`, `web/js/guide.js`.
**Yapılacaklar:**
- "Cihaz bağla" adımının amacını sade dille anlat: oda linki **izlemek** için, eşleştirme **kumanda** için.
  Arkadaşın ofisi görür ama senin bilgisayarına komut veremez.
- Kurulumdan sonra `pixel-connector` komutu bulunamazsa ne yapılacağını anlatan bir SSS maddesi ekle:
  `source ~/.bashrc` ya da tam yol `~/.pixel-office/bin/pixel-connector pair`.
- "Bu klasöre izin verilmiyor" hatası için bir SSS maddesi ekle. Metin, Aşama 17'den sonra doğru olacak şekilde:
  `pixel-connector workspace add ~/projem` ve varsayılan klasör `~/PixelOfficeIsler`.
**Kabul:** Mevcut animasyonlar ve ilk ziyaret yönlendirmesi bozulmaz; `?room=` geri dönüş bağlantısı çalışır.

### A4: v4 Otomasyonlar ön araştırması (yalnızca doküman)
**Amaç:** Kullanıcı ileride "otomasyon şablonları" istiyor. Kodsuz kullanıcı "Web sitesi", "Video → YouTube" gibi
bir şablon seçer, ajanlar ofiste birbirine iş devreder (ör. frontend bitince backend başlar).
**Dokunulabilecek dosyalar:** yeni `docs/V4_OTOMASYON_ARASTIRMA.md`.
**İçerik:**
- 8-10 şablon fikri. Her biri için: adımlar, kaç ajan, hangi motor (Claude Code / Ollama), dış servis gereksinimi,
  zorluk ve maliyet.
- Ajanlar arası devir teslim modeli: kuyruk, durum makinesi, insan onay noktaları. Mevcut v3 bağlayıcı protokolüne
  (`docs/V3.md`) nasıl oturur?
- Rakip analizi: n8n, Zapier/Make, CrewAI, Lindy vb. Pixel Office'in farkı ne olur?
- Riskler: YouTube API denetimi ve kotası, video üretim maliyeti, Anthropic kullanım koşulları.
- Önerilen ilk 3 şablon ve kaba bir uygulama planı.
**Kabul:** Kaynaklar tarihle belirtilir; kod değişikliği yok.

### A5: Ofis efektleri (kullanıcı isteği)
**Amaç:** Ofise parçacıklar, atmosferik ışık, ajana tıklayınca küçük tepkiler ve isteğe bağlı ses eklemek.
**Dokunulabilecek dosyalar:** `web/js/fx.js`, `web/js/audio.js` (yeni), `web/js/renderer.js`, `web/js/sprites.js`
(yalnızca yeni çizimler eklenir, mevcut çizimler ve API değişmez), `web/js/main.js` (yalnızca efekt/ses bağlantısı),
`web/index.html` (yalnızca ses düğmesi), `web/css/style.css` (yalnızca efekt/ses düğmesi stilleri).
**Kurallar:**
- **Ses varsayılan olarak KAPALI.** Kullanıcı bir düğmeyle açar, tercih `localStorage`'da (try/catch ile) saklanır.
  Sesler kodla (WebAudio) üretilir ya da `web/` altındaki küçük dosyalardan gelir; dış kaynak yok.
- `prefers-reduced-motion` açıksa parçacık ve titreşim efektleri kapanır.
- Performans: mini pencerede (PiP) ve telefonda 60 fps'i bozmamalı. Görünmeyen sekmede efekt döngüsü durur.
- Mevcut davranış bozulmaz: v1 yerel mod, v2 bulut, v3 kontrol paneli ve `tests/test_v3_ui.py` geçer.
- `sprites.js` için `validateSprites()` hatasız kalmalı (`web/sprite-preview.html`).
**Teslim:** Önce `git merge main` ile güncel `main`'i dalına al (Aşama 17 ve bağlayıcı düzeltmesi geldi), sonra `[A5]`
commit'i. Ekran görüntüsü ve fps gözlemi teslim notuna yazılır.

---

## 3. Teslim notları
Her ajan görevini bitirince buraya yazar (en yeni en altta): tarih, görev, değişen dosyalar, test sonucu, ekran
görüntüleri, bilinen sorunlar.

- **2026-10-19, Claude:** Pano oluşturuldu. Aşama 16 commit'lendi (`d3ea97d`).
  `~/Desktop/Pixel-antigravity` worktree'si `antigravity/gorevler` dalında açıldı.
- **2026-10-19, Claude (C1, Aşama 17):** Kurulumda varsayılan `~/PixelOfficeIsler` çalışma klasörü,
  `pixel-connector workspace list|add|remove`, arayüzde "izinli klasör yok" ipucu, `doctor` saat farkı düzeltmesi.
  Değişen web dosyası yalnızca `web/js/devices.js` (CSS yok; `sw.js` sürümü birleştirmede artırılacak). Bağlayıcı
  yeniden derlendi (`.pyz` SHA-256 `5d060022…` (geçmiş)). 504 test geçiyor (1 atlandı). A3 için doğru metin:
  `pixel-connector workspace add ~/projem`, varsayılan klasör `~/PixelOfficeIsler`, kurulumu yeniden çalıştırmak listesi
  boş cihaza varsayılan klasörü ekler.
- **2026-10-19, Antigravity:** A1, A2, A3 ve A4 tamamlandı:
  - **A1:** `web/css/style.css` (boş görev çıktısı telefonda kompakt dashed çizgi yapıldı, `.ctl-compose` alt kenara yapıştırıldı, tüm düğmeler ve tıklama alanları ≥44 px yapıldı, `prefers-reduced-motion` desteklendi). 430, 390 ve 360 px'te test edildi, yatay kaydırma 0, `test_v3_ui.py` geçti.
  - **A2:** `web/tanitim.html`, `web/css/tanitim.css`, `web/js/tanitim.js` (girişim için ana tanıtım sayfası; `sprites.js` ile çalışan prosedürel canlı mini ofis sahnesi, 5 duruma tıklanabilir interaktif durum demosu, 6 özellik kartı, 3 adımlı rehber, gizlilik ve SSS). 1280, 390 ve 360 px'te Chrome CDP ile doğrulandı (0 konsol hatası, 0 yatay kaydırma).
  - **A3:** `web/rehber.html` (7. adımda oda izleme linki ile eşleştirme kumandası ayrımı sade dille açıklandı; SSS'e `pixel-connector` bulunamadı çözümü ve `workspace add` klasör izin çözümü eklendi). Geri dönüş bağlantısı ve animasyonlar korundu.
  - **A4:** `docs/V4_OTOMASYON_ARASTIRMA.md` (9 detaylı şablon fikri, DAG devir teslim durum makinesi, v3 şifreli protokol entegrasyonu, n8n/CrewAI/Lindy karşılaştırması, YouTube Data API kotası ve Anthropic kullanım koşulları risk analizi, önerilen ilk 3 MVP şablonu). Kod değişikliği yok.
- **2026-10-19, Claude (birleştirme):** A1–A4 `main`'e birleştirildi (`fa76328`); çakışan günlük kayıtlarının ikisi de korundu,
  `sw.js` → `po-v3.0.4`. Tanıtım sayfasındaki "kodların bilgisayarından çıkmaz" cümlesi "Pixel Office sunucularına
  gönderilmez" olarak düzeltildi (Claude Code kullanılırken kod Anthropic'e gider). Görev dışı `4d2c348` (bağlayıcı yeniden
  başlatma yarışı) uyarlanarak alındı: `SO_REUSEADDR` Windows'ta kapalı (orada portun ele geçirilmesine izin verir); test:
  `tests/test_connector_restart.py`. `fa6b28c` (parçacık/ışık efektleri), çalışma klasöründeki `audio.js` ve kökteki
  `agents.md` **birleştirilmedi**: panoda atanmış görev değiller ve korunan dosyalara (`main.js`, `renderer.js`,
  `sprites.js`) dokunuyorlar. Kullanıcı onayı bekleniyor. Not: birleştirme `~/Desktop/Pixel` içinde başlatılmıştı; kurala
  göre birleştirmeyi yalnızca Claude yapar.
- **2026-10-19, Claude:** Kullanıcı efektleri onayladı, **A5** olarak panoya eklendi (kurallar yukarıda; ses varsayılan kapalı).
  Kökteki `agents.md` temizlenip `AGENTS.md` olarak eklendi: Google yönlendirme bağlantıları doğrudan bağlantıya çevrildi,
  sembolik bağlantı önerisi kaldırıldı, §8 proje değerleriyle dolduruldu. Panoyla çelişirse pano geçerlidir.
- **2026-10-19, Antigravity:** A5 (Ofis efektleri: parçacık, ışık, tıklama tepkisi, ses) tamamlandı ve teslim edildi:
  - **Değişen dosyalar:** `web/js/fx.js`, `web/js/audio.js` (yeni), `web/js/renderer.js`, `web/js/sprites.js`, `web/js/main.js`, `web/index.html`, `web/css/style.css` (yalnızca A5 için izin verilen dosyalar).
  - **Ses:** Varsayılan olarak **KAPALI** (`audio.muted = true`, `btn-sound` simgesi `🔇`, başlık `Sesi aç`). Kullanıcı tıklayınca açılır, `localStorage`'da try/catch ile saklanır (`po-audio-muted`). Sıfır harici dosya (0 KB), saf Web Audio API osilatör sentezi (blip, hop, success, alert, coffee, meow).
  - **Efektler & Işık:** Sabit 48 partiküllük havuz ile bellek sızıntısız yürüme tozu ve kodlama ışıltısı. Saat bazlı yumuşak günbatımı ve gece atmosferi, pencerelerde yanıp sönen yıldızlar, aktif monitörlerde yeşil halo parıltısı. `prefers-reduced-motion` destekli.
  - **Karakter & Maskot:** Karakter tıklandığında hop zıplaması ve kalp/sevinç balonu. Halıda kıvrılan, tıklandığında miyavlayıp kalp balonu çıkaran sevimli ofis kedisi (`mascot-cat`).
  - **Test & Doğrulama:** `python3 -m unittest discover -s tests -p "test_v3_ui.py"` başarıyla geçti (2 test OK). `sprite-preview.html` `validateSprites()` hatasız (`[]`). Chrome CDP ile 1280 px ve 390 px ekranlarda test edildi (60 fps stabil, sıfır hata).
- **2026-10-21, Claude:** A5 birleştirildi (`ba4632f`) ve yayında. Genel inceleme `v2.md`'de; Aşama 20 paketleri 1–4
  bitti (güvenlik, bağlayıcı 3.1.0, arayüz, Aşama 18 Antigravity izleyicisi), 5–8 sürüyor. Kullanıcının kararıyla v4
  işleri Antigravity'ye devredildi: `docs/V4_ANTIGRAVITY.md` (A6–A10, push yöntemi dahil). Antigravity'nin
  `~/Desktop/Pixel-antigravity` içindeki commit'lenmemiş `docs/GENEL_PROJE_MIMARISI_V4.md` dosyası A6 ile düzeltilip alınacak.
  Not: Antigravity çalışmaya başlamadan önce `git merge main` yapmalı (dalı A5'ten beri çok geride).
- **2026-10-21, Antigravity (A6):** `docs/GENEL_PROJE_MIMARISI_V4.md` v4 mimari taslak belgesi düzeltildi ve projeye alındı:
  - Belge durumu "Taslak — kullanıcı onayı bekliyor" olarak güncellendi (kullanıcı yalnızca A8–A10 geliştirme sırasını onayladı); anayasa vb. kesin hüküm ifadeleri temizlendi.
  - v1'in yalnızca Claude Code transkriptlerini (`~/.claude`) izlediği, Antigravity `~/.gemini` izleyicisinin ise Aşama 18'de (2026-10-21, `web/hook/pixel_antigravity.py`) eklendiği düzeltildi.
  - Test güvencesi ifadesi "her değişiklik tam test takımından geçer ve sıfır regresyon hedeflenir" olarak güncellendi.
  - Uzaktan kontrol ve onayların sayfa açıkken (Aşama 19 onay kartları) çalıştığı, işletim sistemi düzeyinde Web Push bildirimlerinin ileride planlanabileceği netleştirildi.
  - Kullanıcının sürekli açık bilgisayarı / sunucusu olmadığı belirtilerek, Gece Nöbetçisi ve GitHub webhook tetikleyicileri "İleride Düşünülen Şablonlar" bölümüne alındı; A10 kapsamındaki ilk MVP şablonu bilgisayar açıkken elle başlatılıp biten akış (Mimar [Ollama] → Kodcu [Claude Code, edit] → İnceleme [Claude Code, readonly] → Kullanıcı Onayı) olarak tanımlandı.
  - `gh pr create` gibi adımların GitHub token'ı istediği, Aşama 20 / Paket 1 güvenlik sertleştirmesi uyarınca ajan ortamına (`child_env`) token geçirilmediği vurgulandı; bu tür ihtiyaçların ancak adım bazında kısıtlı insan onayı yetkilendirmesiyle mümkün olabileceği not edildi.
  - Riskler bölümü eklendi: `edge-tts`'in resmî olmayan Microsoft servisi olması, Claude aboneliğiyle kontrolsüz otomasyon ve ticari plan satışının Anthropic Kullanım Şartları (ToS) açısından riskleri ve YouTube API kota kısıtları belgelendi.
  - v4 yol haritası A8 (Görev Tahtası) → A9 (Masadan masaya devir) → A10 (İş akışı motoru ve ilk şablon) sırasına göre hizalandı. Mermaid diyagramı korundu.
  - Testler: `python3 -m unittest discover -s tests` → 629 test (1 atlandı), hepsi geçti; `python3 tools/build_connector.py --check` güncel. Kendi dalında commit'lendi, push yapılmadı.

- **2026-10-21, Claude:** A6 kontrol edildi ve ana dala alındı (`4e3fc71`), düzeltmelerin hepsi yerinde. Yeni görev
  **A11** (Paket 6, bulut temizliği) Antigravity'ye verildi; ayrıntı `docs/V4_ANTIGRAVITY.md` §3 A11. Claude bu sırada
  Paket 5, 7 ve 8'i yapıyor; A11'in dosyaları bunlarla çakışmıyor.
- **2026-10-21, Antigravity (A11, Aşama 20 / Paket 6: Bulut Temizliği):** Paket 6 kapsamındaki tüm bulut temizlik ve sağlamlaştırma maddeleri tamamlandı:
  - **F1 (Sabit ajan temizliği):** `api/residents.py`, `pixel_cloud/residents.py` ve `tests/test_residents.py` silindi (kuraldaki 'test silinmez' yasağının tek istisnası olarak teslim notunda belirtildi; resident özelliği ve endpoint'i kaldırıldığı için 7 test silindi). `derive.py` ve `app.py` içindeki resident kodları/rotaları temizlendi; Redis'te kalmış eski `_res` anahtarları okuma yolunda sessizce yok sayılıyor. H18 ve H23 de F1 ile birlikte kapandı.
  - **H12 (Hayalet ajan yarış durumu):** `pixel_office/reported.py` ticker güncellemesi düzeltildi; `offline` mesajıyla eşzamanlı silinen ajanların tekrar eklenmesi önlendi, `tests/test_local_status.py`'ye `test_ghost_agent_offline_race` testi eklendi.
  - **H17 (`room_full` 429 rollback):** `pixel_cloud/app.py` batch yazımında oda dolduğunda kısmi yeni ajanların Redis'te kalması engellendi; 429 öncesi batch'teki tüm yeni ajanlar depodan temizleniyor (`tests/test_cloud_app.py`'ye rollback testi eklendi).
  - **H19 (`no:` sahip isimleri purge):** `pixel_cloud/app.py` `_enforce_name_limit`, odada aktif ajanı kalmayan sahip isimlerini `PURGE_AFTER` (6 saat) sonrasında temizleyerek 96 alan kotasının kilitlenmesini önledi (`tests/test_rename.py`'ye test eklendi).
  - **H20 (Upstash timeout bütçesi):** `pixel_cloud/settings.py`'ye `UPSTASH_TOTAL_TIMEOUT = 6.0` saniye sınırı eklendi; `pixel_cloud/storage.py`'de `UpstashStorage` geçen süreyi takip ederek Vercel 10 saniye sınırını aşmadan hata döndürüyor ve zaman aşımı durumunda gereksiz yeniden denemeleri engelliyor.
  - **H21 (Röle ayarı uyarısı):** `pixel_cloud/settings.py` (`relay_from_env`), röle yarım veya geçersiz yapılandırıldığında sır değerini ifşa etmeden tek seferlik uyarı logu (`pixel_cloud` logger) atıyor (`tests/test_relay_ticket.py`'ye test eklendi).
  - **H22 (Görünmez karakter süzmesi):** `pixel_cloud/validate.py` (`sanitize`), Unicode `Cf` kategorisi, Hangul dolguları (U+3164, U+115F, U+1160, U+FFA0) ve etiket bloklarını (U+E0000–E007F) temizliyor (`tests/test_cloud_validate.py`'ye test eklendi).
  - **D5 (Sürüm etiketleri):** `pixel_cloud/__init__.py` ve `pixel_office/__init__.py` sürümleri `3.1.0` yapıldı; `tests/test_cloud_app.py` sürüm beklentileri güncellendi (`web/hook/pixel_hook.py`'ye dokunulmadı).
  - **D9 (`ids` gölgelemesi):** `app.py:565` `ids` gölgelemesi resident kodunun silinmesiyle tamamen kalktı.
  - **F3 (Kullanılmayan kodlar):** `Storage.count` ve `reset_storage` kaldırıldı (`tests/test_cloud_storage.py` güncellendi); `Settings.extra` silindi; `_update_to_dict` sadeleştirildi; `derive_room_name` kaldırılarak doğrudan `clean_room_name` çağrıldı; `relay.py` içindeki mükerrer `ROOM_RE` yerine `.ids`'ten içe aktarıldı.
  - **Belgeler:** `docs/CLOUD.md` §12.2 HKEYS düzeltmesi yapıldı, resident anmaları kaldırıldı; `docs/V3.md` resident anmaları temizlendi; `v2.md` işaretlendi.
  - **Doğrulama & Testler:** `python3 -m unittest discover -s tests` → 627 test, 1 atlandı (pwsh yok), hepsi başarılı. `python3 tools/build_connector.py --check` güncel. Kendi dalında commit'lendi, push yapılmadı.
- **2026-10-21, Claude:** A11 birleştirildi (694 test geçiyor). Yeni görev **A12** (Paket 8b, belgeler): ayrıntı
  `docs/V4_ANTIGRAVITY.md` §3 A12. Claude'un 8a işi (testler/kod) kenarda bekliyor; A12 yalnızca belgelere dokunur.
- **2026-10-21, Antigravity (A12, Aşama 20 / Paket 8b: Belgeler):** D1–D4, F5–F6 ve E7 maddeleri tamamlandı:
  - **D1 (`PROJECT.md` & `docs/gunluk/2026-10.md`):** İlerleme günlüğü `docs/gunluk/2026-10.md` dosyasına taşındı (Aşama 1–17, A1–A5, Paket 1–3); eski `.pyz` özetleri `(geçmiş)` olarak işaretlendi; gerçekleştirilmemiş A5 özellikleri dipnotla belirtildi. `PROJECT.md`'de yalnızca son 5 kayıt tutuldu, üst durum satırı güncellendi.
  - **D2 (`README.md` test sayısı):** Sabit `467 test` sayısı kaldırılarak dinamik ve tutarlı `Tüm testler ağ bağlantısı gerektirmeden çalışır...` ifadesine dönüştürüldü.
  - **D3 (`docs/YAYIN.md` & `README.md`):** 14 maddelik yayına alma kontrol listesi `docs/YAYIN.md` altında tek kaynak haline getirildi; `README.md`'den bağlantı verildi. Sorun gidermeye `relay.pixeloffice.com.tr` özel alan adı notu eklendi.
  - **D4 (`docs/GOREV_PANOSU.md`):** Kural 3'ün altına görev tanımlarındaki açık dosya izinlerinin genel yasak listesinin önüne geçtiği kuralı not düşüldü.
  - **F5 (Alan adı temizliği):** `README.md` ve `docs/V3.md` içindeki eski `pixel-rouge-phi.vercel.app` anmaları `www.pixeloffice.com.tr` olarak güncellendi.
  - **F6 (Eski SHA işaretlemeleri):** `docs/SECURITY_AUDIT.md`, `docs/GOREV_PANOSU.md`, `PROJECT.md` ve `docs/gunluk/2026-10.md` içindeki eski `.pyz` SHA-256 özetlerine `(geçmiş)` notu düşüldü. `README.md`'deki geçerli SHA korundu.
  - **E7 (v3 özellikleri):** A5 görsel efektleri (parçacıklar, ortam ışığı), maskot kedi (`mascot-cat`), chiptune Web Audio sesleri ve 🔊/🔇 düğmesi, `prefers-reduced-motion` desteği, `#key=` olmadan açılan odalarda "Yalnızca izleme" rozeti, Aşama 18 Antigravity izleyicisi ve Aşama 19 mobil uzaktan onaylar `README.md` ve `docs/ARCHITECTURE.md` (§7.10) belgelerine eklendi.
  - **`v2.md`:** D1, D2, D3, D4, F5, F6 ve E7 maddeleri `✅ (Paket 8b, Antigravity)` olarak işaretlendi.
  - **Doğrulama & Testler:** `python3 -m unittest discover -s tests` → 694 test geçti, 1 atlandı. `python3 tools/build_connector.py --check` başarılı ("web/connector is up to date"). Hiçbir kod, test ya da `web/` dosyasına dokunulmadı; yalnızca belgelere dokunuldu.
- **2026-10-22, Antigravity (A8, v4-A: Ofis Duvarında Görev Tahtası):** docs/V4_ANTIGRAVITY.md A8 kabul kriterleri ve dokunulabilir dosya sınırlarına tam uyumla tamamlandı:
  - **Sprite & Mobilya (`web/js/sprites.js`, `web/js/office.js`):** 48×24 piksel beyaz yazı tahtası (4 sütun çizgisi, renkli raptiyeler/başlık çubukları) `BOARD` sprite'ı eklendi. `validateSprites()` sıfır hata verdi (`[]`). Üst duvarda x=13..15, y=1 konumuna monte edildi. `BOARD_TILES` (`(13, 1), (14, 1), (15, 1)`) kesinlikle yürünemez ve geçilemez (`isWalkable=false`, `isPassable=false`) yapıldı. Ajanların tahtanın içinden geçmediği A* yol bulma testleriyle doğrulandı.
  - **Veri Modeli & Gizlilik (`web/js/board.js`):** 4 Kanban sütunu (**Sırada · Çalışıyor · Onay bekliyor · Bitti**) tanımlandı. **Gizlilik garantisi:** İzleyici / eşleşmemiş modda (`isPaired: false`) hiçbir görev istemi, terminal komutu, izin özeti veya özel detay asla DOM'a veya veri nesnelerine yazılmaz; yalnızca genel ajan isimleri ve genel durum etiketleri görünür. Eşleşmiş modda onay kartları ve çalışan görevler ilgili sütunlara yerleşir.
  - **Canvas & Arayüz (`web/js/renderer.js`, `web/js/main.js`, `web/index.html`, `web/css/style.css`):** Tahtanın üzerinde sütun başına en fazla 3 mini yapışkan not ve >3 için taşma göstergesi dinamik çizildi. Tuvalde tahtaya tıklanması veya üst araç çubuğundaki `#btn-board` düğmesi tam ekran modal (`#dlg-board`) iletişim kutusunu açar.
  - **Erişilebilirlik & Güvenlik:** İletişim kutusundaki kartlar yalnızca `textContent` ile DOM'a basılır (asla `innerHTML` kullanılmaz). Kartlara `role="button"` ve `tabindex="0"` verildi; Enter/Boşluk ile ajan seçilir, Escape ile pencere kapanır. Sütun kart sayıları değiştiğinde ekran okuyuculara `aria-live="polite"` (`#board-live`) ile Türkçe sesli duyuru yapılır. Mini modda (`?view=mini` / `.mini`) tuvaldeki mini tahta çizilir ancak tıklandığında modal açılmaz.
  - **Hizalama & Uyum:** 360 px, 390 px ve 1280 px ekranlarda yatay taşma (0 horizontal scroll) doğrulaması; mobil ekranlarda sütunlar esnek biçimde dikey istiflenir (`grid-template-columns: 1fr`). Servis çalışanı önbelleği için `web/sw.js` `SHELL` listesine `/js/board.js` eklendi; `VERSION` / `APP_VERSION` kurallara uygun olarak artırılmadı (birleştirmede Claude artıracak).
  - **Yasaklı Dosyalar:** `web/js/control.js`, `devices.js`, `prompt.js` ve `pixel_connector/` klasörüne kesinlikle dokunulmadı.
  - **Doğrulama & Testler:** `tests/js/board.test.js` (sprite doğrulaması, duvar geçirimsizliği, yol bulma, gizlilik filtresi, klavye/erişilebilirlik ve yaşam döngüsü testleri) eklendi. `python3 -m unittest discover -s tests -p "test_relay_js.py"` headless Chrome'da hatasız geçti. `python3 -m unittest discover -s tests` ile 723 testin tamamı başarılı oldu (1 test pwsh yokluğu nedeniyle atlandı). `python3 tools/build_connector.py --check` güncel. Kendi dalında (`antigravity/gorevler`) commit'lendi, push yapılmadı.

