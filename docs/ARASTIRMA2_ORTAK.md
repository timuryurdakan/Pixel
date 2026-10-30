# Pixel Office — Ortak Evrensel Platform ve Açık Kaynak Entegrasyon Yol Haritası (`ARASTIRMA2_ORTAK.md`)

* **Hazırlayanlar:** Claude (`docs/ARASTIRMA2_CLAUDE.md`) & Antigravity (`docs/ARASTIRMA2_ANTIGRAVITY.md`) — Karşılıklı İnceleme ve Birleştirilmiş Tek Rapor. Taslak Antigravity'nin; Claude'un incelemesi, düzeltmeleri ve kullanıcı kararı gereken noktalar §5–§6'da. **Çelişki olursa §5–§6 geçerlidir.**
* **Tarih:** 2026-10-30
* **Mevcut Sürüm Tabanı:** Bağlayıcı `3.11.0` · Web `po-v4.0.14` (Patron Odası P1–P9 ve Faz 1–3 tamamlanmış durum)
* **Temel İlkeler:**
  1. **Sunucu maliyeti $0 kalır** (Vercel Hobby, Upstash Free, Cloudflare Workers Free + Durable Objects SQLite/Hibernation).
  2. **Güvenlik ve Mahremiyetten Ödün Verilmez:** `docs/FAZ3_TASARIM.md` (D1–D7) ve `docs/SECURITY_AUDIT.md` kuralları bağlayıcıdır; yeni bağımlılıklar yalnızca lisans (MIT / Apache-2.0 / BSD / CC0), boyut ve güvenlik incelemesinden geçerek kullanıcı onayıyla eklenir.

---

## 1. İki Raporun Sentezi: Neden İki Perspektif Birbirini Tamamladı?

Claude ve Antigravity bağımsız araştırmalarında problemin iki farklı ama birbirini %100 tamamlayan yüzünü ortaya çıkarmıştır:

1. **Claude'un Odak Noktası ("Her Geliştirici Aracını ve Ajanını Standartlarla Bağla + Dağıtım Kanalları"):**
   * Rakip analizi (`Pixel Agents` 40 bin indirme, `pixtuoid`, `AIOffice`, `AI Town`) ile Pixel Office'in asıl rekabet üstünlüğünün **"paylaşılan bulut oda + telefondan E2E uzaktan kumanda + Patron Odası"** olduğunu kanıtladı.
   * **Kanca (Hooks) Adaptörleri:** Antigravity CLI/IDE, OpenAI Codex CLI ve Cursor kancalarıyla tüm popüler kodlama araçlarının ofiste canlı (ve onay durumu `!` ile) görünmesini tasarladı.
   * **Zed ACP (Agent Client Protocol — Apache-2.0):** Bağlayıcıyı bir ACP istemcisi yaparak tek adaptörle 25+ ajanı uzaktan yönetilebilir kılma yolunu açtı.
   * **Dağıtım Kanalları:** VS Code / Open VSX eklentisi, Antigravity Yan Panel (`sidecar`) eklentisi, PWABuilder mağaza paketleri, MCP Apps (`ui://`) ve Discord Activity.
2. **Antigravity'nin Odak Noktası ("Terminali Olmayan Her İnsana, Her Mesleğe ve Her Modele Tam Ofis Deneyimi Ver"):**
   * Mevcut `po-v4.0.14` kod tabanındaki 7 somut boşluğu tespit etti:
     1. Kurulumsuz `BrowserDevice` şu an **Patron Odası'nı (`boss.*`), İş Akışı Şablonlarını (`workflow.*`) ve masadan masaya devri (`handoff`)** desteklemiyor.
     2. Bağlayıcıdaki Patron Odası (`pixel_connector/boss.py:78`) hâlâ yalnızca `claude` motoruna kilitli; ücretsiz Gemini/Groq veya yerel Ollama ile çalışmıyor.
     3. `BrowserDevice` yalnızca bulut odası + röle varken açılabiliyor ve ilk ziyaretçi hâlâ `/rehber.html?ilk=1` terminal rehberine yönlendiriliyor.
     4. Kullanıcının bilgisayarına bir şey kurmadan kendi klasörünü (`showDirectoryPicker`) veya `.pdf` / `.docx` / `.csv` / `.zip` belgelerini sürükleyip bırakarak ajanlara okutma yolu yok.
     5. `PreviewDrawer` (`preview.js`) yalnızca statik metin/HTML gösteriyor; diyagram, slayt sunumu, grafik ve opak kum havuzlu mini uygulama çalıştırma yeteneği yok.

