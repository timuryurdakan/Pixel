# Pixel Office — Mevcut Özellikleri Geliştirme ve Küresel Evrensel Platforma Dönüşüm Araştırma Raporu

* **Hazırlayan:** Antigravity (Araştırma & Mimari Analiz)
* **Tarih:** 2026-10-29
* **Mevcut Sürüm Tabanı:** Bağlayıcı `3.8.1` · Web `po-v4.0.7` (Aşama 1–20 + v4 A1–A12 + Patron Odası P1–P9 tamamlanmış durum)
* **Temel Kısıtlar:**
  1. **Sunucu ve Altyapı Maliyeti = $0** (Vercel Hobby, Upstash Redis Free 500K komut/ay, Cloudflare Workers Free + Durable Objects WebSocket Hibernation sınırları içinde kalınacak; sıfır çalışma zamanı bağımlılığı ve `default-src 'self'` CSP korunacak).
  2. **Çoklu Ajan Koordinasyonu:** `AGENTS.md` ve `docs/GOREV_PANOSU.md` kurallarına uygun olarak, dosya çakışması yaratmadan **Claude (Koordinatör / Güvenlik / Çekirdek)** ve **Antigravity (Özellik / UI / Motor & Şablon Genişletmeleri)** arasında paralel yürütülebilir paketlere bölünebilir tasarım.

---

## İçindekiler

