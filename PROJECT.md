# Pixel Office 🖥️☕💤

> **Proje durumu: v1.0 ✅ tamamlandı (yerel mod, 2026-10-11). v2 "Bulut Ofis" ✅ tamamlandı (Aşama 6–10 ✅, 2026-10-11); kullanıcının Vercel'e ilk dağıtımı bekleniyor (bkz. §5b "Kullanıcının elle yapması gerekenler").** Kullanım için bkz. [`README.md`](README.md), bulut tasarımı için [`docs/CLOUD.md`](docs/CLOUD.md).
>
> Tarayıcıda çalışan, pixel-art tarzı sanal bir ofis. Bilgisayarınızda çalışan her **Claude Code** oturumu (ve alt-ajanları) ofiste küçük bir pixel karakter olarak görünür.

## 1. Proje Amacı

Aynı anda çalışan birden fazla yapay zeka kodlama ajanının ne yaptığını tek bakışta, eğlenceli bir şekilde görmek:

| Ajan durumu | Karakter ne yapar? |
|---|---|
| Yeni oturum açıldı (`spawning`) | Kapıdan içeri yürüyerek girer, boş bir masaya gider |
| Çalışıyor – araç kullanıyor (`working`) | Masasında oturur, klavyede yazar; başının üstünde araç ikonu/ismi |
| Düşünüyor – model yanıt üretiyor (`thinking`) | Masasında oturur, "..." düşünce balonu |
| Kullanıcıdan izin/cevap bekliyor (`waiting_for_user`) | Masasında oturur, kırmızı "!" balonu yanıp söner |
| Boşta – turu bitti (`idle`) | Kahve makinesine yürür, kahve içer |
| İşi bitti / uzun süredir sessiz (`sleeping`) | Dinlenme odasındaki yataklara yürür, uyur (Zzz balonları) |
| Oturum kayboldu (`gone`) | Kapıya yürür ve ofisten çıkar |

## 2. Özellikler