---

## 2. Karşılıklı İncelemede Doğrulanan ve Çözülen Açık Sorular

Claude'un raporunda (§5) işaretlenen açık sorular, Antigravity'nin yerleşik sistem belgeleri ve kod analiziyle kesin olarak yanıtlanmıştır:

| # | Açık Soru / Tartışma Noktası | Doğrulanan Teknik Gerçek ve Ortak Karar |
| :--- | :--- | :--- |
| **1** | **Antigravity `hooks.json` IDE, Antigravity 2.0 ve CLI'da aynı mı çalışıyor?** | **%100 Doğrulandı (Evet):** Yerleşik `agy-customizations/docs/hooks.md` resmi spesifikasyonuna göre `~/.gemini/config/hooks.json` (ve `.agents/hooks.json`) hem **CLI (`antigravity-cli/`)**, hem **Antigravity 2.0 (`antigravity/`)**, hem de **IDE (`antigravity-ide/`)** üzerinde birebir aynı çalışır. `SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse`, `PostInvocation`, `Stop` olaylarını destekler. Üstelik `PreToolUse` `stdout`'tan `{"decision": "allow" \| "deny" \| "ask" \| "force_ask"}` beklediği için **Aşama 19 uzaktan telefon onayları (`--decide`) Antigravity'de de birebir çalışır!** |
| **2** | **Antigravity içinde VS Code eklentisi gibi yan panel açılabilir mi?** | **%100 Doğrulandı (Evet):** Antigravity'nin yerleşik `ui-plugin-navigation` / `sidecar.json` sistemi (`SIDECAR_UI_ENTRYPOINT_AUX_PANE`), `~/.gemini/config/plugins/pixel-office/` altına konacak tek bir eklenti paketiyle hem `hooks.json`'ı otomatik yükler hem de Pixel Office'i Antigravity'nin sağ yan panelinde (`sidecar://pixel-office/office/`) açar! |
| **3** | **Anahtarsız tarayıcı modeli için Chrome Prompt API (`LanguageModel`) mi yoksa WebLLM / `wllama` mı?** | **Ortak Karar (Kademeli Yaklaşım):**<br>1. İlk aşamada sıfır bağımlılık gerektiren **Chrome yerleşik Prompt API (`window.LanguageModel` — Gemini Nano)** özellik algılamayla (`if ('LanguageModel' in self)`) eklenir.<br>2. Ancak Brave (kullanıcımızın tarayıcısı), Firefox, Safari ve mobilde `LanguageModel` olmadığı ve araç çağırma (`tools`) desteklemediği için, ikinci aşamada isteğe bağlı dinamik modül olarak **`wllama` (MIT, ~350 KB Wasm)** veya **`WebLLM` (Apache-2.0)** seçeneği değerlendirilir.<br>3. **`Puter.js` kesin olarak reddedildi** (üçüncü parti hesap/CDN ve CSP ihlali). |
| **4** | **Tarayıcıda Python (`Pyodide`) vs. hafif alternatifler** | **Ortak Karar:** `Pyodide` (MPL-2.0, büyük indirme boyutu) çekirdeğe eklenmez. Bunun yerine veri/belge işleme için sıfır bağımlılıklı veya çok küçük MIT kütüphaneler (`fflate` ~8 KB ZIP/DOCX, `MiniSearch` ~7 KB yerel arama) ve kum havuzlu `PreviewDrawer` tercih edilir. |
| **5** | **Piksel sanatı ve tema lisansları (`Kenney` vs. `LPC`)** | **Ortak Karar:** `LPC` varlıkları (CC-BY-SA / GPL lisans bulaşıcılığı nedeniyle) **kesinlikle kullanılmaz**. Yalnızca kendi prosedürel çizimlerimiz (`sprites.js`) ve **Kenney CC0** (kamu malı) varlıkları kullanılır. |
| **6** | **Tarayıcı Ajanı (E4) CORS Doğrulaması** | **Adım 0:** Yeni tarayıcı özelliklerine geçmeden önce 6 sağlayıcının (`gemini`, `groq`, `openrouter`, `openai`, `deepseek`, `mistral`) tarayıcıdan doğrudan `fetch` CORS davranışı gerçek anahtarla doğrulanır; CORS izni vermeyen sağlayıcı varsa listeden çıkarılır. |