1. [Mevcut Durum Tespiti ve Küresel Pazar Analizi (Blue Ocean Fırsatı)](#1-mevcut-durum-tespiti-ve-küresel-pazar-analizi-blue-ocean-fırsatı)
2. [BÖLÜM 1: Var Olan Özelliklerin, Arayüzün (UI/UX) ve Deneyimin Geliştirilmesi](#2-bölüm-1-var-olan-özelliklerin-arayüzün-uiux-ve-deneyimin-geliştirilmesi)
   * [1.1. İlk Ziyaretçi Akışı (`main.js`), `tanitim.html` ve Canlı Etkileşimli Demo Ofisi](#11-ilk-ziyaretçi-akışı-mainjs-tanitimhtml-ve-canlı-etkileşimli-demo-ofisi)
   * [1.2. Görsel Sahne, Kamera, Atmosfer ve Pixel-Art Etkileşimleri (`renderer.js`, `fx.js`, `sprites.js`, `boss_scene.js`)](#12-görsel-sahne-kamera-atmosfer-ve-pixel-art-etkileşimleri-rendererjs-fxjs-spritesjs-boss_scenejs)
   * [1.3. Patron Odası (`boss.py`, `boss.js`, `boss_scene.js`) Geliştirmeleri](#13-patron-odası-bosspy-bossjs-boss_scenejs-geliştirmeleri)
   * [1.4. İş Akışı Şablonları (`workflows.py`, `workflows.js`) ve Masadan Masaya Devir (`handoff`)](#14-iş-akışı-şablonları-workflowspy-workflowsjs-ve-masadan-masaya-devir-handoff)
   * [1.5. Görev Kutusu (`prompt.js`), Bildirimler ve Çıktı Dışa Aktarma](#15-görev-kutusu-promptjs-bildirimler-ve-çıktı-dışa-aktarma)
   * [1.6. Cihaz Eşleştirme (`devices.js`) — QR Kod ile 3 Saniyede Mobil Eşleşme](#16-cihaz-eşleştirme-devicesjs--qr-kod-ile-3-saniyede-mobil-eşleşme)
3. [BÖLÜM 2: "Sadece Claude Kullanan Yazılımcılar"dan "A'dan Z'ye Her İnsana ve Her Tür Yapay Zekâya Hitap Eden Evrensel Platform"a Dönüşüm ($0 Maliyet Mimarisi)](#3-bölüm-2-sadece-claude-kullanan-yazılımcılardan-adan-zye-her-insana-ve-her-tür-yapay-zekâya-hitap-eden-evrensel-platforma-dönüşüm-0-maliyet-mimarisi)
   * [2.1. Sütun 1: "Sıfır Kurulum" Tarayıcı-İçi Ajan Motoru (`Browser-Native Office` — Sunucu Maliyeti: $0)](#21-sütun-1-sıfır-kurulum-tarayıcı-içi-ajan-motoru-browser-native-office--sunucu-maliyeti-0)
   * [2.2. Sütun 2: Her Tür Yapay Zekâ İçin Evrensel Motor ve Araç (Tool-Calling) Katmanı](#22-sütun-2-her-tür-yapay-zekâ-için-evrensel-motor-ve-araç-tool-calling-katmanı)
   * [2.3. Sütun 3: A'dan Z'ye Her İnsana Hitap Eden "Evrensel Ofis Departmanları ve Meslek Paketleri"](#23-sütun-3-adan-zye-her-insana-hitap-eden-evrensel-ofis-departmanları-ve-meslek-paketleri)
   * [2.4. Sütun 4: "Canlı Eser Vitrini" (Artifact & Document Preview)](#24-sütun-4-canlı-eser-vitrini-artifact--document-preview)
   * [2.5. Sütun 5: Küresel Dil (`i18n`), Pomodoro/Lo-Fi Ambiyans ve Viral Büyüme Motoru](#25-sütun-5-küresel-dil-i18n-pomodorolo-fi-ambiyans-ve-viral-büyüme-motoru)
4. [BÖLÜM 3: Claude ve Antigravity İçin Önerilen Görev Bölümü (Görev Panosu Taslağı)](#4-bölüm-3-claude-ve-antigravity-için-önerilen-görev-bölümü-görev-panosu-taslağı)

---

## 1. Mevcut Durum Tespiti ve Küresel Pazar Analizi (Blue Ocean Fırsatı)

### 1.1. Projenin Mevcut Gücü
Kod tabanı mühendislik açısından olağanüstü bir olgunluğa ulaşmış durumda:
* **Sıfır bağımlılık:** Python standart kütüphanesi + tarayıcıda saf ES modülleri + prosedürel Canvas piksel sanatı + Web Audio API ses sentezi.
* **Siber güvenlik ve mahremiyet:** Uçtan uca şifreleme (`P-256 ECDH` + `AES-256-GCM`), PAKE + 6 haneli SAS, sıkı izin profilleri (`readonly`, `edit`, `dev`, `auto`) ve `EXEC_SENSITIVE_DENY` / `SECRET_READ_DENY` korumaları.
* **Canlı Ofis Metaforu:** Karakterlerin kapıdan girmesi, masada yazması, birbirine kağıt/dosya taşıması (`handoff`), Patron Odası'ndan (`boss.py` / `boss_scene.js`) emir alıp rapor teslim etmesi, iş bitince kahve içmesi ve uyuması.

### 1.2. Temel Darboğaz: Neden Şu An Sadece Küçük Bir Kitleye Hitap Ediyor?
1. **Terminal ve Kurulum Duvarı:** Siteye giren bir kullanıcının kendi ajanını çalıştırması için bilgisayarına `curl -fsSL ... | sh` ile `pixel-connector` veya `pixel_hook.py` kurması, terminalde `pixel-connector pair` çalıştırması gerekiyor. Yazılımcı olmayan kullanıcıların %99'u terminal gördüğü anda sayfayı kapatır.
2. **Sadece Claude Code ve Yazılımcı Odaklılık:**
   * Arayüz metinleri (`index.html`, `rehber.html`, `tanitim.html`) *"Claude Code oturumlarını izleyin"* ekseninde.
   * Patron Odası (`pixel_connector/boss.py` satır 43: `ROLE_ENGINES = ("claude",)`) yalnızca Claude Code motoruyla çalışıyor ve uzman rolleri (`frontend`, `backend`, `test`, `docs`, `review`) tamamen yazılım geliştirmeye sabitlenmiş.
   * Yerel/API motorları (`ollama.py`, `openai_compat.py`) yalnızca düz metin sohbeti yapıyor; dosya okuma/yazma, web araştırması veya Patron Odası uzmanlığı yetenekleri yok.

### 1.3. Küresel Pazar Araştırması (2026)
Dünyadaki mevcut alternatifler incelendiğinde:
* **Pixel Agents / Claude Squad / AgentOffice:** Yalnızca yazılımcılara (VS Code eklentisi veya CLI) hitap ediyor. Normal insanların kullanması imkânsız.
* **Relevance AI / MindStudio / Lindy / CrewAI:** Çoklu ajan iş akışı sunuyorlar ama sıkıcı kurumsal akış diyagramlarından ibaretler ve kullanıcıdan aylık **$29–$99 SaaS aboneliği** istiyorlar.
* **Pixel Office'in Eşsiz Konumu:** Hem sıcak, oyunlaştırılmış bir **2D Pixel-Art Ofis & Patron** deneyimi sunan, hem **yazılımcı olmayan her insanın (öğrenci, içerik üreticisi, öğretmen, pazarlamacı, girişimci, yazar) kurulumsuz kullanabildiği**, hem de **BYOK (Kendi Anahtarını Getir) / Tarayıcı-İçi / Yerel-Öncelikli** mimarisiyle **platform sahibine sunucu maliyeti $0 olan** dünyadaki ilk ve tek platform olabilir.

---

## 2. BÖLÜM 1: Var Olan Özelliklerin, Arayüzün (UI/UX) ve Deneyimin Geliştirilmesi

Mevcut kod tabanındaki özelliklerin daha pürüzsüz, etkileyici ve eksiksiz çalışması için tespit edilen somut geliştirmeler:

### 1.1. İlk Ziyaretçi Akışı (`main.js`), `tanitim.html` ve Canlı Etkileşimli Demo Ofisi
* **Tespit Edilen Sorun:** `web/js/main.js` (satır 568) içindeki `shouldRedirectToGuide()` fonksiyonu, siteye (`pixeloffice.com.tr/`) ilk kez giren ve `?room=` parametresi olmayan ziyaretçiyi **anında `/rehber.html?ilk=1` sayfasına yönlendiriyor**. Ziyaretçi daha ofisi, yürüyen karakterleri, Patron Odası'nı, sesleri ve efektleri deneyimlemeden terminal komutlarıyla karşılaşıyor. Ayrıca `web/tanitim.html` sayfasına ana akıştan kimse ulaşamıyor.
* **Çözüm Önerisi:**
  1. **Anında Canlı Demo Ofisi Karşılaması:** İlk kez gelen ziyaretçi `/rehber.html` yerine doğrudan **zenginleştirilmiş Canlı Demo Modu** içinde karşılanmalı veya üstte şık bir "Canlı Demo Ofisi — Karakterlere ve Patron Odası'na tıklayarak hemen dene!" bandı yer almalı.
  2. **Demo Modunda v3/v4 ve Patron Odası Simülasyonu:** Şu an `DemoSource` (`main.js` satır 291) yalnızca v1 durumlarını (`working`, `thinking`, `idle`, `sleeping`) rastgele döndürüyor. Demo modundayken ziyaretçi:
     * **Patron Odası**'na tıkladığında örnek bir Patron senaryosunu (*"Yeni ürün tanıtım sayfası ve içerik planı hazırla"*) tek tıkla başlatıp, 3 uzman ajanın odaya girip Patron'dan mavi dosya (`folder`) alarak masalarına yürüdüğünü ve canlı rapor ürettiğini izleyebilmeli!
     * **Görev Panosu (`board.js`)** ve **Şablonlar (`workflows.js`)** çekmecelerini örnek verilerle etkileşimli olarak deneyebilmeli.
  3. **Harekete Geçirici Üst Bar (CTA):** Ziyaretçi demoyu deneyimledikten sonra üst bardaki parlayan **"🚀 Kendi Ofisini Kur / Başla"** ve **"✨ Özellikler (`tanitim.html`)"** butonlarıyla kendi odasını saniyeler içinde açmalı.

### 1.2. Görsel Sahne, Kamera, Atmosfer ve Pixel-Art Etkileşimleri (`renderer.js`, `fx.js`, `sprites.js`, `boss_scene.js`)
* **Kodda Yazılmış Ama Arayüze Bağlanmamış Özelliklerin Aktifleştirilmesi:**
  1. **Kamera Zoom Kontrolü:** `web/js/renderer.js` (satır 131) içinde `setZoom(z)` (`1x–6x` yakınlaştırma) metodu hazır yazılmış fakat hiçbir UI butonuna veya fare tekerleğine bağlanmamış. Sahnenin sağ alt köşesine küçük piksel butonlar (`🔍+`, `🔍-`, `Sığdır`) ve `Ctrl + Scroll` / mobilde pinch-to-zoom desteği eklenmeli.
  2. **Atmosfer & Gece/Gündüz Seçici:** `web/js/fx.js` (satır 171) içindeki `LightingEngine` sınıfında `this.mode = 'auto' | 'day' | 'sunset' | 'night'` hazır; ancak kullanıcı arayüzden gece modunu (pencerede yıldızlar + monitör ışık haleleri) veya gün batımını elle seçemiyor. Üst bara `☀️ / 🌇 / 🌙` atmosfer geçiş düğmesi eklenmeli.
  3. **Gerçek Ofis Kedisi (Maskot Sprite'ı):** `web/js/audio.js` (satır 190) içinde `playMeow()` (*"Cute chiptune cat meow when clicking the office mascot"*) hazır yazılmış; fakat ofiste gerçek bir kedi karakteri yok (şu an bir ajana 4. tıklamada ajan miyavlıyor — `main.js` satır 438). Halının veya dinlenme köşesinin üzerine uyuyan/mırlayan tatlı bir **Piksel Ofis Kedisi** (`office_cat` sprite'ı) eklenmeli; tıklandığında kalp baloncuğu çıkarıp `playMeow()` çalmalı.
  4. **Etkileşimli Ofis Eşyaları:** Kahve makinesine tıklandığında `playCoffee()` ve buhar efekti, su sebiline veya kitaplığa tıklandığında küçük piksel tepkileri/ipuçları verilmesi ofisi yaşayan bir oyun dünyasına dönüştürür.
  5. **Patron Odası İsim Plakası ve Hover İpucu:** `web/js/boss_scene.js` içindeki `drawPatronRoom()` Patron karakterini çiziyor ama fareyle üzerine gelindiğinde (`hover`) veya başının üstünde `"👔 PATRON"` etiketi/durum ipucu görünmüyor.
  6. **Karakter Özelleştirme (Aksesuarlar & Renk Seçimi):** `web/js/sprites.js` içindeki `makeCharacterPalette()` yalnızca rastgele renk üretiyor. Ajan oluşturma/düzenleme ekranına **gözlük, kulaklık, şapka, bere, kravat/fiyonk** gibi piksel aksesuar katmanları ve tişört rengi seçici eklenebilir.
  7. **Canlı Konuşma Baloncukları (Mini Stream Ticker):** Ajan çalışırken başının üstünde sadece araç ikonu yerine, ürettiği yanıtın son birkaç kelimesi (`"Testler geçti ✓"`, `"Rapor yazılıyor…"`) küçük bir konuşma balonunda (`speech`) 2-3 saniye görünebilir.

### 1.3. Patron Odası (`boss.py`, `boss.js`, `boss_scene.js`) Geliştirmeleri
* **Mevcut Sınırlamalar:**
  * `pixel_connector/boss.py` satır 43'te `ROLE_ENGINES = ("claude",)` sabitlenmiş; Ollama ve OpenAI uyumlu motorlar Patron Odası'nda hiç kullanılamıyor.
  * Uzmanlardan biri hata verdiğinde (`failed` veya `timeout`), tüm işi baştan başlatmak gerekiyor.
* **Geliştirme Önerileri:**
  1. **Çoklu Motor Desteği:** Patron planlayıcısı ve uzman rolleri `openai` ve `ollama` motorlarıyla da çalışabilmeli (JSON şema doğrulaması zaten `boss.py` içindeki `parse_plan_output()` ile sınırda yapılıyor!).
  2. **Adım Bazlı Yeniden Deneme (`boss.retry_step`):** Patron ekranında (`boss.js`) başarısız olan tek bir uzman kartının yanında **"🔄 Bu Adımı Tekrar Dene"** butonu olmalı; başarılı olmuş diğer uzmanların emeği ve harcanan token'lar boşa gitmemeli.
  3. **Rapor Ekranında Dosya Özeti ve İndirme:** Patronun final raporunda (`boss.report`) değiştirilen/oluşturulan dosyaların yanında satır farkı özeti ve raporu tek tıkla `.md` dosyası olarak indirme / kopyalama butonu bulunmalı.

### 1.4. İş Akışı Şablonları (`workflows.py`, `workflows.js`) ve Masadan Masaya Devir (`handoff`)
* **Mevcut Sınırlamalar:**
  * `pixel_connector/workflows.py` içinde yerleşik olarak sadece **1 adet şablon** (`statik-site`) var.
  * Kullanıcının yeni şablon eklemesi için terminalde `~/.pixel-office/connector/workflows.json` dosyasını elle düzenlemesi gerekiyor.
  * `pixel_connector/tasks.py` satır 281'de devir (`handoff`) yalnızca tek sekme (**A → B**) ile sınırlı (`if origin is not None: handoff = None`).
* **Geliştirme Önerileri:**
  1. **Web Arayüzünden Görsel Şablon Oluşturucu:** `workflows.js` çekmecesine **"+ Yeni Şablon Oluştur"** formu eklenerek kullanıcının hiç JSON dosyasına dokunmadan tarayıcıdan adım adı, ajan/rol, profil ve istem şablonu (`{{hedef}}`, `{{onceki_cikti}}`) tanımlayıp cihazına kaydedebilmesi (`workflow.save` / `workflow.delete`).
  2. **Zengin Yerleşik Şablon Paketi:** En az 8 hazır şablonla gelmeli (Yazılım + İçerik + Araştırma + Eğitim şablonları).
  3. **Çok Adımlı Devir Zinciri (A → B → C):** `tasks.py` içinde `handoff_depth` sayacı (`MAX_HANDOFF_DEPTH = 4`) kullanılarak A'dan B'ye, B'den C'ye zincirleme devir imkânı.

### 1.5. Görev Kutusu (`prompt.js`), Bildirimler ve Çıktı Dışa Aktarma
1. **Tarayıcı Bildirimleri (`Notification API`) + Sekme Başlığı Uyarısı:** Sekme arka plandayken bir ajan izin istediğinde (`approval.request`) veya uzun bir görev/Patron işi bittiğinde masaüstü/mobil bildirim gönderilmeli ve sekme başlığı (`document.title`) `"🔔 (1 Onay) Pixel Office"` olarak güncellenmeli.
2. **Hızlı Komut Çipleri (Quick Prompts):** Görev kutusu açıldığında boş metin alanının altında ajanın rolüne göre 3-4 adet tek tıklamalık hazır komut önerisi gösterilmeli.
3. **Yanıtı Kopyala & Markdown İndir:** Görev kutusundaki son yanıtın ve Patron raporunun sağ üst köşesine **"📋 Kopyala"** ve **"⬇️ .md İndir"** butonları eklenmeli.

### 1.6. Cihaz Eşleştirme (`devices.js`) — QR Kod ile 3 Saniyede Mobil Eşleşme
* **Mevcut Sürtünme:** Telefondan eşleşmek için oda linkini telefona göndermek + 12 haneli PAKE kodunu (`K7Q2-M4X5-Z6A9`) telefonda elle yazmak gerekiyor.
* **Çözüm:**
  * `pixel-connector pair` komutu terminalde Unicode blok karakterleriyle (`▀▄█`) bir **QR Kod** basmalı (veya eşleşmiş masaüstü tarayıcısında QR kod açılmalı).
  * Telefon kamerasıyla QR kodu okutan kullanıcı tek tıkla odaya girip PAKE kodunu otomatik doldurarak 3 saniyede eşleşmeli (güvenlikten ödün vermemek için terminaldeki 6 haneli SAS onayı `E/h` aynen korunur).

---

## 3. BÖLÜM 2: "Sadece Claude Kullanan Yazılımcılar"dan "A'dan Z'ye Her İnsana ve Her Tür Yapay Zekâya Hitap Eden Evrensel Platform"a Dönüşüm ($0 Maliyet Mimarisi)

Bu bölüm, Pixel Office'i sadece yazılımcıların kullandığı bir yan araç olmaktan çıkarıp, **dünyadaki her insanın ve her tür yapay zekânın** kullanabileceği küresel bir platforma dönüştürme stratejisidir.

---

### 2.1. Sütun 1: "Sıfır Kurulum" Tarayıcı-İçi Ajan Motoru (`Browser-Native Office` — Sunucu Maliyeti: $0)

#### Problem
Dünyadaki insanların %99'u terminal kullanmaz ve bilgisayarına Python / CLI kurmaz. Ancak ajanları kendi sunucumuzda çalıştırırsak sunucu ve LLM API faturası çıkar.

#### Çözüm: `web/js/browser_device.js` (Tarayıcıda Çalışan Sanal Cihaz — %100 Ücretsiz!)
Kullanıcının **kendi web tarayıcısını (bilgisayar veya telefon)** kurulum gerektirmeyen yerel bir `Device` gibi çalıştıran **"Kurulumsuz Hızlı Ofis" (Zero-Install Cloud/Browser Mode)** mimarisi:
1. **Kullanıcı Deneyimi (15 Saniyede İlk Görev):**
   * Kullanıcı `pixeloffice.com.tr`'ye girer, hiçbir program indirmez.
   * **"⚡ Kurulumsuz Hemen Başla (Tarayıcı Modu)"** butonuna tıklar.
   * Yapay zekâ bağlantısını seçer:
     * **Ücretsiz API Anahtarları (30 Saniyede Alınan Bedava Modeller):**
       * **Google Gemini API (Google AI Studio Free Tier):** Günde 1.500 isteğe kadar **tamamen ücretsiz** `gemini-2.5-flash` desteği! (Arayüzde *"Ücretsiz Gemini Anahtarını 10 Saniyede Al ↗"* rehber linkiyle).
       * **Groq Cloud (Free Tier):** Ücretsiz ve ultra hızlı `llama-3.3-70b-versatile`.
       * **OpenRouter Free Models:** `:free` uzantılı ücretsiz modeller (`deepseek-r1:free`, `qwen:free`, `gemma:free`).
     * **Kullanıcının Kendi API Anahtarı (BYOK):** OpenAI (`gpt-4o`, `o3-mini`), Anthropic (`claude-sonnet`), DeepSeek, Mistral, xAI.
     * **Bilgisayardaki Yerel Ollama / LM Studio:** Tarayıcıdan `http://127.0.0.1:11434` adresine doğrudan bağlantı.
2. **Neden Sunucu Maliyeti Tamamen $0?**
   * Kullanıcının girdiği API anahtarı **asla Pixel Office sunucularına gitmez**; yalnızca kullanıcının kendi tarayıcısındaki `IndexedDB` (`idb.js`) içinde saklanır.
   * Tüm LLM istekleri (`fetch` SSE stream) **doğrudan kullanıcının tarayıcısından** ilgili AI sağlayıcısına (Gemini / OpenAI / Groq / OpenRouter) yapılır.
   * Tarayıcıdaki bu ajanlar da aynı `CharacterManager`, `BossController`, `WorkflowDrawer` ve `Board` arayüzlerini kullanır: masalarına yürür, düşünür, yazar, birbirine kağıt taşır, Patron'dan görev alır ve sanal çalışma klasörüne (`IndexedDB` içindeki sanal dosya sistemine) belgeler/web sayfaları/raporlar üretir!
   * **Vercel, Upstash ve Cloudflare üzerinde 0 bayt LLM trafiği ve $0 maliyet!**

---

### 2.2. Sütun 2: Her Tür Yapay Zekâ İçin Evrensel Motor ve Araç (Tool-Calling) Katmanı

Platformu "sadece Claude Code" bağımlılığından kurtarmak için hem masaüstü bağlayıcısında (`pixel_connector`) hem de tarayıcı modunda **Evrensel Motor Mimarisi**:

1. **`OpenAIEngine` ve `OllamaEngine` İçin Yerleşik Araçlar (Tool Calling):**
   * Şu an `pixel_connector/engines/ollama.py` ve `openai_compat.py` yalnızca düz sohbet yapıyor (`Yerel model: sohbet eder, dosyalara dokunmaz`).
   * Bu motorlara standart OpenAI `tools` (Function Calling) şemasıyla çalışan ve mevcut `workspaces.py` + `profiles` güvenlik sınırlarına %100 uyan **yerleşik hafif araçlar** eklenmeli:
     * `read_file` / `list_dir` (`readonly`, `edit`, `dev` profillerinde)
     * `write_file` / `edit_file` (`edit` ve `dev` profillerinde, sadece çalışma klasörü içinde `O_NOFOLLOW` korumalı)
     * `web_fetch` / `web_search` (araştırma görevleri için)
   * Böylece kullanıcı **ChatGPT (OpenAI), Google Gemini, DeepSeek, Groq, OpenRouter, Ollama veya LM Studio** kullandığında da ajanları gerçek dosyalar üretebilir, ofiste araç ikonları (`👁️`, `✏️`, `🌐`) gösterebilir ve **Patron Odası'nda uzman olarak çalışabilir!**
2. **Hazır Sağlayıcı Şablonları (Provider Presets):**
   * Kullanıcı `connector.json` içinde karmaşık URL ayarlamak zorunda kalmamalı; **OpenAI, Google Gemini, DeepSeek, Groq, OpenRouter, Together AI, Mistral, Ollama, LM Studio** hazır seçenek olarak gelmeli.
3. **Diğer Tüm Kodlama Ajanları İçin Kanca/Adaptör Desteği:**
   * `docs/INTEGRATIONS.md` ve "Ajan Bağla" penceresinde sadece Claude Code değil; **Gemini CLI, OpenAI Codex CLI, Cursor, Windsurf, GitHub Copilot, Cline / Roo Code, Aider** ve **Antigravity IDE** tek tıkla seçilebilir sekmeler olarak sunulmalı.

---

### 2.3. Sütun 3: A'dan Z'ye Her İnsana Hitap Eden "Evrensel Ofis Departmanları ve Meslek Paketleri"

Yazılımcı olmayan bir insanın Pixel Office'i her gün kullanması için, ofisin dilini ve rollerini kullanıcının mesleğine/ihtiyacına göre şekillendiren **Departman Seçici (Office Departments)** sistemi:

Kullanıcı ofisini açtığında veya Patron Odası'na girdiğinde **5 Departmandan** birini (veya birkaçını) seçebilir:

#### 1. 🎬 İçerik, Medya & Sosyal Medya Ajansı (YouTuber, Blogger, Yazar, Ajanslar İçin)
* **Patronun Uzman Kadrosu:**
  * `researcher` (**Trend & Konu Araştırmacısı** — `🌐`): Konuyu, rakip içerikleri ve güncel verileri araştırır.
  * `writer` (**Senarist & Metin Yazarı** — `✏️`): Video senaryosu, blog yazısı, bülten veya hikâye yazar.
  * `seo` (**SEO & Viral Başlık Uzmanı** — `🔍`): Tıklanma oranı yüksek başlıklar, anahtar kelimeler ve etiketler üretir.
  * `social` (**Sosyal Medya Editörü** — `📋`): İçeriği X (Twitter) flood'una, LinkedIn postuna ve Instagram Reels metnine dönüştürür.
  * `editor` (**Baş Editör** — `👁️`): Dilbilgisi, akıcılık ve ton kontrolü yapıp son raporu birleştirir.

#### 2. 🎓 Akademik, Eğitim & Sınav Çalışma Odası (Öğrenciler, Öğretmenler, Akademisyenler İçin)
* **Patronun Uzman Kadrosu:**
  * `tutor` (**Konu Anlatıcı Hoca** — `✏️`): Karmaşık konuları basitleştirerek, analojilerle ve adım adım anlatır.
  * `summarizer` (**Makale & Not Özetleyici** — `👁️`): Uzun metinleri, ders notlarını veya makaleleri maddeler halinde özetler.
  * `quiz` (**Sınav & Soru Bankası Hazırlayıcı** — `📋`): Konuyla ilgili çoktan seçmeli testler, boşluk doldurma ve çalışma kartları (flashcards) üretir.
  * `coach` (**Çalışma Programı Koçu** — `🔍`): Haftalık ders çalışma takvimi ve tekrar planı çıkarır.

#### 3. 🚀 İş, Girişim, Pazarlama & E-Ticaret Ofisi (Girişimciler, Esnaflar, Pazarlamacılar, Freelancer'lar İçin)
* **Patronun Uzman Kadrosu:**
  * `market` (**Pazar & Rakip Analisti** — `🌐`): Sektör analizi, SWOT tablosu ve hedef kitle profili çıkarır.
  * `copywriter` (**Satış & Reklam Yazarı** — `✏️`): Ürün açıklamaları, reklam metinleri, soğuk e-postalar ve teklif yazıları hazırlar.
  * `strategist` (**İş Geliştirme Stratejisti** — `📋`): 30-60-90 günlük büyüme planı ve sunum (pitch deck) taslağı oluşturur.
  * `finance` (**Bütçe & Fiyatlandırma Uzmanı** — `⚙️`): Maliyet, kâr marjı ve paket fiyatlandırma tabloları hazırlar.

#### 4. 🏡 Kişisel Yaşam, Seyahat & Verimlilik Ofisi (A'dan Z'ye Günlük Kullanıcılar İçin)
* **Patronun Uzman Kadrosu:**
  * `travel` (**Seyahat & Rota Planlayıcı** — `🌐`): Saat saat gezi rotası, bütçe ve valiz listesi hazırlar.
  * `wellness` (**Beslenme & Antrenman Koçu** — `📋`): Haftalık yemek menüsü, market alışveriş listesi ve egzersiz programı çıkarır.
  * `organizer` (**Günlük & Haftalık Planlayıcı** — `✏️`): Dağınık işleri önceliklendirip zaman bloklarına böler.

#### 5. 💻 Yazılım & Mühendislik Stüdyosu (Mevcut — Yazılımcılar İçin)
* **Patronun Uzman Kadrosu:** `frontend`, `backend`, `test`, `docs`, `review`.

---

### 2.4. Sütun 4: "Canlı Eser Vitrini" (Artifact & Document Preview)

Yazılımcı olmayan kullanıcılar için bir ajanın ürettiği çıktı "terminal logu" değil, **dokunulabilir bir sonuç (eser)** olmalıdır:
1. **Zengin Belge Görünümü (`web/js/preview.js`):**
   * Görev kutusunda ve Patron Raporunda üretilen çıktılar; temiz tipografi, tablolar, başlıklar ve görev listeleriyle bir belge gibi sunulmalı (`textContent` / güvenli DOM oluşturma ile XSS riski sıfır tutularak).
   * Tek tıkla **"📄 Yazdır / PDF Kaydet"**, **"⬇️ Markdown (.md) İndir"**, **"⬇️ HTML İndir"** ve **"📋 Panoya Kopyala"** aksiyonları.
2. **Canlı Web / Sunum Önizlemesi (Sandboxed Iframe):**
   * Ajan bir web sayfası, interaktif tablo, sunum veya mini araç ürettiğinde, kullanıcı **"👁️ Canlı Önizle"** butonuna tıklayarak güvenli bir `<iframe sandbox="allow-scripts">` içinde eseri anında çalışırken görebilmeli.

---

### 2.5. Sütun 5: Küresel Dil (`i18n`), Pomodoro/Lo-Fi Ambiyans ve Viral Büyüme Motoru

1. **Çoklu Dil Desteği (`TR` / `EN` — `web/js/i18n.js`):**
   * Tüm dünyaya açılmak için arayüzde Türkçe ve İngilizce dil desteği (tarayıcı diline göre otomatik seçilen, üst bardan tek tıkla değiştirilebilen sıfır bağımlılıklı hafif sözlük yapısı).
2. **"Work / Study With My AI Team" (Pomodoro & Ambiyans Ses Motoru):**
   * `web/js/audio.js` içine Web Audio API (pembe/kahverengi gürültü filtresi + osilatör) ile **0 KB boyutunda** hafif **Yağmur Sesi 🌧️ / Kafe Uğultusu ☕ / Gece Ambiyansı** ve üst bara **🍅 Pomodoro Sayacı (25 dk Odak / 5 dk Mola)** eklenmesi.
   * Kullanıcı 25 dakika boyunca kendi işine/dersine odaklanırken ofisteki ajanlar da çalışır; 5 dakikalık mola zili çaldığında ofisteki karakterler de kahve makinesine yürür! Bu özellik, kullanıcıların aktif görev vermedikleri saatlerde bile siteyi tam ekran açık tutmasını sağlar.
3. **Tek Tıkla "Ofis Kartımı Paylaş" (`Share Office Card`):**
   * Canvas sahnesini + ofis adını + aktif ajan kadrosunu + tamamlanan görev sayısını şık bir piksel çerçeveli PNG görsel olarak (`canvas.toBlob()`) tek tıkla indiren **"📸 Ofisimi Paylaş"** butonu. Sağ alt köşedeki `pixeloffice.com.tr` imzası ile X (Twitter), Reddit, LinkedIn ve Product Hunt üzerinde organik viral yayılım sağlar.

---

## 4. BÖLÜM 3: Claude ve Antigravity İçin Önerilen Görev Bölümü (Görev Panosu Taslağı)

`AGENTS.md` ve `docs/GOREV_PANOSU.md` içindeki **Tek Yazıcı Kuralı (Single-Writer Rule)** ve uzmanlık ayrımına göre, Claude ile Antigravity arasında sıfır dosya çakışmasıyla yürütülebilecek önerilen iş bölümü:

| Faz / Paket | Görev Kodu & Başlığı | Önerilen Sorumlu | Dokunulacak Dosyalar (Kapsam Sınırı) | Bağımlılık |
| :--- | :--- | :--- | :--- | :--- |
| **Faz 1: Mevcut Özellikleri & Sahneyi Mükemmelleştirme** | **G1 — Sahne Kontrolleri, Atmosfer Seçici & Ofis Kedisi** (Zoom `+/-`, Gece/Gündüz/Gün Batımı butonu, tıklanabilir Ofis Kedisi ve Kahve Makinesi, Patron hover etiketi) | **Antigravity** | `web/js/renderer.js`, `web/js/fx.js`, `web/js/sprites.js`, `web/js/boss_scene.js`, `web/js/office.js`, `tests/js/*` | Bağımsız |
| **Faz 1: Mevcut Özellikleri & Sahneyi Mükemmelleştirme** | **G2 — İlk Karşılama & Zengin Canlı Demo Ofisi** (İlk ziyarette etkileşimli Demo Ofisi, demoda Patron Odası / Pano / Şablon simülasyonu, `tanitim.html` geçişi) | **Claude** veya **Antigravity** | `web/js/main.js`, `web/index.html`, `web/tanitim.html` | Bağımsız |
| **Faz 1: Mevcut Özellikleri & Sahneyi Mükemmelleştirme** | **G3 — Görev Kutusu, Bildirimler & Çıktı Araçları** (Web Notification API, sekme başlığı uyarısı, `.md` indir / kopyala, hazır komut çipleri) | **Antigravity** | `web/js/prompt.js`, `web/js/approvals.js`, `web/js/boss.js`, `web/css/style.css` | Bağımsız |
| **Faz 1: Mevcut Özellikleri & Sahneyi Mükemmelleştirme** | **G4 — QR Kod ile Mobil Eşleştirme & Adım Tekrarlama** (`pixel-connector pair` terminal/web QR kodu, `boss.retry_step`, много-adımlı `handoff`) | **Claude** | `pixel_connector/__main__.py`, `pixel_connector/boss.py`, `pixel_connector/tasks.py`, `web/js/devices.js` | Bağımsız |
| **Faz 2: Evrensel Departmanlar & Şablonlar** | **E1 — Evrensel Ofis Departmanları & Meslek Paketleri** (Patron Odası ve Şablonlarda *İçerik Ajansı, Akademik Oda, Girişim/Pazarlama, Kişisel Yaşam, Yazılım* departmanları ve rolleri) | **Claude** (Backend protokol) + **Antigravity** (Frontend & Şablon içerikleri) | `pixel_connector/boss.py`, `pixel_connector/workflows.py`, `web/js/boss.js`, `web/js/workflows.js` | G4 sonrası |
| **Faz 2: Evrensel Departmanlar & Şablonlar** | **E2 — Görsel İş Akışı Şablon Oluşturucu & Canlı Eser Vitrini** (Arayüzden kodsuz şablon ekleme/silme, zengin belge/HTML önizleme çekmecesi) | **Antigravity** | `web/js/workflows.js`, `web/js/preview.js` *(yeni)*, `pixel_connector/workflows.py` | E1 ile paralel |
| **Faz 3: Evrensel AI & Sıfır Kurulum Tarayıcı Modu ($0 Maliyet)** | **E3 — Tüm AI Motorlarına Araç (Tool-Calling) ve Patron Yeteneği** (`OpenAIEngine` ve `OllamaEngine` için güvenli çalışma klasörü dosya okuma/yazma + web araştırma + Patron Odası desteği, Gemini/Groq/OpenRouter hazır ayarları) | **Claude** (Güvenlik sınırı kritik olduğu için) | `pixel_connector/engines/openai_compat.py`, `pixel_connector/engines/ollama.py`, `pixel_connector/boss.py`, `pixel_connector/config.py` | E1 sonrası |
| **Faz 3: Evrensel AI & Sıfır Kurulum Tarayıcı Modu ($0 Maliyet)** | **E4 — "Sıfır Kurulum" Tarayıcı-İçi Ajan Motoru (`Browser-Native Office`)** (Terminal kurmadan tarayıcıda ücretsiz Gemini / Groq / OpenRouter / BYOK anahtarıyla çalışan sanal cihaz ve ajanlar — $0 sunucu maliyeti) | **Antigravity** (Modül) + **Claude** (CSP / Güvenlik incelemesi) | `web/js/browser_device.js` *(yeni)*, `web/js/control.js`, `vercel.json` | E3 ile paralel |
| **Faz 4: Küresel Lansman & Viral Büyüme** | **E5 — Küresel Çoklu Dil (`TR` / `EN`), Pomodoro Ambiyansı & "Ofisimi Paylaş" Kartı** (`i18n.js`, Pomodoro + yağmur/kafe sesi, tek tıkla sosyal medya PNG ofis kartı) | **Antigravity** | `web/js/i18n.js` *(yeni)*, `web/js/audio.js`, `web/js/share_card.js` *(yeni)*, `web/index.html`, `web/tanitim.html` | Bağımsız |

