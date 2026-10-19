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
   - Teslimden önce `python3 -m unittest discover -s tests` geçmeli (şu an 481 test, 1 atlanmış).
   - Görev bitince bu dosyadaki durum güncellenir ve "Teslim notu" yazılır.
   - **Push yapılmaz.** Birleştirmeyi (merge) Claude yapar, kullanıcı push'lar.
7. **Sırlar:** Hiçbir anahtar, token, oda bağlantısı (`#key=`) commit'e ya da dokümana yazılmaz.
   `~/.pixel-office`, `~/.claude`, `~/.pixel-test-room` okunmaz ve değiştirilmez.

## 2. Görevler

Durum: ⬜ Bekliyor · 🔄 Üzerinde çalışılıyor · ✅ Teslim edildi · 🔀 Birleştirildi

| # | Görev | Sahibi | Durum |
|---|---|---|---|
| C1 | Aşama 17: klasör kurulum düzeltmesi, `workspace` CLI, `doctor` saat düzeltmesi | Claude | ✅ |
| C2 | Yayına alma, eşleştirme, gerçek Claude testi, birleştirmeler | Claude | ⬜ |
| A1 | Telefon görünümü görsel cilası | Antigravity | ⬜ |
| A2 | Tanıtım (landing) sayfası | Antigravity | ⬜ |
| A3 | Rehber sayfası iyileştirmesi | Antigravity | ⬜ |
| A4 | v4 Otomasyonlar ön araştırması | Antigravity | ⬜ |

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

---

## 3. Teslim notları
Her ajan görevini bitirince buraya yazar (en yeni en altta): tarih, görev, değişen dosyalar, test sonucu, ekran
görüntüleri, bilinen sorunlar.

- **2026-10-19, Claude:** Pano oluşturuldu. Aşama 16 commit'lendi (`d3ea97d`).
  `~/Desktop/Pixel-antigravity` worktree'si `antigravity/gorevler` dalında açıldı.
- **2026-10-19, Claude (C1, Aşama 17):** Kurulumda varsayılan `~/PixelOfficeIsler` çalışma klasörü,
  `pixel-connector workspace list|add|remove`, arayüzde "izinli klasör yok" ipucu, `doctor` saat farkı düzeltmesi.
  Değişen web dosyası yalnızca `web/js/devices.js` (CSS yok; `sw.js` sürümü birleştirmede artırılacak). Bağlayıcı
  yeniden derlendi (`.pyz` SHA-256 `5d060022…`). 504 test geçiyor (1 atlandı). A3 için doğru metin:
  `pixel-connector workspace add ~/projem`, varsayılan klasör `~/PixelOfficeIsler`, kurulumu yeniden çalıştırmak listesi
  boş cihaza varsayılan klasörü ekler.