---

## 3. Açık Kaynak Entegrasyon ve Geliştirme Paketleri (Tamamlayıcı Mimari)

### 3.1. Evrensel Ajan Bağlantısı: Kancalar (Hooks) + ACP + MCP (Claude & Antigravity)
1. **Üç Yeni Resmi Kanca Adaptörü (`web/hook/pixel_hook.py` genişletmesi — Stdlib):**
   * **Antigravity (`~/.gemini/config/hooks.json`):** `SessionStart`, `PreInvocation` (`thinking`), `PreToolUse` (`working` + araç adı + uzaktan onay `--decide`), `PostToolUse`, `Stop` (`idle`). Mevcut `pixel_antigravity.py` JSONL izleyicisi yedek olarak korunur.
   * **OpenAI Codex CLI (`~/.codex/hooks.json`):** `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest` (`waiting_for_user` `!`), `PostToolUse`, `Stop`.
   * **Cursor (`~/.cursor/hooks.json`):** `sessionStart`, `beforeSubmitPrompt`, `preToolUse`, `beforeShellExecution`, `afterFileEdit`, `stop`.
   * *Not:* Google bireysel Gemini CLI kullanımını Antigravity CLI'ya taşıdığı için Gemini CLI'ya ayrı yatırım yapılmaz.
2. **Zed ACP (Agent Client Protocol — Apache-2.0) İstemcisi (`pixel_connector/engines/acp.py`):**
   * Bağlayıcı, ACP konuşan herhangi bir ajanı (Codex, OpenCode, Copilot, Cursor, Claude Code) alt süreç olarak başlatıp `stdio` üzerinden JSON-RPC 2.0 konuşur.
   * İzin eşlemesi: Ajanın kendi izin modeli bizim `readonly`/`edit`/`dev` profillerimize tam çevrilemiyorsa **güvenli başarısızlık (fail-closed)** ilkesiyle varsayılan `readonly` ve her araç çağrısında kullanıcı onayı (`ApprovalCenter`) uygulanır.
3. **Model Context Protocol (`MCP` — MIT) Köprüsü ve `MCP Apps` (`SEP-1865`):**
   * **İstemci yönü (`pixel_connector/mcp.py`):** Stdlib JSON-RPC ile yerel MCP sunucularını (Notion, Google Drive, GitHub, SQLite, Obsidian) bağlayıcıdaki motorlara araç olarak sunar.
   * **Sunucu/Vitrin yönü (`MCP Apps`):** Claude Desktop veya destekleyen istemcilerin içinde Pixel Office'i `ui://` kum havuzlu iframe olarak canlı gösterir.

### 3.2. Kurulumsuz Kullanım: Tarayıcıda Patron Odası (`BrowserBoss`), Şablonlar ve Anahtarsız Model
1. **Tarayıcı Cihazında Patron Odası ve İş Akışları (`web/js/browser_boss.js`, `web/js/browser_workflows.js`):**
   * `BrowserDevice` (`web/js/browser_device.js`) içine `boss.*`, `workflow.*` ve `handoff` mesaj desteği eklenir.
   * Böylece hiçbir şey kurmayan bir kullanıcı (telefonundan veya bilgisayarından) 5 departmanlı (**Yazılım, İçerik ve Medya, Akademik, Girişim ve Pazarlama, Kişisel Yaşam**) Patron Odası'nı ve hazır şablonları doğrudan tarayıcıda çalıştırabilir!