- Gerçek veri: `~/.claude/projects/<proje-klasörü>/<oturum-id>.jsonl` transcript dosyalarını **salt-okunur** izler (≈1 sn aralıkla, artımlı okuma).
- Alt-ajanlar (`<oturum-id>/subagents/agent-*.jsonl`) ayrı karakter olarak görünür, ana ajana bağlıdır.
- Canlı güncelleme: Server-Sent Events (SSE), otomatik yeniden bağlanma.
- Demo modu (`--demo`): ~5 sahte ajan tüm durumlar arasında döner — gerçek veri olmadan denemek için.
- Tüm sprite'lar JS içinde, string pixel haritaları + paletlerden **prosedürel** üretilir (harici resim yok).
- Karakter başına farklı ten/saç/gömlek/pantolon renkleri (oturum id'sinden deterministik).
- A* yol bulma ile karakterler duvarlara/mobilyalara çarpmadan yürür.
- Karaktere tıklayınca yan panelde detay: proje, durum, son araç, son mesaj, süre.

## 3. Teknoloji Yığını

- **Backend:** Python 3.14 ile geliştirildi (en az 3.11 gerekir), yalnızca standart kütüphane (`http.server.ThreadingHTTPServer`, `json`, `threading`, `argparse`). Node.js **yok**.
- **Frontend:** Saf HTML + CSS + vanilla JavaScript ES modülleri + `<canvas>`. Build adımı yok, CDN yok, harici resim yok.
- **Protokol:** `GET /api/agents` (anlık görüntü), `GET /events` (SSE akışı), `/` statik dosyalar (`web/`).

## 4. Nasıl Çalıştırılır

```bash
cd Pixel                          # depoyu klonladığınız klasör
python3 server.py                 # gerçek Claude Code oturumlarını izler
python3 server.py --demo          # sahte ajanlarla demo
python3 server.py --port 9000 --projects-dir ~/.claude/projects
# Tarayıcıda: http://localhost:8765
```

Ayrıntılar, seçenekler ve sorun giderme: [`README.md`](README.md).

Teknik ayrıntılar: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — **tüm ajanlar uygulamadan önce bunu okumalı; arayüzler oradaki tanımlara birebir uymalı.**

## 5. Aşamalar

| # | Aşama | Sorumlu | Teslimatlar | Durum |
|---|---|---|---|---|
| 1 | Mimari & Spesifikasyon | Yazılım Mimarı | `PROJECT.md`, `docs/ARCHITECTURE.md` | ✅ Tamamlandı |
| 2 | Pixel Art Sprite'lar | Pixel Art Uzmanı | `web/js/sprites.js`, `web/sprite-preview.html` | ✅ Tamamlandı |
| 3 | Backend | Backend Mühendisi | `server.py`, `pixel_office/` paketi | ✅ Tamamlandı |
| 4 | Oyun Motoru & Arayüz | Frontend/Oyun Mühendisi | `web/index.html`, `web/css/style.css`, `web/js/*.js` | ✅ Tamamlandı |
| 5 | Test, Hata Düzeltme & README | QA Mühendisi | testler, düzeltmeler, `README.md` | ✅ Tamamlandı |
| 6 | Bulut Mimarisi | Bulut/Yazılım Mimarı | `docs/CLOUD.md` | ✅ Tamamlandı |
| 7 | Bulut Backend | Backend Mühendisi | `api/`, `pixel_cloud/`, `dev_server.py`, `vercel.json`, testler | ✅ Tamamlandı |
| 8 | Ajan Entegrasyonu | Entegrasyon Mühendisi | `web/hook/` (Python + PowerShell istemci, kurulum betikleri, `pixel_agent.py`: kütüphane, `pixel-run`, `pixel-proxy`), testler | ✅ Tamamlandı |
| 9 | Bulut Arayüzü & PWA | Frontend Mühendisi | `transport.js`, `cloud.js`, `pip.js`, manifest, `sw.js`, ikonlar | ✅ Tamamlandı |
| 10 | QA & Yayına Hazırlık | QA Mühendisi | uçtan uca test, README dağıtım rehberi, `.gitignore`, `LICENSE` | ✅ Tamamlandı |

Durum işaretleri: ⬜ Bekliyor · 🔄 Devam ediyor · ✅ Tamamlandı · ⚠️ Sorunlu

### Aşama 1 — Mimari & Spesifikasyon (Yazılım Mimarı)
- [x] Gerçek transcript formatının incelenmesi (`type`, `message.content`, `stop_reason`, alt-ajan klasörleri)
- [x] Durum modeli ve sayısal eşiklerle sezgisel kurallar
- [x] HTTP/SSE protokolü ve JSON şeması
- [x] Ofis haritası (22×14, ASCII, tutarlılığı script ile doğrulandı)
- [x] Sprite spesifikasyonu ve `sprites.js` API'si
- [x] JS ve Python modül arayüzleri

**Kabul kriterleri:** ARCHITECTURE.md, modüllerin birbirinden bağımsız uygulanıp sorunsuz birleşmesine yetecek kesinlikte olmalı. ✔️

### Aşama 2 — Pixel Art Sprite'lar (Pixel Art Uzmanı)
Teslimat: `web/js/sprites.js` (ARCHITECTURE.md §5).
- [x] Karakter sprite'ları 16×24: yürüme (aşağı/yukarı/sağ ×4 kare, sol = sağın aynası), ayakta durma, oturup yazma (2), kahve içme (2), uyuma (2)
- [x] Palet takası ile karakter varyasyonu (`makeCharacterPalette(seed)`)
- [x] Zemin, duvar, pencere, kapı, halı, masa+bilgisayar, sandalye, kahve makinesi + tezgah, su sebili, kitaplık, bitki, yatak sprite'ları
- [x] Balon/ikon sprite'ları: Zzz, "...", "!", kahve buharı, araç ikonları
- [x] Önbellekleme: aynı (sprite, palet) için tek canvas üretilir

**Kabul kriterleri:** Tüm fonksiyonlar §5'teki imzalara birebir uyar; harici dosya yok; tüm haritalarda satır uzunlukları tutarlı (geliştirici modunda `validateSprites()` hata vermez); `web/sprite-preview.html` (opsiyonel) tüm sprite'ları gösterir. ✔️

### Aşama 3 — Backend (Backend Mühendisi)
Teslimat: `server.py`, `pixel_office/__init__.py`, `config.py`, `models.py`, `store.py`, `transcript.py`, `watcher.py`, `simulator.py`, `httpd.py`.
- [x] Transcript ayrıştırıcı (yarım satırlara dayanıklı, artımlı okuma)
- [x] Watcher: ~1 sn polling, mtime/size, alt-ajan keşfi
- [x] Durum makinesi: §2'deki eşikler
- [x] Store + değişiklik olayları; SSE yayını, 15 sn keep-alive
- [x] Demo simülatörü (`--demo`, 5 ajan)
- [x] CLI: `--demo`, `--port` (8765), `--projects-dir`, `--host`

**Kabul kriterleri:** Sadece stdlib; `curl localhost:8765/api/agents` §3 şemasına uyan JSON döner; `curl -N localhost:8765/events` olay akışı gösterir; transcript dosyalarına asla yazmaz; 50 MB'lık transcript'te başlangıç < 2 sn (sadece son 256 KB okunur). ✔️

### Aşama 4 — Oyun Motoru & Arayüz (Frontend/Oyun Mühendisi)
Teslimat: `web/index.html`, `web/css/style.css`, `web/js/office.js`, `pathfinding.js`, `agents.js`, `renderer.js`, `ui.js`, `main.js`.
- [x] Harita yükleme ve yürünebilirlik (`office.js`)
- [x] A* yol bulma (`pathfinding.js`)
- [x] Character sınıfı + durum makinesi, spot rezervasyonu (`agents.js`)
- [x] Y-sıralı çizim, balonlar, isim etiketleri (`renderer.js`)
- [x] Yan panel, tıklayıp inceleme (`ui.js`)
- [x] SSE istemcisi + yeniden bağlanma, sabit zaman adımlı oyun döngüsü (`main.js`)

**Kabul kriterleri:** `--demo` ile açıldığında karakterler kapıdan girer, masaya oturur, kahveye/yatağa gider, duvarlardan geçmez; bağlantı koparsa "Yeniden bağlanıyor…" gösterilir ve geri gelir; 60 FPS'te CPU kullanımı makul; konsolda hata yok. ✔️

### Aşama 5 — Test, Hata Düzeltme & README (QA Mühendisi)
- [x] `tests/` altında `unittest` testleri: transcript ayrıştırma, durum eşikleri, store olayları, HTTP uçları
- [x] Demo ve gerçek modda manuel uçtan uca test
- [x] Bulunan hataların düzeltilmesi
- [x] `README.md` (Türkçe + kısa İngilizce özet): kurulum, kullanım, ekran görüntüsü açıklaması, sorun giderme

**Kabul kriterleri:** `python3 -m unittest discover tests` geçer; README'deki komutlar çalışır. ✔️

## 5b. v2 — Bulut Ofis

**Amaç:** Arkadaşlar tek bir bağlantıyla aynı pixel ofise girer ve birbirlerinin ajanlarını canlı görür. Uygulama Vercel'de barındırılır. Masaüstü uygulaması (PWA) olarak yüklenebilir ve ekranın sağ altına sürüklenen, her zaman üstte duran bir mini pencerede (Document Picture-in-Picture) açılabilir. Her karakterin üstünde sahibinin adı ve proje adı yazar, her sahibin kendi gömlek rengi vardır. **Yerel mod (`python3 server.py`) olduğu gibi çalışmaya devam eder.**

Teknik sözleşme: [`docs/CLOUD.md`](docs/CLOUD.md). Bulut aşamalarındaki tüm ajanlar uygulamadan önce bunu okumalı.

**Temel kararlar (özet):**
- **Barındırma:** Vercel Hobby. Python fonksiyonları (`api/*.py`, yalnızca stdlib) ve `web/` klasöründen statik dosyalar.
- **Depolama:** Upstash Redis (Vercel Marketplace, ücretsiz plan, ayda 500 bin komut). Her oda tek bir Redis hash'i olarak tutulur.
- **Güncelleme yöntemi:** Uyarlamalı polling. Oda aktifken 10 sn, sakinken 30 sn, boşken 60 sn. Sekme gizliyse ve mini pencere de kapalıysa polling durur. Okuma ucu Vercel CDN'inde 5–30 sn önbelleklenir.
- **Erişim:** Oda kimliği 128 bit rastgele bir değerdir ve odayı izleme yetkisi verir. Yazma anahtarı (HMAC) yalnızca davet bağlantısının `#` sonrasında ve `Authorization` başlığında taşınır.
- **Gizlilik:** Hook olayları arkadaşın kendi bilgisayarında süzülür. Sunucuya yalnızca durum, araç adı, sabit bir etiket, proje klasörünün adı ve sahip adı gider. İstem, kod ve dosya içerikleri **gönderilmez**.
- **Ajanları yeniden adlandırma:** Ad, hem yerel hem bulut modunda ayrıntılar panelinden değiştirilebilir. Adlar durumdan ayrı saklanır, bu yüzden hook güncellemeleri bir adı asla ezemez. Bir ad yalnızca o ajana ya da sahibin/projenin sonraki oturumlarına varsayılan olarak verilebilir.
- **Yerel yapay zeka ajanları:** Yerel sunucuya bulutla aynı `POST /api/status` ucu eklenir. `pixel_agent.py` içinde bir kütüphane, `pixel-run` komut sarmalayıcısı (aider, Open Interpreter…) ve `pixel-proxy` bulunur. `pixel-proxy`, Ollama/LM Studio/llama.cpp için OpenAI uyumlu ve akışı bozmayan bir ara sunucudur.
- **Python'suz Windows:** Windows'la birlikte gelen PowerShell 5.1 için aynı işi yapan bir istemci (`pixel_hook.ps1`) var, gizlilik garantisi aynı. Ham veri gönderen "HTTP kancası" yalnızca açık onayla kullanılabilen bir son çaredir.

| # | Aşama | Sorumlu | Durum |
|---|---|---|---|
| 6 | Bulut Mimarisi | Bulut/Yazılım Mimarı | ✅ Tamamlandı |
| 7 | Bulut Backend | Backend Mühendisi | ✅ Tamamlandı |
| 8 | Ajan Entegrasyonu | Entegrasyon Mühendisi | ✅ Tamamlandı |
| 9 | Bulut Arayüzü & PWA | Frontend Mühendisi | ✅ Tamamlandı |
| 10 | QA & Yayına Hazırlık | QA Mühendisi | ✅ Tamamlandı |

Bağımlılıklar: Aşama 7 ile 8 paralel yürüyebilir. 8, sunucu tarafındaki `cc_hook` ucu için 7'deki `pixel_cloud.validate` ile uyumlu olmalıdır. 9, 7'deki `dev_server.py --demo` ile geliştirilir. 10 en son yapılır.

### Aşama 6 — Bulut Mimarisi (Bulut/Yazılım Mimarı)
Teslimat: `docs/CLOUD.md`.
- [x] Vercel Python çalışma ortamı araştırması (dosya düzeni, `handler` sınıfı, Python sürümü, `vercel.json`, Hobby limitleri, GitHub'dan içe aktarma)
- [x] Depolama karşılaştırması (Upstash Redis, Supabase ve diğerleri) ve seçim: Upstash Redis
- [x] Ücretsiz plan bütçe hesabı (polling aralıkları, CDN önbelleği, yazma birleştirme)
- [x] Claude Code hook olayları araştırması ve olay → ofis durumu eşleme tablosu
- [x] HTTP uçları ve tam JSON şemaları, kimlik doğrulama modeli, kötüye kullanım limitleri
- [x] Gizlilik modeli (yerel süzme, izin listesi) ve Python'suz Windows yolu
- [x] Frontend taşıma (transport) arayüzü, PWA ve PiP davranışı
- [x] Yerel geliştirme, test planı ve dağıtım adımları

**Kabul kriterleri:** CLOUD.md, Aşama 7–9'un birbirinden bağımsız uygulanıp sorunsuz birleşmesine yetecek kesinlikte olmalı. Kaynaklar tarihli olarak verilmeli. Bütçe hesabı açık formüllerle yazılmalı. ✔️

### Aşama 7 — Bulut Backend (Backend Mühendisi)
Teslimat: `api/{config,health,rooms,state,status,cc_hook}.py`, `pixel_cloud/` (`settings`, `ids`, `validate`, `derive`, `storage`, `ratelimit`, `app`, `vercel`, `demo`), `dev_server.py`, `vercel.json`, `.vercelignore`, `.python-version`, `tests/test_cloud_*.py` (CLOUD.md §2–§6, §8, §13).
- [x] `ids.py`: oda kimliği (base32, 26 karakter), HMAC yazma anahtarı, ajan kimliği hash'i
- [x] `validate.py`: alan limitleri, kontrol/bidi karakteri temizliği, toplu gönderim ≤ 10, gövde boyutu sınırları
- [x] `derive.py`: okuma anında durum geçişleri (§6.2), v1 ile uyumlu Agent JSON, sahip renkleri, `pollMs` ve CDN TTL seçimi
- [x] `storage.py`: `MemoryStorage` (TTL'li, thread-safe), `UpstashStorage` (REST, keep-alive, `/pipeline`), ortam değişkeni seçimi (`KV_*` veya `UPSTASH_*`), Vercel'de depolama yoksa 503
- [x] `ratelimit.py`: süreç içi token bucket'lar (oda, IP, oda oluşturma)
- [x] `app.py`: tüm uçlar, hata kodları, CORS, `Cache-Control` başlıkları, `PIXEL_CREATE_TOKEN`
- [x] `vercel.py` + 6 ince `api/*.py` dosyası
- [x] `dev_server.py` (`--demo`, `--port`, `--host`; statik `web/` + `/api/*`, güvenlik başlıkları) ve `pixel_cloud/demo.py`
- [x] Sahte Upstash REST sunucusuyla depolama testleri; uçtan uca dispatch testleri
- [x] **Bulutta yeniden adlandırma (CLOUD.md §15.2–§15.3):** `api/rename.py`. Adlar aynı hash'te `n:<agentId>` ve `no:<ownerId>` alanlarında tutulur. Kapsam `agent` veya `owner`; sıfırlama `null` ile yapılır. Ad 1–24 karakterdir ve temizlenir. Her ad değişikliği 1 Redis komutudur; okumaya ek maliyet getirmez. Oda başına en fazla 96 ad alanı olur. `derive.py` ad önceliğini, `nameSource` ve `defaultName` alanlarını hesaplar.
- [x] **Yerel sunucuda yeniden adlandırma (§15.4):** `POST /api/agents/rename` ucu ve `pixel_office/names_store.py`. Dosya kullanıcı veri dizinindedir (`~/.local/share/pixel-office/names.json`; macOS ve Windows karşılıkları ayrı; `--names-file` ile değiştirilebilir). Yazım atomiktir ve dosya git dışında kalır. Ad değişince SSE ile `agent_updated` yayınlanır. Kökteki eski `names.json` çalışmaya devam eder.
- [x] **Yerel genel durum API'si (§16):** yerel sunucuda `POST /api/status`. Bulutla aynı şema ve aynı yanıtları kullanır (ortak `pixel_cloud.validate` ve `derive`). Loopback dışında dinlerken `--write-key` gerekir. `Origin` ve `Content-Type` ile CSRF koruması yapılır. Zamana bağlı geçişleri `pixel_office/reported.py` yürütür. `/api/health` yanıtına `features` alanı eklenir. Bulut ve yerel istemciler için `name` ve `final` alanları eklenir.

**Kabul kriterleri:** Yalnızca stdlib kullanılır. `python3 -m unittest discover -s tests` geçer ve v1'in 58 testi değişmeden geçmeye devam eder. `python3 dev_server.py --demo` ile `curl` üzerinden oda oluşturma, `status` yazma ve `state` okuma CLOUD.md §5'teki JSON'ları birebir döndürür. Okuma yanıtları etkinliğe göre doğru `s-maxage` değerini taşır. Anahtarsız ya da yanlış anahtarlı yazma isteği 401 alır. 4 KB'tan büyük gövde 413 alır. Yerel mod geriye dönük uyumludur: v1 uçları ve SSE olayları aynı kalır, yalnızca yeni uçlar ve yeni alanlar eklenir. v1 testleri değişmeden geçer. Bir ad değişikliği hem bulutta hem yerelde sonraki durum güncellemelerinden sonra da korunur. ✔️

### Aşama 8 — Ajan Entegrasyonu (Entegrasyon Mühendisi)
Teslimat: `web/hook/pixel_hook.py`, `web/hook/pixel_hook.ps1`, `web/hook/install.sh`, `web/hook/install.ps1`, `web/hook/pixel_agent.py`, `tests/test_hook_client.py`, `tests/test_pixel_agent.py`, `tests/hook_vectors.json`, `docs/INTEGRATIONS.md` (CLOUD.md §7, §17; sapmalar §17.8).
- [x] `map_event`: §7.2 eşleme tablosu (alt-ajanlar, MCP → "MCP", sabit Türkçe etiketler)
- [x] Yazma birleştirme (§7.3): durum dosyası, 1 sn debounce + `seq` kontrolü, 30 sn araç kısıtlaması, 5 dk keep-alive, PostToolUse yalnızca bekleme durumundan çıkışta gönderilir
- [x] Gizlilik izin listesi (§7.4), proje takma adları/gizleme, `.pixel-office-ignore`, `PIXEL_OFFICE_DISABLE=1`
- [x] Alt komutlar: `hook`, `install`, `uninstall`, `test`, `status`, `doctor`
- [x] `~/.claude/settings.json` güvenli birleştirme: yedek alınır, yabancı hook'lara dokunulmaz, tekrar çalıştırılabilir, geçersiz JSON'da durur
- [x] PowerShell ikizi (`pixel_hook.ps1`): aynı eşleme, aynı durum dosyası, aynı izin listesi, `/` ve `\` ile yol ayırma
- [x] Tek satırlık kurulum betikleri (`install.sh`: python3 yoksa pwsh'ye düş; `install.ps1`: Python varsa onu, yoksa PowerShell istemcisini kur)
- [x] Genel API örnekleri (curl, Python, PowerShell, JS) ve `pixel_hook.py status` CLI'ı
- [x] Opt-in HTTP kancası parçacığı ve sunucu tarafında aynı `map_event` kullanımı
- [x] **`web/hook/pixel_agent.py` kütüphanesi (CLOUD.md §17.1–§17.2):** `Agent.report()`, `working()` bağlam yöneticisi, `task()` dekoratörü, `waiting()`, `done()`, `offline()` ve atexit. Bloklamayan bir gönderici iş parçacığı kullanır: yerelde 0,3 sn debounce yapar; bulutta durum grubu değişikliklerini hemen, grup içi değişiklikleri en fazla 30 sn'de bir gönderir. Yapılandırma sırası: argümanlar → ortam değişkenleri → `config.json` → `http://127.0.0.1:8765`. `report` CLI'ı ve `install-shims` alt komutu da bu dosyadadır.
- [x] **`pixel-run` sarmalayıcısı (§17.3):** POSIX'te pty kullanır, böylece interaktif TUI'ler çalışmaya devam eder. Çıktı geldikçe `working`, `--idle-after` saniye çıktı gelmezse `idle` olur. Çıkışta `sleeping` + `final` ya da `offline` gönderir. Sinyalleri alt sürece iletir ve onun çıkış kodunu döndürür. Windows'ta (pty yok) süreç boyunca `working` gösterir; `--pipe` seçeneği vardır.
- [x] **`pixel-proxy` (§17.4):** stdlib ile yazılmış, bayt düzeyinde aynen ileten bir ara sunucu. SSE ve NDJSON akışlarını parça parça ve değiştirmeden aktarır. Ajan kimliği `X-Pixel-Agent` başlığından, `/agent/<ad>/` yol önekinden ya da `--name` değerinden gelir. İstek sürerken `thinking`, yanıt `tool_calls` içeriyorsa `working` (araç adı), içermiyorsa `idle` gösterir. Model adı varsayılan olarak gönderilmez.
- [x] JS `fetch` ve `curl` örnekleri (yerel ve bulut; tek fark taban URL ve anahtardır) (§17.5)

**Kabul kriterleri:** En az 40 test vektörü geçer. **Gizlilik fuzz testi:** izin listesi dışındaki her alana gömülen kanarya metni hiçbir giden istekte görünmez. Yükleyici gerçek `~/.claude`'a dokunmadan geçici dizinlerde test edilir. Bu makinedeki gerçek bir Claude Code oturumu dev sunucuda çalışıyor → bekliyor → kahve → uyku → çıkış döngüsünü gösterir. Tipik bir tur en fazla 4 yazma üretir. PowerShell istemcisi vektörlerle `pwsh` üzerinde ya da bir Windows bilgisayarında denenmiş, sonuç günlüğe yazılmıştır. İstemci her durumda çıkış kodu 0 ile biter ve Claude Code'u hiçbir zaman bloklamaz. Aynı `pixel_agent.py` kodu yalnızca taban URL değiştirilerek hem yerel sunucuya hem buluta raporlar. `pixel-run -- aider` ile aider normal çalışır ve ofiste çalışıyor/kahve/uyku döngüsü görünür. `pixel-proxy` üzerinden Ollama'ya giden akışlı bir istek, doğrudan istekle bayt bayt aynıdır. Bu istemciler de yalnızca durum, araç adı ve etiket gönderir. Gizlilik kanarya testi bunları da kapsar. ✔️ (gerçek Claude Code oturumu, `pwsh`/Windows, gerçek aider ve Ollama denemeleri Aşama 10'a kaldı; bkz. günlük)

### Aşama 9 — Bulut Arayüzü & PWA (Frontend Mühendisi)
Teslimat: `web/js/transport.js`, `cloud.js`, `pip.js`, `main.js`/`agents.js`/`renderer.js`/`ui.js`/`index.html`/`style.css` düzenlemeleri, `web/manifest.webmanifest`, `web/sw.js`, `web/icons/*`, `tools/make_icons.py` (CLOUD.md §9–§11).
- [x] Arka uç tespiti (`/api/config`). Yerel modda SSE davranışı birebir korunur.
- [x] `SseTransport` (mevcut kod taşınır) ve `PollingTransport` (sunucunun bildirdiği `pollMs`, jitter, geri çekilme, gizliyken duraklama)
- [x] Giriş ekranı: oda oluştur / bağlantıyla katıl / son odalar. Anahtar `#` kısmından okunup adres çubuğundan silinir.
- [x] Oda araç çubuğu: Davet et (katılma bağlantısı ve izleme bağlantısı), Ajan bağla (sekmeler: macOS/Linux · Windows · Genel API · Gelişmiş), Mini pencere, Uygulamayı yükle
- [x] Sahip rengi gömlek, `sahip·proje` etiketi, listede sahibe göre gruplama, ayrıntılar paneli
- [x] Mini pencere: Document PiP (canvas taşınır, rAF ve zamanlayıcılar PiP penceresine geçer), konumlu açılır pencere yedeği, `?view=mini` kompakt düzeni
- [x] **Yeniden adlandırma arayüzü (CLOUD.md §15.1):** ayrıntılar panelinde ✎ ile satır içi düzenleme (Enter/blur kaydeder, Esc iptal eder). Mod'a göre "yeni oturumlara da ver" kutusu (bulutta sahip, yerelde proje kapsamı). "Varsayılana dön" bağlantısı ve iyimser güncelleme. Yalnızca izleme bağlantısıyla açılan bulut odasında ✎ gizlenir. Yerel modda ✎ yalnızca `/api/health` yanıtında `features` içinde `rename` varsa gösterilir.
- [x] PWA: manifest, servis çalışanı (yalnızca bulut modunda; `/api/*` önbelleğe alınmaz), stdlib ile üretilen ikonlar

**Kabul kriterleri:** `python3 server.py --demo` eskisi gibi çalışır, görsel ve davranışsal fark yoktur. `python3 dev_server.py --demo` ile oda oluşturulup katılınır, 3 sahte arkadaşın ajanları doğru renk ve etiketlerle görünür, durum değişiklikleri bir polling turu içinde yansır, `offline` olan karakter kapıdan çıkar. Gizli sekmede istek gitmez. Chrome'da mini pencere açılır, ana pencere küçültülünce animasyon ve polling sürer, pencere kapanınca canvas yerine döner. Chrome "yüklenebilir" der. Konsolda hata yoktur. Kötü niyetli sahip/proje/etiket metinleri (XSS) düz metin olarak görünür. Bir ajanın adı değiştirildiğinde odadaki diğer tarayıcılar bunu bir polling turu içinde görür. Yerelde ad değişikliği SSE ile tüm açık sekmelere anında yansır. Varsayılana dönüş çalışır. ✔️ (gerçek masaüstü Chrome'da elle PiP sürükleme ve gerçek küçültme Aşama 10'a kaldı; bkz. günlük)

### Aşama 10 — QA & Yayına Hazırlık (QA Mühendisi)
- [x] Tüm kodun gözden geçirilmesi (sözleşme uyumu, gizlilik, güvenlik başlıkları, hata yolları). Bulunan hatalar düzeltildi, bkz. günlük.
- [x] **Yeniden adlandırma testleri** (`tests/test_rename.py`): öncelik sırası, sıfırlama, temizleme ve uzunluk sınırı, durum güncellemesinin adı ezmemesi, yerel dosyanın kalıcılığı ve atomik yazımı (başarısız yazımda eski dosya korunur), SSE `agent_updated`, bulutta 96 alan limiti ve eski adların budanması
- [x] **Yerel durum API'si testleri** (`tests/test_local_status.py`): bulutla şema ve yanıt eşliği, CSRF koruması, loopback dışında anahtar zorunluluğu, derive zamanlayıcısı, `final` kuralı
- [x] **`pixel_agent` testleri** (`tests/test_pixel_agent.py`): gönderici politikası, bağlam yöneticisi ve dekoratör, `pixel-run` (pty/pipe, sinyal, çıkış kodu), `pixel-proxy` (bayt bayt aynı akış, `tool_calls`, **istemci kopması**, hop-by-hop başlıklar), kanarya gizlilik testi
- [x] **Dağıtım testleri** (`tests/test_release.py`): Vercel'in fonksiyon algılayıcısının birebir kopyasıyla her `api/*.py`, `vercel.json`, `.vercelignore`, `.gitignore`, `LICENSE`, kişisel yol taraması, servis çalışanı, symlink'li `settings.json`
- [x] Uçtan uca testler: dev sunucuda headless Chrome (CDP); hook istemcisi, `pixel-run` ve `pixel-proxy` ile ajanlar; gövdelerde ve günlükte istem sızmadığının doğrulanması. Gerçek Claude Code CLI bu makinede yok → bkz. elle yapılacaklar.
- [x] Vercel yapılandırmasının doğrulanması (Vercel'in kaynak kodu ve belgelerine karşı): `api/*.py` algılama hatası bulunup düzeltildi; `outputDirectory: "web"` + `api/` birlikte çalışır, rewrites yedeğine gerek yok
- [x] İlk ajanın hızlı görünmesi (Aşama 9'dan kalan UX sorunu): boş odada ve "Ajan bağla" açıkken 5 sn'lik yoklama, boş oda CDN TTL'i 5 sn; bütçe CLOUD.md §12'de güncellendi
- [x] `README.md` (Türkçe + İngilizce özet): yerel mod, bulut kurulumu adım adım (git → GitHub → Vercel → Upstash → ortam değişkenleri → test), arkadaş kurulumu, PWA ve mini pencere, ajan bağlama, yeniden adlandırma, gizlilik ve güvenlik uyarıları, ücretsiz plan limitleri, sorun giderme, `docs/screenshot-cloud.png`
- [x] `.gitignore`, `LICENSE` (MIT), `.vercelignore` güncellemesi; belgelerden kişisel yollar temizlendi; depoda sır yok
- [x] `sw.js` sürümünü artırma maddesi sürüm kontrol listesine (README "Adım 5" ve sorun giderme) eklendi; `VERSION` → `po-v2.0.1`
- [ ] → kullanıcıya: ilk Vercel dağıtımı ve doğrulaması, gerçek Claude Code / Windows / Ollama / aider denemeleri, ilk haftadan sonra bütçe kontrolü (aşağıdaki liste)

**Kabul kriterleri:** Tüm testler geçer (v1 + bulut): 251 test ✔️. README'deki komutlar yerelde denendi ✔️. Yerel mod bozulmadı ✔️. Yeniden adlandırma iki modda da tarayıcıda denendi ✔️. Gerçek Vercel dağıtımı, gerçek bir arkadaş (tercihen Python'suz Windows), gerçek Ollama/aider ve gerçek Claude Code CLI denemeleri bu makinede yapılamadı; aşağıdaki listede kullanıcıya bırakıldı.

**Kullanıcının elle yapması gerekenler** (ayrıntılı rehber: [`README.md`](README.md) → "Adım adım dağıtım"):

- [ ] `sudo apt install git` ve `git config --global user.name/user.email` (ya da GitHub Desktop / web arayüzünden yükleme)
- [ ] **Kişisel** GitHub hesabında yeni bir depo oluşturup projeyi `git push` ile yüklemek (`.gitignore` hazır; `names.json` depoya girmez)
- [ ] Vercel'de (Hobby) **Add New → Project → Import**; ayarlara dokunmadan **Deploy**
- [ ] Vercel → Storage → **Upstash for Redis** (Free, Frankfurt) → projeye bağlamak
- [ ] `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` ile `PIXEL_SECRET` üretip Vercel'e girmek (**bir kez belirlenir, sonra değiştirilmez**); önerilen `PIXEL_CREATE_TOKEN`, isteğe bağlı `PIXEL_PUBLIC_URL`
- [ ] **Redeploy**, sonra doğrulama: `/api/health` → `"storage":"upstash","storageOk":true`; `/README.md` ve `/server.py` → 404; `/hook/pixel_hook.py` → `text/plain`; `curl -sI ".../api/state?room=<oda>"` iki kez → ikincisinde `x-vercel-cache: HIT`
- [ ] Oda oluşturup kendi bilgisayarında "Ajan bağla" komutunu çalıştırmak ve Claude Code'u yeniden başlatmak (gerçek Claude Code ile ilk deneme)
- [ ] En az bir arkadaşın (tercihen Python'suz Windows) tek komutla bağlanması; `pixel_hook.ps1` ilk kez orada çalışacak, `doctor` çıktısına bakılmalı
- [ ] İsteğe bağlı: gerçek Ollama/LM Studio ile `pixel-proxy`, gerçek aider ile `pixel-run`; Chrome'da mini pencereyi sağ alta sürükleyip ana pencereyi küçültmek
- [ ] İlk ay haftada bir Vercel **Usage** ve Upstash **Usage** kontrolü; gerekirse `PIXEL_POLL_ACTIVE_MS` / `PIXEL_POLL_CALM_MS`
- [ ] Sonraki sürümlerde `web/sw.js` içindeki `VERSION` değerini artırmak

## 6. İlerleme Günlüğü

Her ajan işini bitirince buraya tarihli bir kayıt ekler (en yeni en altta).

- **2026-10-11 — Aşama 1 (Yazılım Mimarı):** Gerçek transcript'ler incelendi (satır tipleri: `user`, `assistant`, `attachment`, `queue-operation`, `last-prompt`, `ai-title`, `cost-state`, `file-history-snapshot`…; assistant içerik blokları her biri ayrı satırda yazılıyor; alt-ajanlar `<oturum>/subagents/agent-<id>.jsonl` + `.meta.json`). Durum modeli, protokol, 22×14 ofis haritası (9 masa, 3 kahve noktası, 4 yatak), sprite API'si ve tüm modül arayüzleri `docs/ARCHITECTURE.md`'de tanımlandı. Aşama 1 tamamlandı.
- **2026-10-11 — Aşama 2 (Pixel Art Uzmanı):** `web/js/sprites.js` yazıldı: §5.5'teki API birebir (`TILE`, `CHAR_W`, `CHAR_H`, `ANIMS`, `makeCharacterPalette`, `getCharacterFrame`, `getTileSprite`, `getFurnitureSprite`, `getBubbleSprite`, `getToolIcon`, `validateSprites`). 16×24 karakterler parça (kafa/gövde/bacak) string haritalarından birleştiriliyor: yürüme 4 kare (gövde zıplaması + kol/bacak salınımı), oturma/yazma (arkadan, sandalye sırtlığı karede), kahve içme (kupa göğüste/ağızda, gözler kapalı), uyuma (yorgan 1 px nefes). Tüm zemin/duvar/mobilya/balon/araç ikonları; canvas'lar tembel üretilip önbellekleniyor. `validateSprites()` gjs ile çalıştırıldı (0 hata; bilerek bozulan kopyada genişlik ve bilinmeyen renk hataları yakalandı). Headless Chrome ile `web/sprite-preview.html` (tüm kareler + canlı animasyonlar + örnek ofis sahnesi) ekran görüntüsü alınıp görsel olarak kontrol edildi. Spesifikasyona yalnızca **eklemeler** yapıldı (ARCHITECTURE.md §5.6).
- **2026-10-11 — Aşama 3 (Backend Mühendisi):** `server.py` ve `pixel_office/` paketi (`config`, `models`, `store`, `transcript`, `watcher`, `simulator`, `httpd`) yazıldı; yalnızca stdlib. `FileTail` yarım satırlara, kesme/yer değiştirmeye (inode), dev satırlara ve bozuk JSON'a dayanıklı; ilk okumada sadece son 256 KB okunuyor (50 MB dosyada ilk tarama ≈ 0,01 sn). `SessionTracker` §2.3 kurallarını birebir uyguluyor. Gerçek veride görülen bir sorun çözüldü: alt-ajan transcript'lerinde son rapor/`end_turn` satırı **yok**; bu yüzden ana oturumdaki, `meta.json.toolUseId`'ye ait `tool_result` alt-ajanın bittiğini (→ `sleeping`) gösteriyor. SSE: bağlanınca atomik snapshot, `agent_added/updated/removed`, 15 sn `: ping`, kopan istemcilerde sessiz temizlik; Ctrl+C/SIGTERM ile temiz kapanış. Demo: 5 ajan, kademeli giriş, tüm durumlar, `Agent` aracıyla alt-ajan, uyuyup çıkan ve yerine yenisi gelen ajanlar. `tests/` altında 56 unittest (durum sezgileri, FileTail, Watcher.scan, store olayları, sahte saatle simülatör, HTTP/SSE) — hepsi geçiyor. Canlı doğrulama: demo modunda `/api/agents`, `/api/health`, `/js/sprites.js` (`text/javascript`), `/events` akışı; gerçek modda bu oturum (`-home-user-Desktop-Pixel`) `working`/`Agent`, Aşama 3 alt-ajanı `working`/`Bash` olarak göründü, biten alt-ajanlar gizlendi. Sapmalar/eklemeler: ARCHITECTURE.md §7.8.
- **2026-10-11 — Aşama 4 (Frontend/Oyun Mühendisi):** `web/index.html`, `web/css/style.css`, `web/js/office.js`, `pathfinding.js`, `agents.js`, `renderer.js`, `ui.js`, `main.js` yazıldı (vanilla ES modülleri, bağımlılık/derleme yok). Harita ayrıştırma + spot/mobilya listeleri, spot rezervasyonu (iki karakter asla aynı spotu paylaşmaz; taşma spotları ve son çare rastgele boş zemin), binary-heap A* (sandalye/kahve/yatak karoları yalnızca başlangıç/hedef), 40 px/sn yürüyüş, 2 sn asgari bekleme, kapıdan giriş/çıkış (0,4 sn solma), alt-ajanlara ana ajanın gömlek rengi. Renderer: önbellekli arka plan, y-sıralı mobilya+karakter, dolu masada `desk_on`, sallanan balonlar (araç ikonu, "...", yanıp sönen "!", kahve fincanı + buhar, Zzz), net isim etiketleri (cihaz pikselinde çizilir), pencereye sığan ve mümkünse tamsayı ölçekli tuval. Arayüz Türkçe: üst çubukta başlık + CANLI/DEMO + bağlantı durumu, tuval üstünde "Yeniden bağlanıyor…" şeridi, durum sayaçları, ajan listesi (renk, ad, proje, durum rozeti, araç, geçen süre; alt-ajanlar girintili), seçilince ayrıntılar (başlık, son istek/mesaj, model, araç sayısı, ana/alt ajan bağlantıları), boş ofis mesajı, dar ekranda alt alta düzen. SSE: 1→2→4→8→10 sn geri çekilmeli yeniden bağlanma. Doğrulama: headless Chrome'u CDP ile süren küçük bir stdlib Python betiğiyle (sanal zaman SSE ile takıldığı için) demo modunda 3–120. saniyeler arası ekran görüntüleri alındı: karakterler kapıdan girip yürüdü, masalarda yazdı, kahve makinesinde içti, yataklarda uyudu, silinen ajan kapıdan çıktı; tıklama ile seçim + ayrıntı paneli çalıştı; sunucu öldürülünce "Yeniden bağlanıyor…", yeniden başlatılınca canlıya dönüş; canlı modda bu oturum ve alt-ajanı göründü; boş dizinle boş-ofis mesajı; ≈500 px genişlikte alt alta düzen. Tarayıcı içinde 30 ajanlı 120 sn'lik stres testi: paylaşılan spot 0, duvara girme 0, ulaşılamayan spot 0. Konsolda JS hatası yok (yalnızca sunucu kapalıyken beklenen `ERR_CONNECTION_REFUSED`). Backend ve `sprites.js` değiştirilmedi. Sapmalar/eklemeler: ARCHITECTURE.md §6.7.
- **2026-10-11 — Aşama 5 (QA Mühendisi):** Tüm kod gözden geçirildi: JS import/export eşleşmeleri, backend JSON ↔ arayüz sözleşmesi, SSE/store yarışları, abonelik ve karakter temizliği, A* uç durumları, XSS ve statik dosya güvenliği. Bulunan sorunlar ve düzeltmeleri:
  1. Demo ajanlarının isimleri çakışıyordu. Örneğin `demo-3` ve `demo-8` ikisi de "Ada" oluyordu. Simülatör artık canlı ajanlar arasında benzersiz bir isim seçiyor (test eklendi).
  2. Kapıda solarken geri dönen (yeniden eklenen) bir karakter kalıcı olarak yarı saydam kalıyordu. Artık yürürken yeniden beliriyor.
  3. Taşma (yedek) noktasındaki karakterler, normal bir masa, kahve noktası ya da yatak boşalsa bile yerlerinde kalıyordu. Artık boşalan normal noktaya yürüyorlar. Rastgele zemin noktasındakiler de boşalan yedek noktaya geçiyor.
  4. Watcher'ın `_removed` belleği silinen dosyalar için sınırsız büyüyordu. Artık her keşifte budanıyor (test eklendi).
  5. `ui.js` sağlamlaştırıldı. Güvenilmeyen tüm metinler zaten `esc()` ile kaçırılıyordu. Buna ek olarak sayısal alanlar sayıya zorlanıyor, durum etiket ve renkleri `Object.hasOwn` ile okunuyor.
  6. `sprite-preview.html` için eksik favicon eklendi (404 hatası veriyordu).

  Aşama 4'ten kalan istekler de karşılandı. Durum etiketleri artık "Çalışıyor / Düşünüyor / Kullanıcıyı bekliyor / Kahve molasında / Uyuyor". Üst çubukta eskiden kafa karıştıran "CANLI" + "Canlı" ikilisi vardı, yerine "CANLI MOD"/"DEMO MOD" rozeti ve "Bağlı"/"Yeniden bağlanıyor…" bağlantı durumu geldi. Spesifikasyon güncellendi (ARCHITECTURE.md §6.5, §6.7, yeni §7.9).

  Testler: 58 unittest geçiyor (önceden 56). Uçtan uca testler, stdlib ile yazılmış bir CDP betiğiyle sürülen headless Chrome'da yapıldı:
  - Demo modunda yaklaşık 3 dk boyunca 100 ms aralıkla değişmez kontrolü yapıldı. Duvara girme 0, paylaşılan nokta 0, kalıcı saydamlık 0, konsol hatası 0. Karakterlerin kapıdan girdiği, masada yazdığı, oturup düşündüğü, kahve içtiği, uyuduğu ve kapıdan çıktığı görüldü. Araç ikonu, "...", "!", kahve ve Zzz balonlarının hepsi göründü. Tuvale ve listeye tıklayarak seçim, ayrıntı paneli ve Esc ile kapatma çalıştı.
  - Tarayıcıda çalıştırılan bir mantık testi taşma noktasından normal noktaya geçişi ve geri dönüşte yeniden belirmeyi doğruladı.
  - Kötü niyetli başlık, istem, mesaj, araç adı ve cwd içeren sahte bir transcript ile XSS denendi. Hiçbir şey çalışmadı, metinler düz yazı olarak göründü.
  - Canlı modda (`~/.claude/projects`, salt-okunur) bu oturum ve QA alt-ajanı göründü. Sunucu öldürülünce "Yeniden bağlanıyor…" çıktı, yeniden başlatılınca "Bağlı" durumuna geri dönüldü.
  - Sprite önizleme sayfasında `validateSprites()` hata vermedi.

  Gerekli en düşük Python sürümü belirlendi: 3.11 (`StrEnum`, `fromisoformat` 'Z'). `README.md` yazıldı ve `docs/screenshot.png` eklendi.

  Bilinen sınırlamalar: durumlar sezgiseldir, örneğin model yanıtı olmayan yerel `/komutlar` "Düşünüyor" görünebilir. Canlı modda isimler hash'ten türetildiği için çakışabilir. Python 3.11–3.13 bu makinede bulunmadığından denenemedi. **Proje tamamlandı.**

- **2026-10-11 — Aşama 6 (Bulut/Yazılım Mimarı):** v2 "Bulut Ofis" tasarlandı ve `docs/CLOUD.md` yazıldı. Kod yazılmadı, yerel mod etkilenmedi.

  Araştırma (2026-10-11, kaynaklar CLOUD.md §18'de):
  - **Vercel:** `api/*.py` dosyalarında `BaseHTTPRequestHandler`'dan türeyen bir `handler` sınıfı kullanılıyor. Varsayılan Python 3.12, 3.13 ve 3.14 da destekleniyor.
  - **Vercel Hobby limitleri:** ayda 1M çağrı, 1M CDN isteği, 4 saat Active CPU. Limit aşılırsa hesap duraklatılabilir.
  - **Vercel CDN:** `s-maxage` başlığı taşıyan fonksiyon yanıtlarını önbelleğe alıyor.
  - **Upstash Redis:** ücretsiz planda ayda 500 bin komut var. Pipeline içindeki her komut ayrı sayılıyor.
  - **Supabase:** 7 gün etkinlik olmayan projeleri duraklatıyor.
  - **Claude Code hook'ları:** `async: true` ile arka planda çalışabiliyor, `args` ile kabuksuz (exec) biçimde çağrılabiliyor. `type: "http"` türü hook'lar var ama senkron çalışıyorlar. `SessionEnd` için varsayılan zaman aşımı 1,5 sn. Windows'ta yollar `\` ile geliyor.
  - **Document PiP:** Chrome/Edge 116+ ve Firefox 151+ destekliyor, Safari desteklemiyor. Site pencerenin konumunu belirleyemiyor.

  Kararlar:
  - **Depolama ve güncelleme:** Upstash Redis seçildi, her oda tek bir hash. Uyarlamalı polling (10/30/60 sn) ve CDN önbelleği (5/15/30 sn) kullanılacak.
  - **Erişim:** Oda kimliği izleme yetkisi verir. Yazma anahtarı durumsuz bir HMAC'tir ve yalnızca `#` sonrasında taşınır.
  - **Durum eşleme:** Hook olayları durumlara istemcide eşlenir. PostToolUse yalnızca bekleme durumundan çıkışta gönderilir. Yazmalar 1 sn debounce ve 30 sn araç kısıtlamasıyla birleştirilir.
  - **Zamana bağlı geçişler:** Sunucu bunları okuma anında hesaplar (kahve → 3 dk sonra uyku, 30 dk sessiz aktif ajan → uyku, 45 dk sonra çıkış).
  - **Gizlilik:** Gönderilecek alanlar izin listesiyle belirlenir. Python'suz Windows için aynı gizliliği sağlayan bir PowerShell istemcisi var. Ham veri gönderen HTTP kancası yalnızca açık onayla kullanılabilir ve bu ödünleşim belgede dürüstçe yazıldı.

  Bütçe (10 arkadaş × günde 8 saat): 30 günlük zorlama senaryosunda CDN istekleri ≈ 757 bin/ay (%76, bağlayıcı limit), fonksiyon çağrıları ≈ 300 bin (%30), Upstash ≈ 318 bin komut (%64), Active CPU ≈ 1,25 saat (%31). 22 iş günüyle CDN istekleri %56'ya iniyor.

  Belirsiz kalan ve Aşama 10'da doğrulanacak noktalar:
  - `outputDirectory: "web"` ile `api/` fonksiyonları birlikte algılanıyor mu (yedek plan: rewrites)
  - Upstash ücretsiz planı limit aşılınca nasıl davranıyor
  - Marketplace'in tam ortam değişkeni adları (kod iki adlandırmayı da kabul ediyor)

  Aşama 6 tamamlandı. Sıradaki adım: Aşama 7 ve 8 (paralel).

- **2026-10-11 — Aşama 6 eki (Bulut/Yazılım Mimarı):** Kullanıcının iki yeni isteği tasarıma eklendi (CLOUD.md §15–§17). Kaynaklar bölümü §18 oldu. Kod yazılmadı.

  1. **Ajanları arayüzden yeniden adlandırma (yerel ve bulut).**
     - **Bulut:** Adlar oda hash'inde ayrı alanlarda tutulur (`n:<agentId>`, `no:<ownerId>`). Hook istemcisi hiç ad göndermez, durum yazmaları da yalnızca ajanın kendi alanına yapılır. Bu yüzden durum güncellemeleri bir adı yapısal olarak ezemez. Oturum kimliği her Claude oturumunda değiştiği için iki kapsam var: `agent` (yalnızca bu ajan) ve `owner` (arkadaşın sonraki oturumlarına varsayılan). Uç `POST /api/rename` olup yazma anahtarı gerektirir. Ad 1–24 karakterdir ve temizlenir. Maliyeti 1 Redis komutudur, okumaya ek maliyet yoktur.
     - **Yerel:** `POST /api/agents/rename` ucu eklendi. Kapsamlar `agent` ve `project`. Adlar kullanıcı veri dizinindeki `~/.local/share/pixel-office/names.json` dosyasında tutulur; dosya git dışındadır ve atomik yazılır. Ad değişince SSE ile `agent_updated` yayınlanır. Kökteki eski `names.json` çalışmaya devam eder.
     - **Öncelik sırası** (tek kural, iki modda da): özel ad → proje/sahip varsayılanı → (yerel) `names.json` → istemcinin bildirdiği ad → otomatik ad.

  2. **Yerel yapay zeka ajanları (Ollama, LM Studio, llama.cpp, aider, Open Interpreter, özel betikler).**
     - **Ortak API:** Yerel sunucuya bulutla aynı şemayı kullanan `POST /api/status` ucu eklendi. Doğrulama ve zaman kuralları ortak koddan gelir. Loopback'te anahtar istenmez. CSRF'e karşı `Origin` ve `Content-Type` denetlenir.
     - **Kütüphane:** `pixel_agent.py`, bağlam yöneticisi ve dekoratör sunar. Bulutta yazmaları birleştirir (tur başına ≈ 2 yazma + en fazla 30 sn'de bir).
     - **`pixel-run`:** POSIX'te pty ile çalışır, böylece interaktif programlar bozulmaz. Çıktı varken `working`, sessizlikte `idle`, çıkışta önce uyku sonra çıkış gösterir. Windows'ta pty olmadığı için boşta algılaması yapılamıyor; bu sınırlama belgede açıkça yazıldı.
     - **`pixel-proxy`:** Akışı bayt bayt aynen aktaran, OpenAI/Ollama uyumlu bir ara sunucu. İstek sürerken `thinking`, yanıtta `tool_calls` varsa `working`, yoksa `idle` gösterir. Model adı varsayılan olarak gönderilmez.
     - **Yeni `final` alanı:** `sleeping` durumundaki ajan 300 sn sonra ofisten çıkar.

  **Bütçe:** Günde 1 saat yerel ajan kullanımıyla 30 günlük zorlama senaryosu CDN istekleri için ≈ 796 bin/ay (%80), Upstash için ≈ 361 bin (%72) oluyor; ücretsiz planlar hâlâ yetiyor.

  **Aşama dağılımı:** yeniden adlandırma backend'i ve yerel durum API'si → Aşama 7; istemciler, sarmalayıcı ve proxy → Aşama 8; yeniden adlandırma arayüzü → Aşama 9; tüm testler → Aşama 10.

- **2026-10-11 — Aşama 7 (Backend Mühendisi):** Bulut backend'i ve yerel sunucu eklemeleri yazıldı. Yalnızca stdlib kullanıldı.

  Yeni dosyalar:
  - `pixel_cloud/` paketi: `settings`, `ids`, `validate`, `derive`, `storage`, `ratelimit`, `app`, `vercel`, `demo`.
  - 7 ince `api/*.py` dosyası: `config`, `health`, `rooms`, `state`, `status`, `cc_hook`, `rename`.
  - `dev_server.py`, `vercel.json`, `.vercelignore`, `.python-version`.
  - Yerel taraf: `pixel_office/names_store.py` ve `pixel_office/reported.py`.
  - `httpd.py`, `models.py`, `store.py` ve `server.py`'ye yalnızca ekleme yapıldı (`--names-file`, `--write-key`).

  Uçlar:
  - **Bulut:** `GET /api/config`, `GET /api/health`, `POST /api/rooms`, `GET /api/state` (etkinliğe göre `s-maxage` 5/15/30), `POST /api/status` (OPTIONS ile CORS), `POST /api/rename`, `POST /api/cc_hook`.
  - **Yerel:** `POST /api/status`, `POST /api/agents/rename` (SSE ile `agent_updated` yayınlanır). `/api/health` yanıtına `features` eklendi.
  - Yerel uçlar bulutla aynı doğrulama ve zaman kurallarını kullanır (ortak `pixel_cloud.validate` ve `derive`).

  Güvenlik:
  - Yazma anahtarı HMAC'tir ve sabit zamanlı karşılaştırılır.
  - Gövde boyutu, `read_body` ile okumadan önce `Content-Length` üzerinden denetlenir (4 KB, 1 KB ve 256 KB).
  - Tüm metinler temizlenir (NFC, kontrol/bidi/sıfır genişlikli karakterler).
  - Süreç içi token bucket'lar: oda 2/sn (30 patlama), IP 4/sn (60 patlama), oda oluşturma 5/saat, ad değişikliği 10/dk.
  - Gövdeler, anahtarlar ve `Authorization` başlığı hiçbir zaman loglanmaz.
  - Yerel sunucuda CSRF koruması için `Origin` ve JSON içerik türü denetlenir.

  Testler: Toplam 166 test geçiyor. Bunların 59'u v1 testidir ve hiç değiştirilmedi. 107 yeni test şu dosyalarda:
  - `test_cloud_ids`, `test_cloud_validate`, `test_cloud_derive` (§6.2'nin her satırı ve sınırları, sahte saatle)
  - `test_cloud_storage` (MemoryStorage ve sahte Upstash REST sunucusu: pipeline, yeniden bağlanma, hatalar)
  - `test_cloud_app` (tüm uçlar ve hata kodları, CORS, önbellek başlıkları, hız limitleri, oda doluluğu, gerçek soket üzerinden adaptör ve chunked gövde, loglarda sır olmadığı)
  - `test_cloud_devserver`, `test_rename`, `test_local_status` (bulutla eşlik, CSRF, anahtar, ticker, SSE)

  Canlı doğrulama:
  - `dev_server.py --demo` 8791 portunda çalıştırıldı ve curl ile denendi. Oda oluşturma 201, yazma 200, anahtarsız ya da yanlış anahtarlı yazma 401, 4 KB üstü gövde 413, hatalı oturum kimliği 400, geçersiz oda 404 döndü. OPTIONS 204 döndü ve CORS başlıkları doğruydu. Ad değişikliği sonraki durum güncellemesinden sonra da korundu. `s-maxage` aktif odada 5, sakin odada 15, boş odada 30 oldu. Demo odasında 3 sahip göründü. Güvenlik başlıkları geldi ve log'da anahtar yoktu.
  - `server.py --demo` 8792 portunda, geçici bir `--names-file` ile çalıştırıldı. Yerel `/api/status` 200, kötü `Origin` 403, yanlış içerik türü 400, büyük gövde 413 döndü. Proje ve ajan kapsamlı ad değişiklikleri SSE'de `agent_updated` olarak yayınlandı. Ad dosyası 0600 izniyle yazıldı. `~/.local/share/pixel-office` oluşmadı. 8765'teki sunucuya dokunulmadı.

  Sapmalar CLOUD.md §17.7'de yazılı. Başlıcaları:
  - v1 testleri değişmesin diye yeni uçlar ve `features` alanı yalnızca `make_server`'a eklentiler verildiğinde açılır (`server.py` hep verir). `nameSource` ve `defaultName` alanları `to_dict(names=True)` ile eklenir.
  - `agentType` serbest metin olarak kabul edilir ("Özel ajan" için).
  - Ajan ve ad limitleri `HLEN` yerine `HKEYS` ile sayılır.
  - `PIXEL_PUBLIC_URL` tanımlıysa önceliklidir.
  - Yeni hata kodları: `internal_error` ve `hook_mapper_unavailable`. `cc_hook`, Aşama 8'in `map_event` işlevini bekler; `cfg` sözleşmesi §17.7'de tanımlandı.

  Aşama 7 tamamlandı. Sıradaki adım: Aşama 8 (ve ardından 9).

- **2026-10-11 — Aşama 8 (Entegrasyon Mühendisi):** Ajan entegrasyonu yazıldı. Yalnızca stdlib kullanıldı ve istemci dosyaları Python 3.8 ile uyumlu (sözdizimi `ast` ile 3.8'e göre denetlendi).

  Yeni dosyalar:
  - `web/hook/pixel_hook.py`: Claude Code kancası. Alt komutlar: `hook`, `install`, `uninstall`, `test`, `status`, `doctor`.
  - `web/hook/pixel_hook.ps1`: PowerShell 5.1 ikizi. Aynı eşleme tablosunu ve aynı durum dosyalarını kullanır. Dosya bilerek salt ASCII.
  - `web/hook/install.sh` ve `web/hook/install.ps1`: tek satırlık kurulum betikleri.
  - `web/hook/pixel_agent.py`: kütüphane, `report`, `run` (`pixel-run`), `proxy` (`pixel-proxy`) ve `install-shims`.
  - `docs/INTEGRATIONS.md`: Türkçe kullanım rehberi. curl/JS/Python/PowerShell örnekleri ile Ollama, LM Studio, llama.cpp, aider ve Open Interpreter kurulum satırlarını içerir.
  - Testler: `tests/test_hook_client.py`, `tests/test_pixel_agent.py` ve 61 vektörlük `tests/hook_vectors.json`.

  Öne çıkanlar:
  - `map_event`, §7.2 tablosunu uygular. Sunucudaki `/api/cc_hook` artık bu işlevi yüklüyor ve 503 yerine 200 dönüyor.
  - Yazma birleştirme: 1 sn debounce ve `seq` kontrolü, 30/60 sn araç kısıtlaması, 5 dk keep-alive. Tipik bir tur en fazla 4 yazma üretiyor (test edildi).
  - `settings.json` güvenli birleştiriliyor: tek seferlik yedek alınıyor, yabancı kancalar korunuyor, işlem tekrarlanabiliyor, geçersiz JSON'da hiçbir şey yazılmıyor, `uninstall` yalnızca bizim kancaları kaldırıyor.
  - `pixel-run` pty ile çalışıyor, sinyalleri alt sürece iletiyor ve çıkış kodunu aynen döndürüyor.
  - `pixel-proxy` SSE ve NDJSON akışlarını bayt bayt aynı ve geldikçe aktarıyor. Akış hâlindeki `tool_calls` parçalarını da algılıyor.

  Testler: Toplam 230 test geçiyor (önceden 166). Yeni testler 64 tane; 1'i `pwsh` olmadığı için atlandı. Kapsam:
  - 61 eşleme vektörü
  - kanarya gizlilik testi: her olay türü, iç içe `tool_input`, `cwd`'nin üst klasörleri, ham kimlikler; giden istekler, durum dosyaları ve günlük kontrol ediliyor; gerçek HTTP üzerinden uçtan uca da deneniyor
  - sahte saatle debounce, birleştirme ve kısıtlama
  - geçici dosyalarla yükleyici (gerçek `~/.claude`'a hiç dokunulmadı)
  - `pixel_agent` gönderici politikası ve toplu gönderim
  - yerel ve bulut eşliği
  - `pixel-run`: pipe ve pty modları, sinyal iletimi, çıkış kodu
  - `pixel-proxy`: sahte bir upstream'e karşı JSON, OpenAI SSE ve Ollama NDJSON, hop-by-hop başlık temizliği, upstream'e ulaşılamaması

  Canlı doğrulama (8811 portunda `dev_server.py`, 8812'de `server.py --demo` ve geçici `--names-file`; geçici `PIXEL_OFFICE_HOME`, `PIXEL_OFFICE_CLAUDE_SETTINGS` ve `HOME`; 8765'e dokunulmadı):
  - **Bulut:** `install` ile geçici `settings.json` güncellendi ve "Kurulum testi" karakteri göründü. Gerçekçi bir oturum `pixel_hook.py` üzerinden gönderildi. Şu sıra görüldü: idle → thinking → working Read → waiting "İzin bekliyor" → thinking → working Agent + Explore alt-ajanı → alt-ajan uyku → idle → çıkış.
  - **Gizlilik:** `hook.log` ve durum dosyalarında `SECRET` geçmedi.
  - **Yerel:** Aynı istemci yerel sunucuya da çalıştı (thinking → working Edit → idle → çıkış). `status` CLI'ı da çalıştı.
  - **cc_hook:** curl ile 200 döndü, Windows yolundan proje adı doğru alındı.
  - **pixel-run (bulut):** working → idle → sleeping "Hata: kod 4" görüldü, çıkış kodu 4 döndü.
  - **pixel-run (yerel, pty):** working → idle → working → sleeping görüldü. Gerçek bir TTY'de (`script`) girdi doğru okundu.
  - **pixel-proxy:** Sahte upstream'e karşı NDJSON, SSE ve `/api/tags` yanıtları doğrudan istekle `cmp` ile bayt bayt aynı çıktı. Ollama ajanı thinking → idle, `/agent/aider/` önekiyle gelen ajan thinking → working `get_weather` oldu; `--send-model` ile model adı etikete yazıldı. Bulut odasında da proxy ajanı göründü.

  Canlı denemede bulunan ve düzeltilen hatalar:
  - pty modunda `tty.setraw` önceden yazılmış girdiyi siliyordu. `TCSANOW` kullanılarak düzeltildi.
  - SessionEnd'de uyuyan alt-ajanlar 120 sn ofiste kalıyordu. Artık onlar da `offline` gönderiliyor.

  Sapmalar CLOUD.md §17.8'de yazılı.

  Bilinen sınırlamalar ve Aşama 10'a kalanlar:
  - PowerShell istemcisi ve `install.ps1` **hiç çalıştırılmadı**, çünkü bu makinede `pwsh` yok. Yalnızca dikkatle statik olarak incelendi; bu sırada `$HOME` çakışması, `Dictionary.Contains`, `Write-Error`/Stop ve dizi açılması hataları bulunup düzeltildi. Vektör testi `pwsh` bulunduğunda kendiliğinden çalışır.
  - Bu makinede gerçek bir Claude Code oturumuyla, gerçek aider ile ve gerçek Ollama/LM Studio ile deneme yapılmadı. Yalnızca gerçekçi olay ve akış verileri kullanıldı.
  - Python 3.8–3.13 bulunmadığından denenemedi; testler 3.14 ile çalıştırıldı.
  - Testler `web/hook/__pycache__` oluşturabilir. `.vercelignore` bunu dağıtım dışında tutar; `.gitignore` Aşama 10'da eklenecek.

  Aşama 8 tamamlandı. Sıradaki adım: Aşama 9.

- **2026-10-11 — Aşama 9 (Frontend Mühendisi):** Bulut arayüzü ve PWA yazıldı. Bağımlılık ve derleme adımı yok, tüm kod vanilla ES modülleri.

  Yeni dosyalar:
  - `web/js/transport.js`: `SseTransport` (v1 SSE istemcisi aynen taşındı) ve `PollingTransport` (sunucunun `pollMs` değeri, ±%10 jitter, 2→30 sn geri çekilme, 404'te "Oda bulunamadı", gizliyken duraklama, değiştirilebilir zamanlayıcı penceresi).
  - `web/js/cloud.js`: arka uç tespiti, giriş ekranı (oda oluştur, oluşturma parolası, bağlantıyla katıl, son odalar), `#key=` okuma ve adres çubuğundan silme, Davet et ve Ajan bağla pencereleri, servis çalışanı kaydı, yükleme düğmesi.
  - `web/js/pip.js`: Document PiP ile mini pencere ve sağ alta konumlanan açılır pencere yedeği.
  - `web/manifest.webmanifest`, `web/sw.js` (yalnızca bulutta; `/api/*` ve `/hook/*` hiç yakalanmaz), `web/icons/*.png` ve onları üreten `tools/make_icons.py` (zlib + struct).
  - Düzenlenenler: `main.js`, `agents.js` (sahip rengi gömlek), `renderer.js` (`ad·proje` etiketi), `ui.js` (sahibe göre gruplama, ayrıntılar, ✎ yeniden adlandırma, bildirim), `index.html`, `style.css`. `sprites.js`, `office.js`, `pathfinding.js` değişmedi.

  Öne çıkanlar:
  - **Ajan bağla:** macOS/Linux, Windows, Genel API ve Gelişmiş sekmeleri. Komutlar gerçek adres, oda ve anahtarla doldurulur. Ad, kabukta güvenli olacak şekilde süzülür. HTTP kancası parçacığı kırmızı uyarı ve onay kutusunun arkasındadır.
  - **Yeniden adlandırma (iki mod):** ✎ ile satır içi düzenleme (Enter/blur kaydeder, Esc iptal eder), "yeni oturumlara da ver" kutusu (bulutta sahip, yerelde proje kapsamı), "Varsayılana dön", iyimser güncelleme ve hata bildirimi. İzleme bağlantısında ve özelliği olmayan yerel sunucuda ✎ gizlenir.
  - **Mini pencere:** canvas ve durum şeridi PiP penceresine taşınır. rAF ve polling zamanlayıcıları PiP penceresinde çalışır. Kapanınca her şey yerine döner.

  Küçük backend eki (CLOUD.md §17.9): yerel sunucu artık `GET /api/config` isteğine `{"mode":"local","features":[…]}` döndürüyor. Böylece yerel modda konsolda 404 hatası çıkmıyor; yalın v1 `make_server` hâlâ 404 döndürüyor. `.webmanifest` MIME türü eklendi. 2 yeni test yazıldı; toplam 232 test geçiyor (1 atlandı, `pwsh` yok).

  Doğrulama, stdlib ile yazılmış bir CDP betiğiyle sürülen headless Chrome'da yapıldı. Portlar 8931–8934 kullanıldı, 8765'e dokunulmadı, `~/.claude`'a hiç yazılmadı:
  - **Oda:** Oda oluşturuldu ve davet penceresi kendiliğinden açıldı. Katılma bağlantısı `#key=` içeriyordu. Bağlantı açılınca anahtar `localStorage`'a kaydedildi ve adres çubuğundan silindi. Oda adındaki `<b>` düz metin olarak göründü. Oluşturma parolasında yanlış parola "Oluşturma parolası yanlış." hatasını verdi, doğru parola odayı oluşturdu.
  - **Ajanlar:** `pixel_hook.py` (geçici HOME ile `install`, ardından olaylar) ve `pixel_agent.py` ile gelen ajanlar göründü. Boş odanın 60 sn'lik ilk polling turu bekleniyor. Durum değişiklikleri (`waiting_for_user`) 4–5 sn içinde yansıdı. Gömlek rengi sahip rengine eşitti. Etiketler `Timur·Pixel` / `aider·notlar` biçimindeydi. `offline` gönderilen karakter kapıya yürüdü.
  - **Yeniden adlandırma:** Ajan kapsamında yapılan ad değişikliği sonraki üç durum güncellemesinden sonra da kaldı. Sahip kapsamında verilen ad aynı sahibin yeni oturumuna da uygulandı. Blur ile kaydetme, Esc ile iptal ve Varsayılana dön çalıştı.
  - **İzleme bağlantısı** (temiz profil): ✎ ve sıfırlama bağlantısı yoktu. Ajan bağla penceresi katılma bağlantısının gerektiğini açıkladı.
  - **PWA:** Servis çalışanı etkin oldu (18 dosya önbellekte). `Page.getAppManifest` ve `getInstallabilityErrors` boş döndü, `beforeinstallprompt` geldi ve "Uygulamayı yükle" düğmesi göründü. Çevrimdışı yeniden yüklemede sayfa önbellekten açıldı, ağ geri gelince bulut moduna geçti.
  - **Gizli sekme:** 30 sn'lik bekleme süresinden sonra 30 sn boyunca 0 istek gitti. Sekme görünür olunca hemen 1 istek gitti.
  - **Mini pencere:** Document PiP headless Chrome'da açıldı ve ekran görüntüsünde ofis ile durum şeridi göründü. Ana sayfa gizliyken PiP açıkken 36 sn'de 4 polling isteği gitti ve oyun zamanı 36 sn ilerledi. PiP kapanınca canvas, zamanlayıcılar ve döngü ana pencereye döndü. API silinerek yedek yol denendi: `?view=mini` açılır penceresi kompakt düzende açıldı.
  - **Komutlar:** Ajan bağla penceresindeki `install.sh`, `pixel_agent.py run`, curl ve HTTP kancası komutları geçici HOME ile gerçekten çalıştırıldı, ajanlar odada göründü.
  - **XSS:** Kötü niyetli sahip, proje, etiket ve ad metinleri düz metin olarak göründü. Hiç `alert` çalışmadı ve DOM'a eleman eklenmedi.
  - **Mobil (390 px):** alt alta düzen, yatay kaydırma yok.
  - **Yerel mod** (`server.py --demo --names-file <geçici>`): SSE, "DEMO MOD", giriş ekranı ve bulut düğmeleri yok, servis çalışanı yok. Ajan ve proje kapsamında ad değişikliği dosyaya yazıldı ve ikinci sekmeye SSE ile anında yansıdı; Varsayılana dön çalıştı.
  - Tüm senaryolarda konsol hatası yok.

  Bulunan ve düzeltilen hatalar:
  - Mini açılır pencerede (`?view=mini`) ResizeObserver döngüsü oluşuyordu. Düzeltildi.
  - Çevrimdışı açılışta sayfa yanlışlıkla yerel moda düşüyordu. Düzeltildi.
  - "Yeni oturumlara da ver" kutusu önceden işaretli geldiği için sahip varsayılanı farkında olmadan değişiyordu. Kutu artık işaretsiz geliyor.

  Sapmalar CLOUD.md §17.9'da yazılı.

  Bilinen sınırlamalar ve Aşama 10'a kalanlar:
  - Headless Chrome, PiP penceresinin 400×290 boyut isteğini uygulamıyor. Gerçek masaüstü Chrome'da sürükleme, konumun hatırlanması ve ana pencerenin gerçekten küçültülmesi elle denenmeli; küçültme burada `visibilityState` taklidiyle denendi.
  - Firefox ve Safari'de yedek yol yalnızca API silinerek taklit edildi.
  - Lighthouse ile kurulabilirlik denetimi ve gerçek Vercel üzerinde deneme yapılmadı.
  - Yeni ve boş bir odada ilk ajanın görünmesi 60 sn'ye kadar sürebilir (sunucunun boş oda için verdiği `pollMs`).
  - Yerel modda da "Mini pencere" düğmesi ve ✎ görünür; bunlar bilinçli eklemelerdir.

  Aşama 9 tamamlandı. Sıradaki adım: Aşama 10.

- **2026-10-11 — Aşama 10 (QA Mühendisi):** v2 kodu baştan sona gözden geçirildi, yayına hazırlandı. **v2 tamamlandı**; geriye yalnızca kullanıcının Vercel'e dağıtımı kaldı.

  Gözden geçirilenler: HMAC anahtarının taşınması (adres çubuğu, `Referer`, sunucu günlüğü, servis çalışanı önbelleği), CORS, hız limitleri, gövde sınırları, Upstash REST hata yolları, tüm arayüz yollarında XSS, kurulum komutlarında kabuk enjeksiyonu, `~/.claude/settings.json` yükleyicisinin güvenliği, hook istemcilerindeki gizlilik izin listesi, `sw.js`'in `/api` davranışı ve Vercel'in fonksiyonları algılaması.

  Bulunan ve düzeltilen hatalar:
  1. **Dağıtımı engelleyen hata: Vercel `api/*.py` dosyalarını fonksiyon olarak algılamazdı.** Vercel'in kaynak kodu incelendi (`find_app_or_handler_impl`). Bir dosya yalnızca en üst düzeyde `class handler` (ya da `app`/`application`) tanımlıyorsa fonksiyon sayılıyor. Yedi dosyada `handler = make_handler()` ataması vardı, yani gerçek dağıtımda hiçbir `/api` ucu çalışmayacaktı. Hepsi `class handler(ApiHandler)` yapıldı. `tests/test_release.py` algılayıcının `ast` ile yazılmış bir kopyasıyla her dosyayı denetliyor ve bir işleyiciyi gerçek bir soket arkasında çalıştırıyor.
  2. **Yeni odada ilk ajan 60 sn'ye kadar gecikiyordu** (Aşama 9'dan). `PollingTransport.boost()` eklendi: oda açıldıktan sonraki 3 dk boyunca (oda boşken), "Ajan bağla" penceresi açıkken ve kapandıktan sonraki 3 dk boyunca 5 sn'de bir yoklama yapılıyor. Boş oda CDN TTL'i 30 sn'den 5 sn'ye indi. Ölçüm: ilk ajan, ilk kanca olayından **3,4 sn** sonra göründü. Bütçe yeniden hesaplandı (CLOUD.md §12, §17.10): zorlama senaryosunda CDN %76 → %79, Upstash %64 → %73; yerel ajanlarla birlikte CDN %83, Upstash %81. Ücretsiz planlar yetiyor.
  3. **Yükleyici symlink'i bozuyordu.** `~/.claude/settings.json` bir dotfiles symlink'iyse atomik yazım symlink'i düz dosyayla değiştiriyordu. `install`/`uninstall` artık hedef dosyaya yazıyor (test eklendi).
  4. **Kurulum komutunda `-` ile başlayan ad** argparse/PowerShell tarafından seçenek sanılabiliyordu. `cmdName` artık baştaki `-` ve `.` karakterlerini siliyor. Tırnaklama zaten güvenliydi; `x'; rm -rf ~ #` gibi bir ad `'x rm -rf'` olarak çıktı.

  Sorunsuz bulunanlar: anahtar hiçbir istek adresinde ya da `Referer`'da yok (46 istek tarandı); önbellekte `/api` ya da anahtar yok (18 kayıt); `hook.log`'da istem yok; hatalar ve günlükte gövde ya da anahtar yok; tüm güvenilmeyen metinler `esc()`/`textContent` üzerinden geçiyor; renkler `#rrggbb` ile doğrulanıyor.

  Vercel yapılandırması Vercel'in kaynak kodu (`detect-builders.ts`, `python/src/index.ts`) ve güncel belgeleriyle doğrulandı:
  - `framework: null` ve build komutu yokken `web/**/*` statik olarak `/` altında sunuluyor; `api/*.py` algılaması `outputDirectory`'den bağımsız. Rewrites yedeğine gerek yok.
  - Python fonksiyonu tüm proje dosyalarını paketliyor (`.vercelignore` hariç), `includeFiles` ek güvence.
  - `.python-version` 3.12 destekleniyor.
  - `.vercelignore` artık `*.pyc`, `.env*` ve `.pixel-office/` dosyalarını da dışlıyor.

  Yayın dosyaları: `.gitignore`, `LICENSE` (MIT, Timur Yurdakan, 2026), yeni README (Türkçe dağıtım rehberi + İngilizce özet), `docs/screenshot-cloud.png`. Belgelerdeki ve test vektörlerindeki kişisel yollar `/home/user/...` yapıldı. `sw.js` sürümü `po-v2.0.1` oldu.

  Testler: **251 test geçiyor** (önceden 232; 1 test `pwsh` olmadığı için atlanıyor). Yeni testler: `test_release.py` (17), ad dosyasının başarısız yazımı, `pixel-proxy` istemci kopması. Mevcut testlerde boş oda TTL'i 30 → 5 olarak güncellendi.

  Uçtan uca (headless Chrome, CDP; portlar 8951–8955 ve 9351–9355; 8765'e ve `~/.claude`'a dokunulmadı):
  - **Bulut** (`dev_server.py`): oda arayüzden oluşturuldu, davet penceresi açıldı ve katılma bağlantısı `#key=` içeriyordu. Geçici HOME ile `pixel_hook.py install` yapıldı ve 13 olay kaydedildi. İlk ajan 3,4 sn'de göründü. Ardından `pixel-run` (Ayşe/aider: çalışıyor → uyuyor) ve `pixel-proxy` (Mert/ollama: düşünüyor → `get_weather` ile çalışıyor) eklendi. Proxy'deki geçiş 34 sn sürdü; bu, gruptaki değişikliklerin bilinçli olarak 30 sn'de bir gönderilmesinden kaynaklanıyor. `waiting_for_user` göründü. XSS içeren bir ad düz metin olarak kaldı ve sonraki güncellemeler adı ezmedi. Mini pencere (Document PiP) açıldı, kapanınca canvas yerine döndü. İzleme bağlantısı temiz bir profilde açıldı: ✎ yoktu ve "Ajan bağla" anahtarın gerektiğini açıkladı. `SessionEnd` ile karakter kapıdan çıktı. Pencere kapanınca yoklama 3 dk boyunca 5 sn'de bir sürdü. Konsolda hata yok.
  - **Yerel** (`server.py --demo`, geçici `--names-file`): SSE çalıştı; ajan ve proje kapsamında ad değişikliği ikinci sekmeye anında yansıdı; Varsayılana dön çalıştı; `pixel_agent.py report` ile yerel durum API'si ajanı göründü. Konsolda hata yok.
  - Ekran görüntüleri incelendi.

  Gerçek Claude Code testi: `claude` CLI bu makinede PATH'te yok, test **yapılmadı**. Bunun yerine gerçekçi kanca olayları gerçek istemciden geçirildi.

  Kalan sınırlamalar: gerçek Vercel/Upstash dağıtımı, `x-vercel-cache: HIT` ve Upstash'in kota aşımındaki davranışı denenmedi; `pixel_hook.ps1` hiç çalıştırılmadı (`pwsh` yok) ve symlink düzeltmesi yalnızca Python istemcisinde var; gerçek aider/Ollama ve gerçek masaüstünde PiP sürükleme denenmedi; Python 3.8–3.13 yok. Bunlar §5b'deki elle yapılacaklar listesinde.
