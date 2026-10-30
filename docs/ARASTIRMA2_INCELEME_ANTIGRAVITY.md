# Araştırma 2 Karşılıklı İnceleme: `docs/ARASTIRMA2_CLAUDE.md` Değerlendirmesi

> **İnceleyen:** Antigravity  
> **İncelenen Rapor:** `docs/ARASTIRMA2_CLAUDE.md` (Claude)  
> **Karşılaştırma Raporu:** [`docs/ARASTIRMA2_ANTIGRAVITY.md`](ARASTIRMA2_ANTIGRAVITY.md)  
> **Tarih:** 2026-10-30

---

## 1. Katıldığım Öneriler

Claude'un raporundaki şu tespit ve mimari önerilere **tamamen katılıyorum**:

1. **Rakip Konumlandırması ve "Neden Farklıyız?" Tespiti (§1, §2):**
   - `Pixel Agents` (~40K VS Code indirmesi, MIT), `pixtuoid` (MIT), `Claude Office` ve `Agent Office` gibi açık kaynak alternatiflerin tamamının **yalnızca yerel makinede** çalıştığı ve çoğu zaman yalnızca Claude Code JSONL günlüklerini izlediği tespiti çok isabetlidir.
   - Pixel Office'in asıl rekabet üstünlüğünün **paylaşılan bulut oda + E2E şifreli uzaktan kumanda + Patron Odası (çoklu ajan/departman) + kurulumsuz tarayıcı ajanı** birleşimi olduğu vurgusu yol haritasının merkezinde yer almalıdır.

2. **Üç Yeni Kanca (Hook) Adaptörü ve Gemini CLI Kararı (§4.1):**
   - `pixel_hook.py` dosyasını genişleterek **Google Antigravity** (`~/.gemini/config/hooks.json` ve `.agents/hooks.json`), **OpenAI Codex CLI** (`~/.codex/hooks.json`) ve **Cursor** (`~/.cursor/hooks.json`) kancalarını tek kurulum komutuyla (`pixel_connector.py install-hooks`) bağlamak en yüksek kaldıraçlı adımdır.
   - **Gemini CLI'ya yatırım yapmama kararı (%100 doğru):** Google'ın bireysel Gemini CLI kullanımını 18 Haziran 2026 itibarıyla Antigravity CLI'ya yönlendirmesi nedeniyle Gemini CLI için ayrı adaptör yazmak ölü yatırım olurdu.

3. **Zed ACP (Agent Client Protocol, Apache-2.0) İstemcisi (§4.2):**
   - Pasif kanca bildirimlerinin ötesinde, telefondan/web'den yeni görev başlatabilmek için bağlayıcıya `pixel_connector/engines/acp.py` eklenmesi (stdlib `subprocess` + satır sonlu JSON-RPC 2.0) çok güçlü bir mimari karardır.
   - Tek bir ACP istemcisiyle 25+ ajanın (`codex-acp`, `opencode acp`, `copilot --acp`, `cursor` vb.) uzaktan kumandaya açılması ve izin modeli eşleşmeyen ajanlarda **varsayılan olarak salt-okunur (`readonly`) + güvenli başarısızlık (fail-closed)** uygulanması `AGENTS.md` §3.3 ile birebir uyumludur.

4. **VS Code / Open VSX Eklentisi ve Ofis Özelleştirme (§4.4, §4.6):**
   - Mevcut `web/` klasörünü sıfır derleme adımıyla bir VS Code `WebviewViewProvider` yan paneli olarak paketleyip hem VS Code Marketplace hem **Open VSX** (Cursor, Windsurf, Antigravity, VSCodium) üzerinde yayınlamak en büyük organik kullanıcı kanalını açacaktır.
   - **Masa ↔ Klasör Eşlemesi (`cwd` / `workspacePaths`):** Her çalışma klasörünün ofiste ayrı bir masa kümesine bağlanması, çoklu proje çalışan geliştiriciler için somut bir işlevsel değer üretir.
   - **Karakter Seçici ve 64×64 Ofis Düzenleyici:** Mevcut `renderer.js` ve `layout.js` üzerine küçük bir palet ekleyip düzeni 2 KB'lık sıkıştırılmış dizgi olarak saklamak sıfır maliyetle yüksek kişiselleştirme sağlar.