2. **Bağlayıcıda Çok Motorlu Patron Odası (`pixel_connector/boss.py`):**
   * E3 ile araç (`list_dir`, `read_file`, `write_file`) kazanan `openai` (Gemini, Groq, OpenRouter, OpenAI, DeepSeek, Mistral) ve `ollama` motorları, D7 jeton ve tur tavanlarıyla Patron Odası uzmanlarında da kullanılabilir hâle getirilir (ayrı tasarım onayıyla).
3. **Anahtarsız İlk Deneyim ve İlk Ziyaretçi Akışı:**
   * İlk ziyarette doğrudan `/rehber.html`'e zorunlu yönlendirme yerine, kullanıcının tek tıkla **"Anahtarsız Hemen Dene"** (destekleyen tarayıcıda Chrome `LanguageModel` / Gemini Nano veya WebGPU/Wasm mini model) ya da **"Kendi Anahtarınla Kurulumsuz Çalıştır"** moduna geçmesi sağlanır.
   * Sağlayıcıların ücretsiz kota rakamları (sürekli değiştiği için) arayüze sabit yazılmaz; doğrudan sağlayıcının kendi anahtar/kota sayfasına bağlantı verilir.

### 3.3. Dosya, Belge ve Eser Deneyimi (Yazılımcı Olmayan Kullanıcılar İçin)
1. **Gerçek Klasör Seçici (`window.showDirectoryPicker`) ve `.ZIP` / Sürükle-Bırak Desteği:**
   * Chromium tarayıcılarda (**Brave hariç**, bkz. §5.2) kullanıcı kurulum yapmadan **"Bilgisayarımdan Klasör Seç"** diyerek `BrowserDevice` ajanlarını yerel bir klasöre (`splitPath` ve `deniedName` korumalarıyla) bağlayabilir.
   * Sanal klasördeki tüm çıktılar tek tıkla `.zip` olarak indirilebilir; `.md` / `.txt` / `.csv` / `.pdf` belgeleri sürükle-bırak ile sanal klasöre eklenebilir ve yerel arama (`search_files`) aracıyla taranabilir.
   * Bağlayıcıya `workflow.save` ve `workflow.delete` eklenerek E2 şablon oluşturucunun indirme yapmadan doğrudan cihaza kaydetmesi sağlanır.
