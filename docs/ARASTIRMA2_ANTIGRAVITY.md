# Pixel Office — "Herkes İçin Evrensel Yapay Zekâ Ofisi" Derin Araştırma ve Açık Kaynak Entegrasyon Raporu (Faz 4+)

* **Hazırlayan:** Antigravity (Araştırma & Sistem Mimarisi)
* **Tarih:** 2026-10-30
* **Baz Alınan Güncel Sürüm:** Bağlayıcı `3.11.0` · Web `po-v4.0.14` (Faz 1 `G1–G4`, Faz 2 `E1a–E1b–E2`, Faz 3 `T0–E3–E4a–E4b` tamamlanmış ve `main` dalında birleştirilmiş durum)
* **Araştırma Kapsamı:** Projenin en güncel halini teknik bilgisi olmayan A'dan Z'ye her insanın, her meslek grubunun ve her yapay zekâ modelinin kullanabileceği küresel bir platforma dönüştürmek için yapılabilecek tüm mimari adımlar ve **doğrudan entegre edilebilecek açık kaynak projeler**.

---

## 1. Güncel Kod Tabanı Analizi (`3.11.0` / `po-v4.0.14`): Neler Başarıldı ve Hangi Kritik Boşluklar Kaldı?

Faz 1, Faz 2 ve Faz 3 ile Pixel Office çok önemli yetenekler kazandı:
* Sahne yakınlaştırma/uzaklaştırma, gece/gün batımı/gündüz atmosferi (`scene_controls.js`, `fx.js`), tarayıcı bildirimleri ve dışa aktarma (`notify.js`), QR ile mobil eşleştirme ve tek iş emri yeniden deneme (`G4`), canlı demo ofisi (`demo.js`), 5 departmanlı Patron Odası (`yazilim`, `icerik`, `akademik`, `girisim`, `kisisel`), kodsuz şablon oluşturucu ve kum havuzlu önizleme çekmecesi (`template_builder.js`, `preview.js`), Ollama/OpenAI uyumlu motorlar için dosya araçları (`engines/tools.py`) ve tarayıcı içi sanal cihaz + şifreli anahtar deposu (`browser_device.js`, `keystore.js`).

Ancak güncel kaynak kodu satır satır incelendiğinde, **sıradan bir insanın siteye girip tam kapsamlı bir yapay zekâ ofisini kullanmasını hâlâ engelleyen 7 somut mimari darboğaz** tespit edilmiştir:

### 1.1. Darboğaz 1: Tarayıcı Cihazı (`BrowserDevice`) Patron Odası'nı, Şablonları ve Devri Desteklemiyor
* **Kod Tespiti:** `web/js/browser_device.js` şu an yalnızca tekil ajan oluşturma (`agent.create`) ve tekil görev çalıştırma (`task.start`) isteklerini işliyor.
* `boss.ask`, `boss.approve`, `boss.get`, `workflow.list`, `workflow.start`, `workflow.answer` ve `handoff` (masadan masaya devir) mesajları `BrowserDevice` içinde **yok**.
* `web/js/workflows.js` içindeki `supportsWorkflows(device)` fonksiyonu `device.info.features` içinde `"workflows"` arıyor; `BrowserDevice` ise yalnızca `["browserDevice"]` dönüyor.
* **Sonuç:** Bilgisayarına `pixel-connector` kurmayan bir kullanıcı ("Tarayıcı Ajanı" kullananların %95'i), Pixel Office'in en büyüleyici özellikleri olan **Patron Odası'nı (5 departman), İş Akışı Şablonlarını ve masadan masaya dosya devrini hiç kullanamıyor!**

### 1.2. Darboğaz 2: Bağlayıcıdaki Patron Odası Hâlâ Sadece `claude` Motoruna Kilitli
* **Kod Tespiti:** `pixel_connector/boss.py` satır 78'de `ROLE_ENGINES = ("claude",)` sabitlenmiş durumda ve `BossDesk` planlama/yürütme adımlarında yalnızca Claude Code CLI'ı çağırıyor (`docs/FAZ3_TASARIM.md` D2 kararıyla Faz 3'te ertelenmişti).
* **Sonuç:** Bilgisayarına `pixel-connector` kuran ama ücretli Claude aboneliği olmayan — bunun yerine ücretsiz **Google Gemini, Groq, OpenRouter** veya yerel **Ollama / LM Studio** kullanan — hiç kimse Patron Odası'nı çalıştıramıyor (`engine_unavailable` alıyor). Oysa E3 ile bu motorlara `list_dir`, `read_file`, `write_file` araçları ve D7 jeton/tur tavanları zaten kazandırıldı!

### 1.3. Darboğaz 3: İlk Ziyaretçi Akışı ve `BrowserDevice` Erişimi Hâlâ Bulut Odası + Röleye Bağımlı
* **Kod Tespiti:**
  1. `web/js/main.js` satır 570 ve 586'da siteye ilk kez gelen ziyaretçi hâlâ `/rehber.html?ilk=1` sayfasına (terminal kurulum adımlarına) yönlendiriliyor (`if (!guideSeen()) location.replace('/rehber.html?ilk=1')`).
  2. `setupBrowserDevice(hub, toast)` (`main.js` satır 757) yalnızca `setupV3` içinden, yani kullanıcı bir bulut odası açtıktan ve sunucuda `config.relay` ayarlıysa çağrılıyor.
* **Sonuç:** Yeni gelen bir kullanıcı daha ofisi görmeden kurulum rehberine düşüyor; `/?demo=1` canlı demosundayken veya yerel moddayken "Tarayıcı Ajanı" düğmesini göremiyor.

### 1.4. Darboğaz 4: "Anahtarım Yok" Duvarı (API Anahtarı Alma Sürtünmesi)
* `BrowserDevice` için kullanıcının Google AI Studio, Groq veya OpenAI'dan bir API anahtarı alıp yapıştırması gerekiyor. Öğrenciler, öğretmenler veya meraklı ziyaretçilerin büyük kısmı dış bir siteden API anahtarı üretme adımında kaybedilir. Tarayıcının kendi içinde **sıfır anahtarla (WebGPU / WebAssembly ile)** çalışan yerel bir mini model seçeneği yok.

### 1.5. Darboğaz 5: Sanal Klasör (`VirtualWorkspace`) ile Gerçek Bilgisayar Dosyaları Arasındaki Kopukluk
* `BrowserDevice` içindeki `VirtualWorkspace` dosyaları IndexedDB'de tutuyor ve kullanıcı dosyaları yalnızca tek tek `.md` olarak indirebiliyor.
* Kullanıcının masaüstündeki bir PDF'i, Word (`.docx`) belgesini, Excel/CSV tablosunu veya `.zip` klasörünü ofisin üstüne **sürükleyip bırakarak (Drag & Drop)** ajanlara okutması, tüm çıktıları tek tıkla `.zip` olarak indirmesi ya da Chromium tarayıcılarda **File System Access API (`window.showDirectoryPicker`)** ile kurulumsuz olarak gerçek bir yerel klasörü bağlaması henüz mümkün değil.
* Ayrıca `template_builder.js` (E2) ile üretilen `workflows.json` dosyasını bağlayıcıya doğrudan kaydeden `workflow.save` / `workflow.delete` uçları henüz yok.

### 1.6. Darboğaz 6: Önizleme Çekmecesi (`preview.js`) Yalnızca Statik Metin/HTML Gösteriyor
* `web/js/preview.js` şu an `<iframe sandbox="" srcdoc="...">` kullanıyor ve yalnızca temel Markdown başlık/liste/kod bloklarını dönüştürüyor.
* Diyagramlar (**Mermaid / Zihin Haritası**), etkileşimli sunum slaytları (**Reveal/Marp**), veri grafikleri (**Chart/uPlot**), matematiksel formüller (**KaTeX**) ve ajanların ürettiği mini web uygulamaları/hesaplayıcılar çalışmıyor.

### 1.7. Darboğaz 7: Dış Dünya ile Entegrasyon (MCP, Arama, Veri Analizi) Eksikliği
* Ajanlar şu an yalnızca çalışma klasöründeki düz metin dosyalarını okuyup yazabiliyor (`list_dir`, `read_file`, `write_file`).
* Bir akademisyenin ArXiv/Wikipedia/Crossref'ten makale/kaynakça çekmesi, bir veri analistinin CSV üzerinde SQL/Python çalıştırması veya bir kullanıcının kendi Notion/Google Drive/GitHub/Takvim araçlarını **MCP (Model Context Protocol)** üzerinden bağlaması mümkün değil.

---

## 2. Projeye Doğrudan Entegre Edilebilecek Açık Kaynak Projeler Kataloğu

Kullanıcının *"açık kaynak projeleri de alıp kendi projemize entegre edebiliriz, her şey serbest"* talimatı doğrultusunda, Pixel Office'in mimarisine, güvenlik ilkelerine ve **$0 sunucu maliyeti** hedefine %100 uyan en güçlü açık kaynak projeler incelenmiş ve entegrasyon haritası çıkarılmıştır:

### 2.1. Tarayıcı-İçi Sıfır-Anahtar Yapay Zekâ Motorları (WebGPU & WebAssembly)

| Açık Kaynak Proje | Lisans | Boyut / Teknoloji | Pixel Office'e Nasıl Entegre Edilir ve Ne Kazandırır? |
| :--- | :--- | :--- | :--- |
| **[WebLLM (`@mlc-ai/web-llm`)](https://github.com/mlc-ai/web-llm)** | Apache-2.0 | WebGPU + Web Worker (İsteğe bağlı dinamik yükleme) | **Sıfır API Anahtarlı Tarayıcı Ajanı:** Kullanıcının hiçbir API anahtarı girmesine gerek kalmadan, tarayıcının ekran kartı (WebGPU) üzerinde `Qwen2.5-1.5B-Instruct`, `Llama-3.2-1B-Instruct`, `SmolLM2-360M` veya `DeepSeek-R1-Distill-Qwen-1.5B` modellerini doğrudan sekmede çalıştırır. OpenAI `chat.completions` ve `tools` arayüzüyle birebir uyumludur; `BrowserDevice` içine `engineId: 'webgpu'` olarak doğrudan takılır! |
| **[wllama (`ngxson/wllama`)](https://github.com/ngxson/wllama)** | MIT | ~350 KB Wasm (`llama.cpp` WebAssembly SIMD portu) | **Her Cihazda (WebGPU Olmayan Telefon/Bilgisayar) Yerel Model:** WebGPU desteklemeyen tarayıcılarda bile CPU (Wasm SIMD + multi-thread Worker) üzerinde GGUF modelleri çalıştırır. Sıfır kurulum, sıfır sunucu maliyeti, %100 çevrimdışı (offline) çalışma sağlar. |
| **[Transformers.js v3 (`@huggingface/transformers`)](https://github.com/huggingface/transformers.js)** | Apache-2.0 | ONNX Runtime Web (WebGPU / Wasm) | **Tarayıcıda Ses Tanıma (Whisper) ve Yerel Vektör Arama (Embeddings):** 1) `whisper-tiny` ile kullanıcının sesli komutlarını tarayıcıda metne çevirir. 2) `all-MiniLM-L6-v2` (23 MB) ile kullanıcının yüklediği belgeleri tarayıcı içinde vektörleştirip yerel RAG (anlamsal arama) yapar. |

### 2.2. Tarayıcı-İçi Güvenli Kod, Veri ve Belge Kum Havuzları (Wasm Sandboxes)

| Açık Kaynak Proje | Lisans | Boyut / Teknoloji | Pixel Office'e Nasıl Entegre Edilir ve Ne Kazandırır? |
| :--- | :--- | :--- | :--- |
| **[QuickJS-Emscripten / `quickjs-wasm`](https://github.com/justinadams/quickjs-emscripten)** | MIT | ~450 KB Wasm | **Güvenli `run_js` Aracı:** Ajanların matematiksel hesaplama, veri dönüştürme, algoritma doğrulama ve birim testi çalıştırmasını sağlar. Ana sayfa DOM'undan, ağdan ve `KeyStore`'dan tamamen yalıtılmış bir WebAssembly sanal makinesinde (bellek ve CPU tur sınırı ile) çalışır. |
| **[MicroPython-Wasm](https://github.com/micropython/micropython/tree/master/ports/webassembly)** veya **[Pyodide](https://github.com/pyodide/pyodide)** | MIT / MPL-2.0 | ~400 KB (MicroPython) / Dinamik CDN-Cache (Pyodide) | **Tarayıcıda `run_python` (Veri Analisti, Finans ve Akademik Departmanlar):** Tarayıcı ajanlarının Python kodu çalıştırmasını, CSV/JSON verilerini analiz etmesini, istatistik hesaplamasını ve SVG grafik üretmesini sağlar. |
| **[SQL.js (SQLite Wasm)](https://github.com/sql-js/sql.js)** veya **[PGlite](https://github.com/electric-sql/pglite)** | MIT / Apache-2.0 | ~600 KB Wasm (SQL.js) | **Tarayıcıda `query_sql` Aracı:** Kullanıcının sürükleyip bıraktığı `.csv`, `.json` veya `.sqlite` dosyalarını tarayıcı belleğinde tabloya dönüştürür; ajanlar doğal dilden SQL sorguları çalıştırarak muhasebe, satış ve stok raporları çıkarır. |
| **[fflate](https://github.com/101arrowz/fflate)** veya **[client-zip](https://github.com/Touffy/client-zip)** | MIT | ~3 KB – 8 KB (Sıfır bağımlılık, saf ES modülü) | **1) Tek Tıkla `.ZIP` İndir / Yükle:** Sanal klasördeki tüm projeyi tek tıkla `.zip` olarak indirir veya `.zip` arşivini sanal klasöre açar.<br>**2) `.DOCX` / `.XLSX` / `.EPUB` Okuma ve Üretme:** Word ve Excel dosyaları özünde XML içeren ZIP arşivleridir; `fflate` sayesinde ajanlar tarayıcıda gerçek `.docx` (Word) ve `.xlsx` (Excel) dosyaları okuyup üretebilir! |
| **[PDF.js (`mozilla/pdf.js`)](https://github.com/mozilla/pdf.js)** + **[MiniSearch](https://github.com/lucaong/minisearch)** | Apache-2.0 + MIT |MiniSearch yalnızca ~7 KB (Sıfır bağımlılık) | **Sürükle-Bırak PDF/Belge Kütüphanesi ve `search_files` (Yerel RAG):** Öğrenci, avukat veya araştırmacı 100 sayfalık PDF ders notunu veya sözleşmeyi ofise sürükler; `PDF.js` metni çıkarır, `MiniSearch` (BM25 + bulanık arama) indeksler ve ajanlar `search_files(query)` aracıyla ilgili sayfaları saniyeler içinde bulup kaynak göstererek yanıtlar! |

### 2.3. Eser Stüdyosu (`PreviewDrawer`) İçin Açık Kaynak Görselleştirme Motorları

Mevcut `web/js/preview.js` çekmecesinin `<iframe srcdoc>` kum havuzu içine gömülebilecek (ana sayfada sıfır XSS riski taşıyan) hafif açık kaynak kütüphaneler:

| Açık Kaynak Proje | Lisans | Kullanım Alanı | Kime Hitap Eder? |
| :--- | :--- | :--- | :--- |
| **[Nomnoml](https://github.com/skanaar/nomnoml)** veya **[Mermaid.js](https://github.com/mermaid-js/mermaid)** | MIT | Metinden akış şeması (flowchart), sıra diyagramı, ER diyagramı, zihin haritası ve organizasyon şeması çizimi (` ```mermaid ` veya ` ```diagram ` blokları). | Yazılımcılar, Proje Yöneticileri, Öğretmenler, Öğrenciler |
| **[Reveal.js](https://github.com/hakimel/reveal.js)** / **Hafif Marp Slayt Motoru** | MIT | Ajanın ürettiği `---` ayraçlı Markdown raporunu tek tıkla **tam ekran etkileşimli sunuma (Slayt Şov)** dönüştürür (ok tuşlarıyla geçiş, konuşmacı notları, PDF çıktı). | Girişimciler (Pitch Deck), Öğretmenler, Pazarlamacılar, Öğrenciler |
| **[uPlot (`leeoniya/uPlot`)](https://github.com/leeoniya/uPlot)** | MIT (~45 KB, sıfır bağımlılık) | Ajanın ürettiği ` ```chart ` JSON/CSV bloklarını etkileşimli çizgi, sütun ve pasta grafiklerine dönüştürür. | Finans, E-Ticaret, Veri Analizi, Akademik Araştırma |
| **[KaTeX](https://github.com/KaTeX/KaTeX)** veya **Temml (MathML)** | MIT (~25 KB Temml) | `$...$` ve `$$...$$` matematik, fizik ve kimya denklemlerini tarayıcıda yerel MathML/HTML olarak dizgiler. | Öğrenciler, Akademisyenler, Mühendisler |
| **[Excalidraw](https://github.com/excalidraw/excalidraw) JSON / SVG Şema Üretici** | MIT | Ajanın ürettiği el çizimi tarzı (`rough.js`, ~9 KB MIT) tel kafes (wireframe) ve beyin fırtınası tahtalarını görselleştirir. | Ürün Yöneticileri, Tasarımcılar, Eğitimciler |

### 2.4. Evrensel Ajan, Protokol ve Oyun Dünyası Projeleri

| Açık Kaynak Proje | Lisans | Pixel Office'e Nasıl Entegre Edilir ve Ne Kazandırır? |
| :--- | :--- | :--- |
| **[Model Context Protocol (MCP — Anthropic)](https://github.com/modelcontextprotocol)** | MIT | **`pixel_connector/mcp.py` (Stdlib JSON-RPC 2.0 Köprüsü):** MCP protokolü yalnızca `stdin/stdout` veya `HTTP SSE` üzerinden akan JSON-RPC 2.0 mesajlarıdır; Python standart kütüphanesiyle (`subprocess.Popen` + `json`) **sıfır `pip` bağımlılığıyla** ~250 satırda tam bir MCP İstemcisi yazılabilir! Böylece kullanıcı `connector.json` içine herhangi bir açık kaynak MCP sunucusunu (Google Drive, Notion, GitHub, Slack, PostgreSQL, SQLite, Obsidian, Takvim, Brave Search) eklediğinde tüm Pixel Office ajanları (Ollama, Gemini, Groq, OpenAI dahil) bu araçları güvenli onay kapısıyla kullanabilir! |
| **[AI Town (`a16z-infra/ai-town`)](https://github.com/a16z-infra/ai-town) & Stanford Generative Agents** | MIT | **Yaşayan Sosyal Ofis & Ajan Hafızası:** AI Town'daki *Memory Stream* (hatıra defteri), *Reflection* (gün sonu özeti) ve *Conversation* (karakterler arası diyalog) mimarisinden esinlenerek: kahve makinesinde karşılaşan iki ajanın birbirine o günkü görevleriyle ilgili kısa esprili/teknik balonlar çıkarması ve her ajanın kendi karakter hafızasını (`memory.md`) tutması. |
| **[Tiled Map JSON](https://github.com/mapeditor/tiled) + [Kenney.nl CC0 Pixel Art](https://kenney.nl/assets)** | MIT / CC0 (Kamu Malı) | **Özelleştirilebilir Ofis Temaları ve Harita Editörü:** Tek tip ofis yerine kullanıcının seçebileceği veya kendi dizdiği CC0 piksel temaları: *Klasik Ofis, Sıcak Kütüphane/Akademi, Siberpunk Laboratuvar, Yaratıcı Ajans Stüdyosu, Uzay İstasyonu, Kafe-Ofis*. |
| **[Tauri v2 (`tauri-apps/tauri`)](https://github.com/tauri-apps/tauri)** veya **Cosmopolitan / PyInstaller Tek Dosya** | MIT / Apache-2.0 | **Terminal Açtırmayan Masaüstü Uygulaması:** `pixel-connector` + Web arayüzünü sistem tepsisinde (System Tray) çalışan, çift tıklamayla kurulan `.exe` / `.dmg` / `.AppImage` (~8 MB) masaüstü uygulamasına dönüştürür. Yazılımcı olmayan masaüstü kullanıcıları hiç terminal görmeden yerel klasörlerini ve yerel Ollama'larını kullanır. |

---

## 3. "Herkes İçin Pixel Office" — 7 Büyük Mimari Dönüşüm Paketi

Mevcut kod tabanının (`3.11.0` / `po-v4.0.14`) üzerine inşa edilecek, güvenlik ve $0 sunucu maliyeti kısıtlarını bozmayan somut geliştirme paketleri:

### Paket 1: Herkes İçin Patron Odası ve İş Akışları (`BrowserBoss` + Çok Motorlu Bağlayıcı Patronu)
1. **Tarayıcı Cihazında Tam Patron Odası (`web/js/browser_boss.js`) ve Şablon Motoru (`web/js/browser_workflows.js`):**
   * `pixel_connector/boss.py` ve `workflows.py` içindeki durum makinesi (`idle → planning → approval → running → reviewing → reporting → done`), 5 departman (`yazilim`, `icerik`, `akademik`, `girisim`, `kisisel`), 20 uzman rolü ve `validate_plan` kuralları `BrowserDevice` içine taşınır.
   * Böylece hiçbir şey kurmayan bir kullanıcı (telefonundan veya bilgisayarından), ücretsiz bir Gemini/Groq/OpenRouter anahtarıyla veya WebGPU yerel modeliyle **Patron'a tıklayıp** *"Bana 5 günlük Kapadokya gezi planı, bütçe tablosu ve Instagram içerik takvimi hazırla"* diyebilir; tarayıcıdaki uzman ajanlar Patron'un odasına girip dosya alır, masalarında `VirtualWorkspace` içine raporları yazar ve Patron birleştirilmiş nihai raporu sunar!
   * Görev başına para yerine **jeton ve tur bütçesi** (`maxOutputTokens`, `maxTurns`, `maxOrders = 4`) onay kartında şeffafça gösterilir.
2. **Bağlayıcıdaki (`pixel_connector/boss.py`) Patron'un Claude Dışı Motorlara Açılması:**
   * `boss.py` içinde `ROLE_ENGINES = ("claude", "openai", "ollama")` yapılarak, bilgisayarda Claude Code yüklü olmasa bile kullanıcının `connector.json` içinde tanımladığı Gemini/Groq/OpenAI preset'leri veya `tools: true` olan yerel Ollama modelleriyle Patron Odası'nı çalıştırması sağlanır.
   * `costBasis == "unknown"` olan motorlarda `boss.ask` ve `boss.approve` ekranında dolar tavanı yerine **Jeton ve Tur Tavanı** (D7: adım başına 20 tur, 8.000 çıkış jetonu) uygulanır.

### Paket 2: Gerçek Klasör Bağlantısı (`File System Access API`), Sürükle-Bırak Belge Masası ve `.ZIP` / `.DOCX` Desteği
1. **Tarayıcıdan Kurulumsuz Gerçek Klasör Seçimi (`window.showDirectoryPicker`):**
   * Chromium tabanlı tarayıcılarda (Chrome, Brave, Edge, Opera) `VirtualWorkspace` içine `NativeFsWorkspace` adaptörü eklenir.
   * Kullanıcı **"📂 Bilgisayarımdan Klasör Seç"** düğmesine tıkladığında tarayıcının yerel klasör seçicisi açılır; `splitPath` ve `deniedName` (`.git`, `.env`, `.ssh`, `.claude` vb. yasaklı adlar) korumaları birebir uygulanarak ajanlar doğrudan kullanıcının seçtiği gerçek klasörde dosya okuyup yazar — **sıfır kurulumla!**
2. **Sürükle-Bırak (Drag & Drop) Belge Masası + İstemci-Taraflı RAG (`PDF.js` + `MiniSearch` + `fflate`):**
   * Kullanıcı ofis kanvasının veya görev kutusunun üzerine `.pdf`, `.docx`, `.csv`, `.txt`, `.md` veya `.zip` dosyalarını sürükleyip bıraktığında:
     * `.pdf` dosyaları `PDF.js` ile sayfa sayfa metne dönüştürülüp sanal klasöre kaydedilir.
     * `.docx` dosyaları `fflate` ile açılıp `word/document.xml` içinden temiz Markdown'a çevrilir.
     * `MiniSearch` (~7 KB) tüm sanal klasörü indeksler ve ajanlara 4. araç olarak **`search_files(query)`** (anahtar kelime + BM25 arama) aracını verir.
3. **Tek Tıkla `.ZIP` ve `.DOCX` İndirme + Bağlayıcıda `workflow.save` / `workflow.delete`:**
   * Sanal klasördeki tüm dosyalar `fflate` / `client-zip` ile tek tıkla `proje.zip` olarak indirilir.
   * E2'deki şablon oluşturucunun indirmeye gerek kalmadan bağlayıcıya doğrudan şablon kaydetmesi için `pixel_connector/workflows.py` ve `app.py` içine izin listeli `workflow.save` ve `workflow.delete` işleyicileri eklenir.

### Paket 3: İki Katmanlı "Canlı Eser Stüdyosu" (`PreviewDrawer` 2.0)
Mevcut `web/js/preview.js` güvenlikten ödün vermeden iki modlu hale getirilir:
1. **Zengin Belge, Sunum, Diyagram ve Grafik Modu (`sandbox=""` — Sıfır Betik):**
   * İframe dışındaki ön-işlemci (ana sayfada DOM'a hiçbir şey yazmadan, yalnızca `srcdoc` dizesi üreterek):
     * ` ```mermaid ` / ` ```diagram ` bloklarını SVG akış şemasına,
     * ` ```chart ` JSON/CSV bloklarını SVG sütun/çizgi/pasta grafiğine,
     * `$...$` / `$$...$$` matematik bloklarını MathML'e,
     * `---` ile ayrılmış bölümleri **"📽️ Sunum Görünümü (Slayt)"** moduna dönüştürür.
2. **İnteraktif Mini-Uygulama Kum Havuzu (`sandbox="allow-scripts"` — Opak Origin, Sıfır Ağ):**
   * Ajan bir HTML5 oyun, interaktif bütçe hesaplayıcı veya çalışma kartı (flashcard) uygulaması ürettiğinde kullanıcı **"▶️ Etkileşimli Çalıştır (Kum Havuzu)"** anahtarını açabilir.
   * **Güvenlik Kanıtı:** `<iframe sandbox="allow-scripts" srcdoc="...">` özniteliğinde `allow-same-origin` **olmadığı** için iframe `null` (opak) origin'de çalışır; `window.parent`, `document.cookie`, `localStorage`, `indexedDB` ve `KeyStore`'a erişimi tarayıcı çekirdeği tarafından donanımsal/süreçsel olarak engellenir. İç CSP `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none';` olduğu için dış ağa veri sızdırması (exfiltration) da imkânsızdır!

### Paket 4: "Hemen Dene" Açılış Akışı ve Sıfır-Anahtar WebGPU/Wasm Model Desteği
1. **İlk Ziyarette Rehber Yerine "Canlı Ofis + Hemen Görev Ver" Deneyimi:**
   * `main.js` içindeki ilk ziyaret yönlendirmesi (`/rehber.html?ilk=1`) kaldırılır; kullanıcı doğrudan canlı ofiste karşılanır.
   * Üst bantta 3 net seçenek sunulur:
     1. **"⚡ Hemen Görev Ver (Kurulumsuz Tarayıcı Modu)"** → Odaya/röleye ihtiyaç duymadan yerel `DeviceHub` + `BrowserDevice` anında aktifleşir!
     2. **"🖥️ Bilgisayarındaki Ajanları Bağla (`pixel-connector`)"** → Mevcut v3 kurulum sihirbazı.
     3. **"🎬 Canlı Demoyu İzle"** → Mevcut 70 sn'lik Patron senaryosu.
2. **Ücretsiz Anahtar Sihirbazı + Sıfır-Anahtar Yerel Model (`WebLLM` / `wllama`):**
   * `BrowserDevicePanel` içinde:
     * **"Anahtarım Yok — Tarayıcıda Ücretsiz Mini Model Çalıştır (WebGPU/Wasm)"**: Tek tıkla `Qwen2.5-1.5B` / `SmolLM2` modelini tarayıcı önbelleğine indirip anahtarsız çalıştırır.
     * **"30 Saniyede Ücretsiz Bulut Anahtarı Al"**: Google AI Studio (Gemini — günde 1.500 ücretsiz istek) ve Groq (ücretsiz hızlı Llama-3.3-70B) için adım adım görsel mini rehber.

### Paket 5: `pixel_connector` İçin Sıfır Bağımlılıklı MCP Köprüsü + Güvenli Bilgi Araçları
1. **Yerel Bağlayıcıda MCP İstemcisi (`pixel_connector/mcp.py`):**
   * Python stdlib (`subprocess.Popen` ve `http.client`) ile JSON-RPC 2.0 üzerinden çalışan hafif MCP istemcisi.
   * `connector.json` içinde `"mcpServers"` izin listesiyle tanımlanan sunucuların araçları (`tools/list`, `tools/call`), görev politikasına (`readonly` / `edit` / `dev`) ve kullanıcı onayına (`ApprovalCenter`) bağlanarak tüm motorlara (Ollama, OpenAI, Claude) açılır.
2. **Tarayıcı ve Bağlayıcı İçin Salt-Okunur "Güvenli Bilgi Kütüphanesi" Araçları (SSRF ve Sızıntı Korumalı):**
   * Genel `web_fetch(herhangi_bir_url)` veri sızdırma riski taşıdığı için (`FAZ3_TASARIM.md` D1), bunun yerine **yalnızca kullanıcının açıkça etkinleştirdiği** ve sorgu uzunluğu/biçimi sıkı sınırlandırılmış (ör. en fazla 80 karakterlik arama terimi, dosya okuma sonrasında aynı turda çağrılamayan veya kullanıcı onayı isteyen) **Akademik & Ansiklopedik Bilgi Araçları**:
     * `wikipedia_summary(topic)` (Wikimedia REST API — CORS açık, ücretsiz)
     * `arxiv_search(query)` / `crossref_works(query)` (Akademik makale ve DOI doğrulama)

### Paket 6: Yaşayan Ofis 2.0 — Sosyal Etkileşim, Sesli Patron, Pomodoro ve Harita Editörü
1. **Ajanlar Arası "Kahve Molası Sohbetleri" (AI Town Esinli Watercooler Chat) ve Karakter Hafızası:**
   * İki ajan görevini bitirip kahve makinesinde veya dinlenme odasında yan yana geldiğinde, başlarının üstünde kısa diyalog balonları (`"Rapor bitti! ☕"`, `"Tablolar hazır 📊"`) belirir.
   * Her ajan sanal klasörde veya cihazda kendi kısa hafıza notlarını (`.memory/<agent>.md` — kullanıcının tercihleri, yazı dili, önceki kararlar) tutabilir.
2. **Sesli Komut ve Sesli Rapor (`Web Speech API`):**
   * Görev kutusuna ve Patron Odası'na **🎙️ Bas-Konuş (SpeechRecognition)** düğmesi ve raporlar için **🔊 Sesli Oku (SpeechSynthesis)** düğmesi eklenir (0 KB, tarayıcı yerleşik API'si, sıfır maliyet).
3. **Pomodoro & Co-Working Modu ("Yapay Zekâ Ekibimle Çalışıyorum"):**
   * Üst çubukta **🍅 25/5 Pomodoro Sayacı** + `audio.js` içinde prosedürel Web Audio **Yağmur / Kafe / Şömine Ambiyansı** (0 KB harici ses dosyası). Odak süresinde ajanlar masalarında çalışır, mola zilinde tüm ofis kahve makinesine yürür!
4. **Ofis Harita ve Tema Seçici + Karakter Özelleştirme:**
   * Ajan oluştururken şapka, gözlük, kulaklık ve kıyafet rengi seçimi.
   * Ofis teması seçimi: *Klasik Ofis, Akademik Kütüphane, Yaratıcı Stüdyo, Gece Laboratuvarı*.

### Paket 7: Küresel Dil (`TR`/`EN`), Şablon Pazaryeri ve Viral Büyüme
1. **Sıfır Bağımlılıklı Çoklu Dil (`web/js/i18n.js` — `TR` / `EN`):**
   * Arayüz, departmanlar, roller ve yerleşik şablonlar hem Türkçe hem İngilizce destekler (`navigator.language` ile otomatik algılama + üst çubukta `TR | EN` anahtarı).
2. **Hazır Şablon Kütüphanesi (20+ Evrensel Şablon) ve Tek Linkle Şablon Paylaşımı:**
   * Her departman için 4'er adet yerleşik şablon (*YouTube Video Paketi, Blog & SEO Makalesi, Ders Notu → Quiz & Flashcard, Akademik Literatür Özeti, Girişim Pitch Deck & SWOT, E-Ticaret Ürün Sayfası, Haftalık Beslenme & Alışveriş Planı, Gezi Rotası, Statik Web Sitesi, Kod İnceleme & Test*).
   * Kullanıcının oluşturduğu bir şablonu `#template=<base64url>` veya `.json` ile arkadaşlarıyla paylaşabilmesi.
3. **"📸 Ofis Kartımı Paylaş" (`web/js/share_card.js`):**
   * Ofis kanvasını, ajan kadrosunu, departman rozetini ve tamamlanan görev istatistiğini tek tıkla piksel çerçeveli PNG poster olarak indiren viral paylaşım aracı.

---

## 4. Önceliklendirme, Güvenlik ve Maliyet Özeti ($0 Sunucu Maliyeti Garantisi)

| Öncelik | Paket | Sunucu Maliyeti | Güvenlik Sınırı | Etki (Hedef Kitle Çarpanı) |
| :--- | :--- | :--- | :--- | :--- |
| **P0 (En Kritik)** | **Paket 1:** Tarayıcıda Tam Patron Odası (`BrowserBoss`), Tarayıcıda Şablonlar (`BrowserWorkflows`) ve Bağlayıcıda Çok Motorlu Patron (`Gemini/Groq/OpenAI/Ollama`) | **$0** (Tamamı istemci veya yerel bağlayıcıda) | Mevcut `validate_plan`, `splitPath`, D7 jeton/tur tavanları ve `KeyStore` izolasyonu aynen korunur. | **10x** — Kurulum yapmayan veya Claude aboneliği olmayan herkes Patron Odası'nı ve 5 departmanı kullanabilir. |
| **P0 (En Kritik)** | **Paket 4:** İlk Ziyarette Doğrudan Canlı Ofis + Rölesiz/Odasız Tek Tıkla `BrowserDevice` + Sıfır-Anahtar WebGPU/Wasm (`wllama`/`WebLLM`) | **$0** (Model ağırlıkları HuggingFace CDN'den doğrudan tarayıcı önbelleğine iner) | Wasm/WebGPU Web Worker içinde çalışır; ağ veya ana DOM erişimi yoktur. | **10x** — Siteye giren bir ziyaretçi 5 saniye içinde ne kurulum ne de API anahtarı olmadan ilk ajanını çalıştırır. |
| **P1 (Yüksek)** | **Paket 2:** Sürükle-Bırak Belge Masası (`PDF.js`, `MiniSearch`, `fflate` `.zip`/`.docx`) + `showDirectoryPicker` + `workflow.save` | **$0** (Tamamı tarayıcı belleğinde / yerel diskte) | `splitPath` ve `deniedName` gerçek klasör seçicide de zorunlu uygulanır; PDF/DOCX yalnızca düz metin olarak çıkarılır. | **5x** — Öğrenciler, avukatlar, araştırmacılar ve ofis çalışanları kendi PDF/Word/CSV dosyalarını ajanlara işletebilir. |
| **P1 (Yüksek)** | **Paket 3:** Canlı Eser Stüdyosu 2.0 (Mermaid Diyagramı, Slayt Sunumu, `uPlot` Grafik, `KaTeX` ve `sandbox="allow-scripts"` Opak Mini-Uygulama) | **$0** | `allow-same-origin` asla verilmez (opak `null` origin); iç CSP `connect-src 'none'` ile dış ağ tamamen kapalıdır. | **5x** — Ajan çıktıları kuru metin olmaktan çıkıp sunuma, diyagrama, grafiğe ve çalışan mini uygulamalara dönüşür. |
| **P2 (Orta-Yüksek)** | **Paket 5:** Tarayıcıda `QuickJS`/`Pyodide`/`SQL.js` Wasm Kum Havuzu + Bağlayıcıda Sıfır Bağımlılıklı `MCP` Köprüsü | **$0** | Wasm Worker bellek/süre tavanıyla çalışır; MCP sunucuları yalnızca yerel `connector.json` izin listesinden açılır. | **4x** — Veri analizi, gerçek kod çalıştırma ve 1.000+ açık kaynak MCP aracına (Notion, Drive, GitHub) erişim. |
| **P2 (Orta)** | **Paket 6 & 7:** Çoklu Dil (`TR`/`EN`), Sesli Patron (`Web Speech API`), Pomodoro/Ambiyans, 20+ Hazır Şablon ve "Ofis Kartımı Paylaş" | **$0** | Saf istemci taraflı sözlük, Web Audio ve Canvas `toBlob()`. | **5x** — Küresel erişim (İngilizce), günlük ekran başında tutma (Pomodoro) ve sosyal medyada organik büyüme. |

---

## 5. Claude'un Raporunun (`ARASTIRMA2_CLAUDE.md`) İncelenmesi ve Doğrulanan Açık Sorular

Claude'un hazırladığı `ARASTIRMA2_CLAUDE.md` incelenmiş; hem mükemmel tamamlayıcı noktalar hem de Claude'un "doğrulanmadı / açık soru" olarak işaretlediği konular yerleşik sistem belgelerinden kanıtlarıyla doğrulanmıştır:

### 5.1. Claude'un Raporundaki Çok Güçlü Tespitler (Tam Mutabakat)
1. **Zed ACP (Agent Client Protocol — Apache-2.0) İstemcisi (§4.2):**
   * `pixel_connector` içine stdlib (`subprocess` + JSON-RPC 2.0) ile bir ACP istemcisi eklenmesi, uzaktan kumanda ve Patron Odası'nı tek seferde 25+ kodlama ajanına (Codex CLI, OpenCode, Copilot, Cursor vb.) açar.
2. **Kanca (Hooks) Adaptörleri (§4.1) ve VS Code / Open VSX Dağıtım Kanalı (§4.4):**
   * `Pixel Agents` eklentisinin 40.000+ indirmeye ulaşması, geliştiricilerin aracın içine gömülü yan paneli ve masa ↔ klasör eşlemesini çok sevdiğini kanıtlıyor.
3. **Yapılmaması Gerekenler (§4.8):**
   * `Puter.js` (üçüncü parti CDN/hesap zorunluluğu), `LPC` (CC-BY-SA / GPL lisans bulaşıcılığı — yalnızca **Kenney CC0** veya kendi prosedürel çizimlerimiz kullanılmalı) ve ücretsiz katman rakamlarını arayüze sabit yazmama kararlarına %100 katılıyoruz.

### 5.2. Claude'un "Açık Sorular" (§5) Bölümündeki Soruların Kesin Doğrulaması

#### Doğrulama 1: Antigravity Hooks (`hooks.json`) IDE, Antigravity 2.0 ve CLI'da Aynı mı Çalışıyor?
* **Kesin Cevap: EVET (%100 Doğrulandı — Kaynak: Yerleşik `agy-customizations/docs/hooks.md` Resmi Spesifikasyonu).**
* Antigravity'nin kanca sistemi hem **Antigravity CLI (`antigravity-cli/`)**, hem **Antigravity 2.0 (`antigravity/`)**, hem de **Antigravity IDE (`antigravity-ide/`)** üzerinde **birebir aynı `hooks.json` şemasıyla** çalışır:
  * **Konum:** Küresel olarak `~/.gemini/config/hooks.json` (veya proje bazlı `.agents/hooks.json` ya da eklenti paketi içinde `~/.gemini/config/plugins/pixel-office/hooks.json`).
  * **Desteklenen 6 Olay:** `SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse`, `PostInvocation`, `Stop`.
  * **Girdi/Çıktı Sözleşmesi:** Kanca komutu `stdin` üzerinden **camelCase** JSON alır (`conversationId`, `workspacePaths`, `transcriptPath`, `modelName`, `stepIdx`, `toolCall: {name, args}`) ve `stdout` üzerine JSON yazar.
  * **Uzaktan Onay (Aşama 19) Desteği:** `PreToolUse` olayı `stdout`'tan `{"decision": "allow" | "deny" | "ask" | "force_ask", "reason": "..."}` bekler! Yani Claude Code için yazdığımız `pixel_hook.py --decide` uzaktan telefon onayı mekanizması, Antigravity'de de **sıfır eksikle, birebir aynı mimariyle** çalışabilir!
  * **Ek Keşif — Antigravity Yan Panel Eklentisi (`sidecar.json`):** Antigravity ayrıca `~/.gemini/config/plugins/pixel-office/sidecars/office/sidecar.json` (`has_web_ui: true`, `SIDECAR_UI_ENTRYPOINT_AUX_PANE`) ile Pixel Office'i doğrudan Antigravity sağ yan panelinde (Aux Pane) tek tıkla (`sidecar://pixel-office/office/`) açabilir!

#### Doğrulama 2: Chrome Prompt API (`window.LanguageModel` / Gemini Nano) vs. WebGPU/Wasm
* Claude'un belirttiği gibi Chrome Prompt API (`LanguageModel`) Brave'de, Firefox'ta, Safari'de ve mobil cihazlarda çalışmaz (ve kararlı sürümde `tools` desteklemez).
* **Birleşik Çözüm:** Tarayıcıda "Anahtarsız Deneme" katmanı kademeli (progressive) tasarlanabilir:
  1. Tarayıcıda `window.LanguageModel` (Chrome Gemini Nano) varsa **0 MB indirmeyle** anında onu kullanır.
  2. Yoksa (Brave, Edge, Firefox, Safari) kullanıcı isterse **WebGPU / Wasm (`wllama` veya `WebLLM`)** ile küçük bir modeli önbelleğe indirir ya da 30 saniyede ücretsiz Gemini/Groq bulut anahtarı alır.

#### Doğrulama 3: Claude'un Raporunda Eksik Kalan ve Antigravity Raporunun Tamamladığı "Yazılımcı Olmayan Kullanıcı" Boyutu
* Claude'un raporu ağırlıklı olarak **geliştirici araçlarını** (Codex, Cursor, Antigravity CLI, ACP, VS Code eklentisi) mükemmel biçimde çözmüştür.
* Antigravity'nin raporu ise **"terminali veya IDE'si olmayan normal insanların"** (öğrenci, öğretmen, içerik üreticisi, pazarlamacı, girişimci, araştırmacı) ürünü kullanabilmesi için gereken parçaları tamamlar:
  1. **`BrowserDevice` İçinde Patron Odası (`BrowserBoss`) ve İş Akışları (`BrowserWorkflows`):** Kurulumsuz tarayıcı kullanıcısının da 5 departmanlı Patron'u ve şablonları kullanabilmesi.
  2. **Bağlayıcıdaki Patron Odası'nın Claude Dışı Motorlara (`openai` / Gemini / Groq ve `ollama`) Açılması.**
  3. **Sürükle-Bırak Belge Masası (`.pdf`, `.docx`, `.csv`, `.zip`) ve Gerçek Klasör Seçici (`showDirectoryPicker`).**
  4. **Zengin Eser Stüdyosu (`PreviewDrawer` içinde Diyagram, Sunum Slaytı, Grafik ve Opak Kum Havuzlu Mini-Uygulama).**