5. **Dil (`i18n.js`) ve Erişilebilirlik (`aria-live`) (§4.7):**
   - Projenin küresel açık kaynak topluluğuna açılabilmesi için `web/js/i18n.js` (`TR` / `EN`) ve ekran okuyucu/renk körlüğü desteğinin (`aria-live="polite"`, renk dışı durum ikonları) eklenmesi şarttır.

6. **"Yapılmaması Gerekenler" Listesindeki Güvenlik ve Lisans Kararları (§4.8):**
   - `Puter.js` (üçüncü taraf sunucuya veri çıkışı + CSP ihlali) ve `AI Town` Convex arka ucunun reddedilmesi.
   - **Liberated Pixel Cup (LPC)** varlıklarının (`CC-BY-SA 3.0` / `GPLv3` copyleft yayılma riski) MIT/CC0 çekirdeğe karıştırılmaması; yalnızca **Kenney CC0** veya kendi prosedürel çizimlerimizin kullanılması.
   - Sağlayıcı API anahtarlarının sunucu/röle tarafında asla tutulmama ilkesi ve sık değişen ücretsiz kota sayılarını arayüze sabit metin olarak yazmama kararı.

---

## 2. Katılmadığım Öneriler ve Nedenleri (Kaynaklı)

### 2.1. "Anahtarsız İlk Deneyim" İçin Yalnızca Chrome Prompt API'ye (`window.LanguageModel`) Güvenip WebAssembly/WebLLM Yedeğini Tamamen Reddetmek (§4.3 ve §4.8)