2. **Zenginleştirilmiş Eser Önizleme Çekmecesi (`web/js/preview.js`):**
   * Mevcut `sandbox=""` + `srcdoc` mimarisi korunarak Markdown ön-işlemcisine **Slayt Sunumu Görünümü** (`---` ayraçlarıyla ileri/geri slayt geçişi), **SVG Akış/Zihin Diyagramı** ve **SVG Veri Grafiği** (` ```chart `) desteği eklenir.

### 3.4. Özelleştirme, Gerçek Zamanlı Oda, Çoklu Dil ve Dağıtım Kanalları
1. **Tam Çoklu Dil (`web/js/i18n.js` — `TR` / `EN`) ve Erişilebilirlik:**
   * Sıfır bağımlılıklı sözlük + `Intl` API ile tüm arayüz, departmanlar ve şablonlar İngilizce ve Türkçe destekler.
   * Ekran okuyucu (`aria-live`) duyuruları ve renk körlüğü için şekil/simge ayrımları tamamlanır.
2. **Masa ↔ Klasör Eşleme, Karakter Özelleştirme ve Ofis Düzenleyici:**
   * Belirli bir projenin/klasörün ajanının hep seçilen masaya oturması.
   * Karakter saç, kıyafet rengi ve piksel aksesuar (gözlük, kulaklık, şapka) seçimi.
   * Oda başına kaydedilen **Ofis Düzenleyici** (izin listeli karo/eşya kimlikleriyle) ve **Kenney CC0** uyumlu hazır temalar (*Klasik Ofis, Kütüphane/Akademi, Kafe, Uzay İstasyonu*).
   * **Pomodoro Sayacı & Web Audio Ambiyans (Yağmur/Kafe)** ve tek tıkla **"📸 Ofis Kartımı Paylaş"** PNG çıktısı.
3. **Dağıtım Kanalları:**
   * **Antigravity Plugin + Sidecar Paketi (`plugin.json` + `hooks.json` + `sidecar.json`):** Antigravity IDE/2.0 içinde yan panelde çalışan resmi eklenti.
   * **VS Code / Open VSX Eklentisi:** Odayı VS Code yan panelinde (Webview) açan ve kancaları tek tıkla kuran eklenti.
4. **Durable Objects ile Anlık Oda Durumu:**
   * 5 saniyelik Upstash yoklamasını (polling), mevcut Cloudflare Worker (`relay/`) üzerinde SQLite + WebSocket Hibernation kullanan Durable Object ile anlık hale getirme (kota aşımında otomatik polling yedeğiyle).

---

## 4. Birleştirilmiş Öncelik Sırası ve Görev Dağılımı (Claude + Antigravity)

| Sıra | Görev Kodu & Başlığı | Büyüklük | Sorumlu (Öneri) | Neden Bu Sırada ve Ne Kazandırır? |
| :--- | :--- | :--- | :--- | :--- |
| **0** | **F4-0: Tarayıcı Ajanı (E4) Gerçek CORS Doğrulaması** | S | **Kullanıcı + Claude** | 6 sağlayıcının tarayıcıdan doğrudan `fetch` CORS izni gerçek anahtarla doğrulanır; çalışmayan varsa temizlenir. |
| **1** | **F4-1: Kanca Adaptörleri (`Antigravity`, `Codex CLI`, `Cursor`) + Antigravity Uzaktan Onay (`--decide`)** | M | **Claude** (Gizlilik süzgeci, kanarya testleri ve `pixel_hook.py`) | En büyük geliştirici kitlesini sıfır riskle bağlar; Antigravity'deki "onay anı görünmüyor" sorununu `PreToolUse` kancasıyla kökten çözer. |
| **2** | **F4-2: Küresel Dil Desteği (`TR` / `EN` — `web/js/i18n.js`) ve Erişilebilirlik** | M | **Antigravity** | Dünya kitlesine açılmanın önündeki en büyük engel olan "yalnızca Türkçe" sınırını kaldırır (sıfır bağımlılık). |
| **3** | **F4-3: Tarayıcıda Tam Patron Odası (`BrowserBoss`), Şablonlar (`BrowserWorkflows`) ve Bağlayıcıda `workflow.save`/`delete`** | M–L | **Antigravity** (`browser_boss.js`, `browser_workflows.js`) + **Claude** (`workflows.py` `save/delete` & inceleme) | Kurulum yapmayan tarayıcı kullanıcılarına 5 departmanlı Patron Odası'nı, şablonları ve masadan masaya devri açar; E2 şablonlarını doğrudan kaydeder. |
| **4** | **F4-4: Anahtarsız İlk Deneyim (Chrome `LanguageModel` Gemini Nano) + Sanal Klasör `.ZIP` / Gerçek Klasör Seçici (`showDirectoryPicker`)** | M | **Antigravity** (Modül) + **Claude** (Güvenlik incelemesi) | "Kayıt yok, anahtar yok" ilk deneyimi ve kurulumsuz yerel klasör / toplu `.zip` indirme yeteneği sağlar. |
| **5** | **F4-5: Eser Stüdyosu 2.0 (`PreviewDrawer` içinde Slayt Sunumu, SVG Diyagram/Grafik) + Karakter Özelleştirme & Masa ↔ Klasör Eşleme** | M | **Antigravity** | Yazılımcı olmayanlar için ajan çıktılarını sunuma/diyagrama dönüştürür; ofise kişilik ve masa düzeni katar. |
| **6** | **F4-6: Bağlayıcıda Çok Motorlu Patron (`OpenAI/Gemini/Groq/Ollama`) ve Zed `ACP` İstemcisi** | L | **Claude** (Tasarım belgesi + Güvenlik + Uygulama) | Claude aboneliği olmayanlara ve 25+ ACP ajanına (`Codex`, `OpenCode`, `Copilot`) tam uzaktan kumanda ve Patron Odası yeteneği verir. |
| **7** | **F4-7: VS Code / Open VSX Eklentisi + Antigravity Yan Panel (`sidecar`) Eklentisi + "Ofis Kartımı Paylaş"** | M | **Antigravity** (Eklenti arayüzleri & paylaşım kartı) + **Claude** (Kanca kurulumu) | En güçlü organik dağıtım kanallarında (Open VSX, Antigravity Marketplace, sosyal medya) yer alır. |
| **8** | **F4-8: Ofis Düzenleyici (64×64 Harita & Kenney CC0 Temaları) + Pomodoro/Ambiyans** | M–L | **Antigravity** (Arayüz & Çizim) + **Claude** (Upstash şema doğrulaması) | Kullanıcıların kendi ofislerini/kafelerini/kütüphanelerini tasarlamasını ve gün boyu açık tutmasını sağlar. |
| **9** | **F4-9: Cloudflare Durable Objects ile Anlık Oda Durumu + `MCP` / `MCP Apps`** | L | **Claude** | 5 sn yoklamayı gerçek zamanlı WebSocket akışına taşır ve MCP ekosistemini bağlar. |

---

## 5. Claude'un İncelemesi: Düzeltmeler ve Ek Kısıtlar (2026-10-30)

Antigravity'nin raporu ve bu taslak, özellikle **yazılımcı olmayan kullanıcı** boyutunda benim raporumun eksiğini
kapatıyor: tarayıcıda Patron, belge masası, zengin önizleme. Tespit ettiği boşlukları kodda doğruladım:
- `browser_device.js` gerçekten `boss.*`, `workflow.*` ya da `handoff` mesajlarını işlemiyor.
- `boss.py` `ROLE_ENGINES = ("claude",)` gerçekten öyle.

Aşağıdaki düzeltmeler uygulamaya geçmeden önce dikkate alınmalı.

### 5.1 Katıldıklarım

- Kanca adaptörleri (F4-1). Antigravity hooks doğrulaması (`PreToolUse` `decision`) için not: Kaynak, Antigravity'nin
  bilgisayarda yerleşik belgesi (`agy-customizations/docs/hooks.md`); herkese açık bir bağlantı değil. Uygulamada
  gerçek `agy` ile denenerek doğrulanır.
- İngilizce arayüz (F4-2) ve sıralamanın genel mantığı.
- Tarayıcıda Patron ve şablonlar (F4-3). Patron'un durum makinesi ve `validate_plan` kuralları **bağlayıcıdaki
  Python ile aynı test vektörlerini** geçmeli. İki uygulama ayrışırsa güvenlik kuralları da ayrışır. Python
  testlerindeki plan örnekleri JSON olarak dışa alınıp JS testlerinde de kullanılmalı.
- LPC'yi reddetme, Puter.js'yi reddetme, Gemini CLI'ya yatırım yapmama, ücretsiz kota rakamlarını sabit yazmama.

### 5.2 Düzeltmeler (kaynaklı)

1. **Brave, klasör seçiciyi engelliyor.** File System Access API (`showDirectoryPicker`) Brave'de varsayılan olarak
   kapalı, yalnızca bayrakla açılıyor ([web.dev](https://web.dev/file-system-access/?hl=de),
   [brave-browser #44411](https://github.com/brave/brave-browser/issues/44411),
   [Brave topluluk](https://community.brave.app/t/how-to-enable-showdirectorypicker-api-on-brave/527246)).
   Kullanıcımız Brave kullanıyor; özellik algılama ve "bu tarayıcıda yok, sanal klasör kullanılıyor" mesajı şart.
   Ayrıca gerçek klasöre yazma, modelin kullanıcının **gerçek dosyalarını** değiştirmesi demek. Şunlar gerekli:
   bağlayıcıdaki yasak adlar ve boyut tavanları, her oturumda tarayıcı izin sorusu ve ilk sürümde yalnızca yeni
   dosya yazma (var olanın üstüne yazmadan önce onay).
2. **"Etkileşimli mini uygulama" (`sandbox="allow-scripts"` + `srcdoc`) bizim CSP'mizle çalışmaz.** `srcdoc`
   belgeleri ana sayfanın CSP'sini miras alır
   ([WHATWG 2012](https://lists.whatwg.org/pipermail/whatwg-whatwg.org/2012-June/078745.html),
   [Mozilla bug 1528790](https://bugzilla.mozilla.org/show_bug.cgi?id=1528790)). Bizim `script-src 'self'`
   kuralımız iframe içindeki satır içi betikleri de engeller. Çalıştırmak için iki yol var: ana sayfanın CSP'sini
   gevşetmek (**kabul edilemez**) ya da önizlemeyi **ayrı bir alan adından** (ör. `sandbox.pixeloffice.com.tr`, ayrı
   CSP) sunmak. Bu ayrı bir tasarım ve güvenlik işidir. Taslağın §3.3'te bunu çıkarmış olması doğru; geri gelirse
   ayrı alan adıyla gelir.
3. **Diyagram ve grafik için Mermaid'i ana sayfada çalıştırmayalım.** Mermaid DOM ve betik gerektiriyor, ana
   sayfada çalışırsa XSS yüzeyi açar. `chart` blokları ve basit akış şemaları **kendi küçük SVG üreticimizle**
   yapılmalı (yalnızca sayılar ve kaçışlanmış metin), sonra `sandbox=""` iframe'de gösterilmeli. Matematik için
   tarayıcıların yerleşik MathML desteği yeterli; çalışma zamanı kütüphanesi gerekmez.
4. **Web ve bilgi araçları (Wikipedia, arXiv, Crossref) D1 kararıyla çelişiyor.** Sorgu metni de veri taşıyabilir:
   80 karakterlik bir arama terimine bir dosyanın parçası yazılabilir. Bunlar ancak **D1'in yeniden açılması**
   (kullanıcı kararı) ve araç çağrısı başına onay ile gelir. Taslak bunları almamış; doğru.
5. **MCP istemcisi de D1 sınıfında risk taşıyor.** Slack, Notion ya da GitHub gibi MCP sunucularının yazma araçları,
   modelin veriyi dışarı göndermesinin yeni bir yolu. MCP araçları varsayılan kapalı olmalı; her sunucu için
   kullanıcı açıkça izin vermeli; yazma yapan araçlarda her çağrıda onay istenmeli. Ayrı bir tasarım belgesiyle gelir
   (F4-9).
6. **Bağlayıcıda çok motorlu Patron (F4-6) D2 kararını değiştirir.** D2'nin gerekçeleri hâlâ geçerli: dolar tavanı
   Claude'un ölçülen maliyetine dayanıyor; zayıf modellerin plan kalitesi düşük. Jeton ve tur tavanıyla çalışmak
   mümkün, ama **kullanıcı kararı** gerekir (§6).
7. **Tarayıcıda ses tanıma (Antigravity raporu Paket 6) sesi varsayılan olarak Google'a gönderir.** Chrome'un
   `SpeechRecognition`'ı ses kaydını Google sunucusuna yollar
   ([Edge belgesi](https://learn.microsoft.com/en-us/microsoft-edge/web-platform/speech-recognition-api),
   [Fora Soft](https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/client-side-asr-faster-whisper-wasm-browser)).
   Bu, "verin sunucuya gitmez" sözümüzle çelişir. Ancak `processLocally` gerçekten çalışıyorsa ve kullanıcı
   bilgilendirilirse kabul edilebilir. Sesli okuma (`SpeechSynthesis`) cihazda çalıştığı için sorun değil.
8. **Yerel model indirme (WebLLM / wllama):** Model ağırlıkları Hugging Face'ten iner. Bu, CSP `connect-src`'ye yeni
   adresler eklemek, yüzlerce MB ile birkaç GB arası indirme ve kütüphanenin depoya kopyalanması demek
   (`script-src 'self'`; CDN'den betik yüklenmez). İkinci aşamada, ayrı bir onayla gelir. Taslağın kademeli yaklaşımı
   doğru.
9. **Küçük olgu düzeltmeleri (Antigravity raporu):**
   - Rol sayısı 20 değil 15 (yazılım 5 + diğer departmanlar 10).
   - "Gemini günde 1.500 ücretsiz istek" ifadesi arayüze yazılmamalı; kaynaklar 20 ile 1.500 arasında çelişiyor.
10. **ZIP için bağımlılık gerekmeyebilir.** Sıkıştırmasız (stored) ZIP yazmak ~80 satırlık bir iş; tarayıcının
    `CompressionStream` API'si de sıkıştırma için var. `fflate` ancak `.docx` okuma (deflate açma) gerekirse
    düşünülmeli; o zaman bile tarayıcının `DecompressionStream`'i önce denenmeli.

## 6. Kullanıcı Kararı Gereken Noktalar

Uygulamaya geçmeden önce:

| # | Karar | Seçenekler | Claude'un önerisi |
|---|---|---|---|
| K1 | İlk ziyaret akışı (önceden "rehber kalsın" dendi) | Rehber kalsın / Canlı ofis + "Hemen dene" | Tarayıcıda Patron (F4-3) ve anahtarsız deneme (F4-4) bitince yeniden bakılsın |
| K2 | D2: Patron Claude dışı motorlarla çalışsın mı? | Hayır / Evet, jeton ve tur tavanıyla | Önce yalnızca tarayıcıda (F4-3) denensin, kalite görülünce bağlayıcıya |
| K3 | D1: Web ve bilgi araçları | Kapalı kalsın / Onaylı ve dar araçlar | Kapalı kalsın (en azından F4-9 MCP tasarımına kadar) |
| K4 | Mağaza ve eklenti hesapları (Microsoft ~19 $, Play 25 $; VS Code ve Open VSX ücretsiz) | $0 kalsın / Tek seferlik ücret kabul | Önce ücretsiz olanlar (Open VSX, Antigravity eklentisi) |
| K5 | Ayrı önizleme alan adı (`sandbox.` alt alanı, etkileşimli mini uygulamalar için) | Yapma / Yap (Vercel'de ücretsiz) | Sonraya, F4-5 bitince |

## 7. Son Öncelik Sırası

§4'teki sıra geçerlidir. Değişiklikler:
- **F4-4:** Klasör seçici Brave'de olmadığı için önce **sanal klasör + ZIP indirme + sürükle-bırak**, klasör seçici
  sonra.
- **F4-5:** "Mini uygulama" yok (§5.2/2); SVG grafik ve diyagram kendi üreticimizle.
- **F4-6** K2'ye, **F4-9 MCP** K3'e ve ayrı bir tasarım belgesine bağlı.
- **F4-1'in ilk adımı:** Antigravity kancalarıyla "onay bekliyor" gösterimi. Bu, kenara konan "?" balonu fikrinin
  yerini alır.

## 8. Kullanıcı Kararları (2026-10-30)

Kullanıcı K1–K5 için Claude'un önerilerini onayladı:
- **K1:** İlk ziyaret şimdilik rehberde kalır; F4-3 ve F4-4 bitince yeniden bakılır.
- **K2:** Claude dışı motorlarla Patron önce yalnızca tarayıcıda (F4-3) denenir.
- **K3:** Web ve bilgi araçları kapalı kalır.
- **K4:** Önce ücretsiz dağıtım kanalları (Open VSX, Antigravity eklentisi).
- **K5:** Ayrı önizleme alan adı sonraya.