- **Claude'un Önerisi:** Tarayıcı içi yerel model olarak yalnızca `window.LanguageModel` (Chrome Gemini Nano) kullanılsın; `WebLLM` / `Transformers.js` / Wasm modelleri tamamen "yapılmaması gerekenler" listesine alınsın.
- **Neden Katılmıyorum:**
  1. **Tarayıcı ve İşletim Sistemi Kısıtı:** [Chrome Prompt API Resmi Belgesi](https://developer.chrome.com/docs/ai/prompt-api) uyarınca `window.LanguageModel` yalnızca masaüstü Google Chrome'da çalışır; **Brave** (bu projeyi test ettiğimiz tarayıcı dahil), **Firefox**, **Safari**, **iOS** ve **Android** tarayıcılarında **yoktur** (`undefined` döner).
  2. **Donanım Barajı:** Chrome'da bile ~22 GB boş disk alanı ve yüksek VRAM/CPU şartı arar ([Chrome Built-in AI Requirements](https://developer.chrome.com/docs/ai/get-started#hardware-requirements)).
  3. **Araç Çağırma (`tools`) Eksikliği:** Kararlı Chrome Prompt API sürümü yalnızca düz metin (`prompt()` / `promptStreaming()`) üretir; `BrowserDevice`'ın sanal klasör araçlarını (`list_dir`, `read_file`, `write_file`) çalıştıran OpenAI uyumlu `tools` / `tool_calls` şemasını yerel olarak desteklemez.
- **Karşı Önerim (Hibrit Katman):**
  - `window.LanguageModel` varsa **ilk tercih** olarak (0 MB indirme ile) kesinlikle kullanılmalıdır.
  - Ancak `window.LanguageModel` yoksa (Brave/Firefox/Edge/Safari kullanıcıları), kullanıcıyı doğrudan API anahtarı ekranına mahkûm etmek yerine isteğe bağlı ("İstersen tek tıkla 350 MB yerel modeli tarayıcı önbelleğine indir") açık kaynak **[wllama (`ngxson/wllama`, MIT, ~350 KB Wasm)](https://github.com/ngxson/wllama)** (`llama.cpp` WebAssembly SIMD portu; WebGPU olmadan CPU'da bile çalışır) veya **[WebLLM (`@mlc-ai/web-llm`, Apache-2.0)](https://github.com/mlc-ai/web-llm)** ([Function Calling desteği mevcut](https://webllm.mlc.ai/docs/user/function_calling.html)) dinamik `import()` ile yedek olarak sunulmalıdır. Dinamik `import()` kullanıldığında ana sayfa boyutu 0 bayt artar.

### 2.2. Oda Durumunu Cloudflare Durable Objects'e Taşımanın Tüm İzleyiciler İçin Sorunsuz Varsayılan Gibi Sunulması (§4.5)

- **Claude'un Önerisi:** Oda durumu (`state`) Vercel + Upstash'ten Cloudflare Durable Objects SQLite'a (`relay/worker.js`) taşınsın, tüm istemciler WebSocket'ten anlık güncelleme alsın.
- **Neden Katılmıyorum (Kota ve Hizmet Reddi Riski):**
  - [Cloudflare Durable Objects Fiyatlandırma ve Limitler Belgesi](https://developers.cloudflare.com/durable-objects/platform/pricing/) uyarınca **Workers Free** planında Durable Objects günlük **100.000 istek** ve **100.000 SQLite satır yazma** ile sınırlıdır. WebSocket mesajları 20:1 oranında sayılsa bile her WebSocket bağlantı kurulumu (`Upgrade: websocket`) ve her HTTP durumu **1 tam istek** sayılır; **günlük sınır aşılınca ücretsiz planda tüm Durable Object istekleri 00:00 UTC'ye kadar hata döner**.
  - Mevcut mimaride (`docs/CLOUD.md` §12) salt-okunur izleyiciler (`viewer`) **Vercel CDN `s-maxage=1, stale-while-revalidate=4` önbelleğinden** sunulmaktadır; yani 10.000 kişi paylaşılan bir odayı izlediğinde Upstash ve Cloudflare'e **saniyede en fazla 1 istek** gider.
  - Eğer salt-okunur izleyiciler de doğrudan Durable Object WebSocket'ine veya DO HTTP uç noktasına bağlanırsa, sosyal medyada paylaşılan popüler bir oda birkaç saat içinde 100.000 DO istek kotasını tüketir ve **E2E şifreli cihaz kumanda rölesini de kilitler**.
- **Karşı Önerim:**
  - Salt-okunur izleyiciler (**viewers**) mutlaka **Vercel CDN önbellekli HTTP okumasında** kalmalıdır.
  - Durable Object WebSocket kanalı yalnızca zaten açık olan `DeviceRelay` (sahip/bağlayıcı oturumu) üzerinden hafif bir **"durum değişti darbesi" (`room.nudge`)** göndermek için kullanılmalı, tam durum depolaması ücretsiz kota dar boğazına sokulmamalıdır.

### 2.3. Ücretli Mağaza Kayıtlarının (Microsoft Store ~19 \$, Google Play 25 \$) Öncelik Listesine Alınması (§4.4 ve §6 Öncelik 6)

- **Claude'un Önerisi:** Tek seferlik 19 \$ (Microsoft Store) ve 25 \$ (Google Play) geliştirici hesaplarıyla PWA'nın mağazalara konulması.
- **Neden Katılmıyorum:**
  - Projenin temel kısıtı **$0 maliyet** (ücretsiz katman / sıfır bütçe) ilkesidir.
  - Mobil ve masaüstü kurulum için mağaza ücretine ihtiyaç yoktur: mevcut PWA (`web/manifest.json` + `web/sw.js`) zaten Chrome/Edge/Brave/Safari üzerinden doğrudan kurulabilmektedir; masaüstü için gerekirse **[Tauri v2 (MIT / Apache-2.0)](https://v2.tauri.app/)** ile GitHub Actions ücretsiz derleyicisinde üretilen `.msi` / `.dmg` / `.AppImage` paketleri GitHub Releases üzerinden **$0 maliyetle** dağıtılabilir.

---

## 3. Claude'un Kaçırdığı Ama Antigravity Raporunda ([`docs/ARASTIRMA2_ANTIGRAVITY.md`](ARASTIRMA2_ANTIGRAVITY.md)) Olan Fikirler

Claude'un raporu dış ajan/IDE entegrasyonlarında (Hooks, ACP, VS Code eklentisi) çok derinleşmiş; ancak **mevcut kod tabanındaki darboğazları** ve **yazılımcı olmayan son kullanıcıların (Faz 2 & Faz 3) tarayıcı içindeki deneyimini** tamamlayan şu kritik noktaları kaçırmıştır:

1. **Tarayıcı Cihazında (`BrowserDevice`) Patron Odası (`BrowserBoss`), İş Akışları (`BrowserWorkflows`) ve Devir (`handoff`) Eksikliği:**
   - Faz 3'te (`web/js/browser_device.js`) tarayıcıya kurulumsuz tekil ajan (`task.create` / `prompt.ask`) eklendi; ancak **`boss.ask` (Patron Odası + 5 departman), `workflow.run` (çok adımlı şablonlar) ve `handoff` (ajanlar arası iş devri)** hâlâ yalnızca Python bağlayıcısında (`pixel_connector/`) çalışıyor!
   - Kurulum yapmayan bir yazar, öğrenci veya pazarlamacı tarayıcıdan girdiğinde Patron Odası'nı ve "İçerik ve Medya / Araştırma / Eğitim" departmanlarını **hiç kullanamıyor**. `BrowserDevice` içine tarayıcı içi orkestratör (`BrowserBoss` + `BrowserWorkflows`) eklenmelidir.

2. **Bağlayıcıdaki Patron Odası'nın (`pixel_connector/boss.py:78`) Yalnızca `claude` Motoruna Kilitli Olması:**
   - `pixel_connector/boss.py` satır 78'de `ROLE_ENGINES = ("claude",)` sabit kodludur ve tüm alt ajanlar yalnızca `claude` CLI motoruyla başlatılmaktadır.
   - E3 göreviyle `openai` (Gemini, Groq, OpenRouter, OpenAI, DeepSeek, Mistral) ve `ollama` motorlarına dosya araçları (`list_dir`, `read_file`, `write_file`) kazandırıldığı halde, Claude Code aboneliği olmayan kullanıcılar bağlayıcı kursalar bile Patron Odası'nı çalıştıramamaktadır.

3. **İlk Ziyaretçi Yönlendirmesi (`web/js/main.js:570`) ve `BrowserDevice`'ın Yalnızca Bulut Odasında Açılması:**
   - `web/js/main.js` satır 570'teki `if (isFirstVisit) window.location.replace('/rehber.html?ilk=1')` kodu ilk kez gelen ziyaretçiyi hâlâ terminal/Python kurulum rehberine atıyor.
   - Ayrıca `BrowserDevicePanel` yalnızca `setupV3` içinde (bulut odası + röle varken) bağlanıyor; yerel/demo modda (`?local=1` veya çevrimdışı PWA) birleşik `DeviceHub` başlatılmadığı için kurulumsuz tarayıcı ajanı orada açılmıyor.

4. **Kurulumsuz Gerçek Yerel Klasör Bağlama (`File System Access API`) ve Sürükle-Bırak Belge/ZIP Masası:**
   - Python kurmadan kendi bilgisayarındaki bir klasör üzerinde ajan çalıştırmak isteyen kullanıcılar için Chromium tarayıcılarda yerleşik **`window.showDirectoryPicker({ mode: 'readwrite' })`** ([MDN File System Access API](https://developer.mozilla.org/en-US/docs/Web/API/Window/showDirectoryPicker)) ile gerçek yerel klasörü `BrowserDevice` araçlarına (`list_dir`, `read_file`, `write_file`) bağlama fikri.
   - Sanal klasöre **sürükle-bırak (`drag & drop`)** ile `.md`, `.txt`, `.csv`, `.json`, `.zip`, `.docx`, `.pdf` yükleme ve ajanların ürettiği tüm dosyaları **[fflate (MIT, ~8 KB)](https://github.com/101arrowz/fflate)** ile tek tıkla `.zip` olarak indirme + **[MiniSearch (MIT, ~7 KB)](https://github.com/lucaong/minisearch)** ile istemci-içi tam metin arama (RAG).

5. **Bağlayıcıda `workflow.save` ve `workflow.delete` Protokol Mesajlarının Eksikliği:**
   - E2'de yapılan kodsuz şablon oluşturucu (`web/js/template_builder.js`) şu an `workflows.json` dosyasını indirip kullanıcının `~/.pixel-office/connector/` klasörüne elle kopyalamasını gerektiriyor. `pixel_connector/workflows.py` içine `workflow.save` ve `workflow.delete` eklenerek şablonlar arayüzden tek tıkla kaydedilmelidir.

6. **Eser Stüdyosu 2.0 (`PreviewDrawer` Geliştirmeleri — `web/js/preview.js`):**
   - E2'de açılan `PreviewDrawer` şu an yalnızca düz Markdown/HTML ve kaynak kodu gösteriyor. Yazılımcı olmayan kullanıcılar için:
     - **Markdown → Etkileşimli Slayt Sunumu:** `---` ayraçlarıyla bölünen çıktıları ileri/geri tuşlu ve tam ekran destekli slayt destesi olarak gösterme.
     - **Metinden Diyagrama (SVG):** ` ```mermaid ` veya ` ```nomnoml ` bloklarını **[nomnoml (MIT)](https://github.com/skanaar/nomnoml)** / **[Mermaid (MIT)](https://github.com/mermaid-js/mermaid)** ile akış şemasına dönüştürme ve `.svg`/`.png` indirme.
     - **Canlı Mini-Uygulama Önizlemesi:** `sandbox=""` (salt-okunur) moduna ek olarak, kullanıcının açık onayıyla `sandbox="allow-scripts"` (`allow-same-origin` **olmadan**, opak `null` origin + `connect-src 'none'` CSP ile) etkileşimli HTML/JS mini-uygulama ve **[uPlot (MIT)](https://github.com/leeoniya/uPlot)** grafik önizlemesi.

7. **Antigravity IDE Yerleşik Yan Panel Eklentisi (`sidecar.json`):**
   - VS Code eklentisine ek olarak, Antigravity IDE'nin kendi yerleşik `sidecar.json` (`SIDECAR_UI_ENTRYPOINT_AUX_PANE`) standardıyla Pixel Office'i Antigravity'nin sağ yardımcı panelinde tek tıkla açma.

8. **Sesli Patron (`Web Speech API`) ve Prosedürel Odak/Pomodoro Ambiyansı (`Web Audio API`):**
   - Sıfır harici API maliyetiyle tarayıcının yerleşik `webkitSpeechRecognition` / `SpeechRecognition` ve `speechSynthesis` API'leriyle mobilde konuşarak Patron'a görev verme; `AudioContext` pembe/kahverengi gürültü üreteciyle 0 KB ses dosyasıyla yağmur/kafe odak sesi ve masa üstü Pomodoro sayacı.

---

## 4. Claude'un "(doğrulanmadı)" Dediği Noktalardan Doğrulananlar (Bağlantılarla)

Claude'un raporunda `(doğrulanmadı)` olarak işaretlediği 5 teknik noktayı resmi belgeler ve bu çalışma ortamındaki yerleşik sistem dosyaları üzerinden doğruladım:

### 4.1. Antigravity Hooks (`hooks.json`) IDE ve CLI'da Aynı mı Çalışıyor? → **DOĞRULANDI (Evet, Birebir Aynı + Uzaktan Onay Destekli)**
- **Kaynaklar:**
  - Yerleşik Antigravity resmi belgesi: `~/.gemini/antigravity/builtin/skills/agy-customizations/docs/hooks.md` (Antigravity ile gelen yerel belge)
  - Resmi Web Belgesi: [https://antigravity.google/docs/hooks](https://antigravity.google/docs/hooks)
- **Doğrulanan Teknik Detaylar:**
  - Yükleme yolları hem **Antigravity CLI**, hem **Antigravity 2.0**, hem de **Antigravity IDE** için ortaktır:
    1. `~/.gemini/config/hooks.json` (Kullanıcı geneli)
    2. `~/.gemini/antigravity/hooks/hooks.json` (Eski/miras yol)
    3. `<workspace>/.agents/hooks.json` (Çalışma alanı)
  - Desteklenen 6 olay: `SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse`, `PostInvocation`, `Stop`.
  - **Çok Önemli (Claude'un raporunda eksik olan bilgi):** Antigravity `PreToolUse` kancası **engelleyici (blocking) karar** destekler! `stdin` üzerinden camelCase JSON (`sessionId`, `conversationId`, `workspacePaths`, `toolCall: { name, args }`) alır ve `stdout`'tan şu JSON'u bekler:
    ```json
    {"decision": "allow" | "deny" | "ask" | "force_ask", "reason": "..."}
    ```
    Yani Aşama 19'da Claude Code için yazdığımız **telefondan uzaktan araç onayı (`pixel_hook.py --decide`) Antigravity IDE ve CLI'da da birebir çalışacaktır!**

### 4.2. OpenAI Codex CLI `hooks.json` ve `codex_hooks` Özellik Bayrağı → **DOĞRULANDI**
- **Kaynaklar:**
  - Resmi Belge: [https://developers.openai.com/codex/hooks](https://developers.openai.com/codex/hooks)
  - Kaynak Depo: [https://github.com/openai/codex](https://github.com/openai/codex)
- **Doğrulanan Teknik Detaylar:**
  - Yapılandırma dosyası `~/.codex/hooks.json` veya `<repo>/.codex/hooks.json` konumundadır.
  - Güncel Codex CLI sürümlerinde kanca motoru varsayılan olarak etkindir (`[features] codex_hooks = true` eski sürümler için geriye dönük uyumluluk bayrağıdır).
  - Desteklenen olaylar Claude'un yazdığından daha geniştir: `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`.
  - `PreToolUse` ve `PermissionRequest` olaylarında çıkış kodu `0` izin verir, çıkış kodu `2` (veya JSON çıktısı) işlemi engeller.

### 4.3. Chrome Prompt API (`window.LanguageModel`) ve Brave'in Gemini Nano'yu Kapatması → **DOĞRULANDI**
- **Kaynaklar:**
  - Chrome Resmi Belgesi: [https://developer.chrome.com/docs/ai/prompt-api](https://developer.chrome.com/docs/ai/prompt-api)
  - W3C WebML Prompt API Taslağı: [https://webmachinelearning.github.io/prompt-api/](https://webmachinelearning.github.io/prompt-api/)
- **Doğrulanan Teknik Detaylar:**
  - Güncel API `await LanguageModel.availability()` (`"unavailable" | "downloadable" | "downloading" | "available"`) ve `await LanguageModel.create({ systemPrompt })` şeklindedir (eski `window.ai.languageModel` ad alanı kaldırılmıştır).
  - Brave, Chromium içindeki Google `OptimizationGuide` ikili bileşen indirmesini ve `window.LanguageModel` API'sini gizlilik politikası gereği varsayılan olarak devre dışı bırakmaktadır; bu nedenle Brave'de `typeof window.LanguageModel === 'undefined'` döner.

### 4.4. MCP Apps (`SEP-1865`) Claude İstemcisi Desteği → **DOĞRULANDI (Evet, Destekliyor)**
- **Kaynaklar:**
  - Resmi MCP Apps Blog Duyurusu: [https://blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps/](https://blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps/)
  - Resmi Spesifikasyon (`SEP-1865`): [https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp)
  - SDK Deposu: [https://github.com/modelcontextprotocol/ext-apps](https://github.com/modelcontextprotocol/ext-apps)
- **Doğrulanan Teknik Detaylar:**
  - `ui://` kaynak şeması (`mimeType: "text/html;profile=mcp-app"`, kum havuzlu `<iframe>` içinde `postMessage` JSON-RPC 2.0 köprüsü ve `io.modelcontextprotocol/ui` uzantısı) Anthropic ve OpenAI tarafından ortak standart olarak yayımlanmıştır ve **Claude Desktop ile Claude Web (`claude.ai`)** üzerinde çalışmaktadır (ayrıca VS Code Copilot ve Goose tarafından da desteklenmektedir).

### 4.5. Cloudflare Durable Objects (SQLite) Ücretsiz Plan Sınırları → **DOĞRULANDI**
- **Kaynak:**
  - Cloudflare Resmi Fiyatlandırma ve Limitler Sayfası: [https://developers.cloudflare.com/durable-objects/platform/pricing/](https://developers.cloudflare.com/durable-objects/platform/pricing/)
- **Doğrulanan Teknik Detaylar (Workers Free Plan):**
  - **İstek sınırı:** Günde **100.000 istek** (gelen WebSocket mesajları için 20 mesaj = 1 istek oranında indirim uygulanır; giden WebSocket mesajları ve WS ping'leri ücretsizdir).
  - **Süre (Duration):** Günde **13.000 GB-sn**.
  - **SQLite Depolama:** Toplam **5 GB** veri, günde **5 milyon satır okuma**, günde **100.000 satır yazma**.
  - **Aşım davranışı:** Ücretsiz planda kredi kartı/fatura çıkmaz; günlük sınır dolduğunda **00:00 UTC'ye kadar yeni istekler hata döner** (bu nedenle §2.2'de belirttiğim gibi salt-okunur izleyiciler CDN önbelleğinde tutulmalıdır).
