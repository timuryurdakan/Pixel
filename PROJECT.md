# Pixel Office 🖥️☕💤

> **Proje durumu: v1.0 ✅ tamamlandı (yerel mod, 2026-10-11). v2 "Bulut Ofis" ✅ tamamlandı (Aşama 6–10 ✅, 2026-10-11). v3 "Ajan Kontrol Merkezi" ✅ https://www.pixeloffice.com.tr adresinde yayında (Aşama 11–17 ✅; Aşama 18 Antigravity ✅, Aşama 19 uzaktan onay ✅; Aşama 20 kalan görevler ✅ — yayına alma ve kontroller için bkz. [`docs/YAYIN.md`](docs/YAYIN.md), mimari için [`docs/V3.md`](docs/V3.md)).** Kullanım için bkz. [`README.md`](README.md), bulut tasarımı için [`docs/CLOUD.md`](docs/CLOUD.md).
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
| 11 | v3 Mimari | Yazılım/Güvenlik Mimarı | `docs/V3.md`, §5c | ✅ Tamamlandı |
| 12 | Gerçek Zamanlı Röle | Gerçek Zamanlı/Edge Mühendisi | `relay/`, `tools/dev_relay.py`, `api/relay_ticket.py`, `web/js/relay.js` | ✅ Tamamlandı |
| 13 | Bağlayıcı Uygulama | Bağlayıcı/Güvenlik Mühendisi | `pixel_connector/`, `web/js/e2e.js`, `web/connector/` | ✅ Tamamlandı |
| 14 | Arayüz | Frontend Mühendisi | görev kutusu, cihazlar sayfası, token göstergesi, telefon düzeni | ✅ Tamamlandı |
| 15 | Güvenlik İncelemesi & QA & Dokümantasyon | Güvenlik İnceleyici + QA | inceleme, uçtan uca test, README, rehber | ✅ Tamamlandı (gerçek dünya denemeleri yayına almadan sonra, kullanıcıyla) |
| 16 | Siber Güvenlik Denetimi | Uygulama Güvenliği Mühendisi | `docs/SECURITY_AUDIT.md`, düzeltmeler, `tests/test_phase16_security.py` | ✅ Tamamlandı |
| 17 | Çalışma klasörü kurulum düzeltmesi | Bağlayıcı Mühendisi | kurulumda `~/PixelOfficeIsler`, `pixel-connector workspace` komutu, arayüz ipucu, `doctor` saat farkı | ✅ Tamamlandı |
| 18 | Antigravity bağlantısı | Entegrasyon Mühendisi | `pixel_antigravity.py` izleyicisi (`pixel_agent.py antigravity`), `pixel-antigravity` kısayolu, servis, belgeler, testler | ✅ Tamamlandı |
| 19 | Ofisten kontrol: uzaktan onay | Bağlayıcı/Güvenlik Mühendisi + Frontend | izin isteklerini ve seçmeli soruları eşleşmiş telefondan onaylama/reddetme (`remote-approvals`, `approvals.js`, kanca `--decide`) | ✅ Tamamlandı (gerçek telefon testi 2026-10-22 geçti) |
| 20 | Kalan görevler | Koordinatör (Claude) + uzmanlar | `v2.md` raporundaki 8 paket + H38 | ✅ Tamamlandı (2026-10-22) |
| P1–P9 | Patron Odası | Yazılım/Ürün Mimarı (tasarım) + uzmanlar, sırayla | [`docs/PATRON_ODASI.md`](docs/PATRON_ODASI.md), görevler `docs/GOREV_PANOSU.md` P1–P9 | 🔄 P1–P8 ✅ (2026-10-29), sırada P9 (kullanıcı onayıyla gerçek deneme) |

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

- [x] `sudo apt install git` ve `git config --global user.name/user.email` (ya da GitHub Desktop / web arayüzünden yükleme)
- [x] **Kişisel** GitHub hesabında yeni bir depo oluşturup projeyi `git push` ile yüklemek (`.gitignore` hazır; `names.json` depoya girmez)
- [x] Vercel'de (Hobby) **Add New → Project → Import**; ayarlara dokunmadan **Deploy**
- [x] Vercel → Storage → **Upstash for Redis** (Free, Frankfurt) → projeye bağlamak
- [x] `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` ile `PIXEL_SECRET` üretip Vercel'e girmek (**bir kez belirlenir, sonra değiştirilmez**); önerilen `PIXEL_CREATE_TOKEN`, isteğe bağlı `PIXEL_PUBLIC_URL`
- [x] **Redeploy**, sonra doğrulama: `/api/health` → `"storage":"upstash","storageOk":true`; `/README.md` ve `/server.py` → 404; `/hook/pixel_hook.py` → `text/plain`; `curl -sI ".../api/state?room=<oda>"` iki kez → ikincisinde `x-vercel-cache: HIT`
- [x] Oda oluşturup kendi bilgisayarında "Ajan bağla" komutunu çalıştırmak ve Claude Code'u yeniden başlatmak (gerçek Claude Code ile ilk deneme)
- [ ] En az bir arkadaşın (tercihen Python'suz Windows) tek komutla bağlanması; `pixel_hook.ps1` ilk kez orada çalışacak, `doctor` çıktısına bakılmalı
- [ ] İsteğe bağlı: gerçek Ollama/LM Studio ile `pixel-proxy`, gerçek aider ile `pixel-run`; Chrome'da mini pencereyi sağ alta sürükleyip ana pencereyi küçültmek
- [ ] İlk ay haftada bir Vercel **Usage** ve Upstash **Usage** kontrolü; gerekirse `PIXEL_POLL_ACTIVE_MS` / `PIXEL_POLL_CALM_MS`
- [ ] Sonraki sürümlerde `web/sw.js` içindeki `VERSION` değerini artırmak

## 5c. v3 — Ajan Kontrol Merkezi (Deneme Sürümü)

**Amaç:** Ofisteki ajanları yalnızca izlemek değil, onlara görev vermek. Kendi bilgisayarında çalışan bir **bağlayıcı** (`pixel-connector`), oradaki Claude Code ve Ollama ajanlarını ofisten kontrol edilebilir yapar. Bir karaktere tıklayıp görev yazılır, yanıt ayrıntılar panelinde canlı akar. Yeni ajan oluşturulabilir, çalışan görev durdurulabilir. Ajan başına token ve tahmini maliyet sayacı vardır. Telefondan (PWA) cihazlardaki yerel yapay zeka ortamları (Ollama sunucusu, yüklü modeller) görülüp başlatılıp durdurulabilir. Bu bir **deneme sürümüdür**: sahibi ve birkaç arkadaşı için, ileride bir girişime dönüşebilir. Her şey ücretsiz planlarda ve kredi kartı olmadan kalır. **v1 yerel mod ve v2 bulut olduğu gibi çalışmaya devam eder**; v3 yalnızca ekleme yapar. v3 ortam değişkenleri yoksa site birebir v2 gibi davranır.

Teknik sözleşme: [`docs/V3.md`](docs/V3.md). v3 aşamalarındaki tüm ajanlar uygulamadan önce bunu okumalı.

**Temel kararlar (özet):**
- **Gerçek zamanlı röle:** Cloudflare Workers Free üzerinde, oda başına bir **Durable Object** (yalnızca SQLite arka ucu ücretsiz planda var) ve WebSocket Hibernation API kullanılır. Ücretsiz limitler: günde 100 bin istek ve 13.000 GB-sn. Gelen WS mesajları 20:1 oranında sayılır, giden mesajlar ücretsizdir. 5 kullanıcı için istek kotasının ≈ %8'i, süre kotasının en kötü durumda ≈ %35'i kullanılır. Vercel WebSocket tutamadığı ve Upstash polling'i kotayı aşacağı için bu yol seçildi.
- **Node.js gerekmez:** Röle JavaScript ile ve npm çalışma zamanı bağımlılığı olmadan yazılır. **Cloudflare Workers Builds**, GitHub deposundan (`relay/` klasörü) derleyip dağıtır. Worker testleri Cloudflare'in derleme ortamında `node --test` ile çalışır; test başarısızsa dağıtım olmaz. Yerelde aynı protokolü uygulayan stdlib Python rölesi (`tools/dev_relay.py`) ile test edilir. Aynı uyumluluk testleri hem bu röleye hem dağıtılmış Worker'a karşı çalışır. **`sudo apt install nodejs npm` gerekmez.**
- **Röle kimlik doğrulaması:** Vercel, oda yazma anahtarını görünce 120 sn geçerli bir **röle bileti** üretir (`POST /api/relay_ticket`, HMAC-SHA256, `RELAY_SECRET`). Bu sır hem Vercel'de hem Cloudflare'de aynı değerle tanımlanır. Bilet adres çubuğunda değil `Sec-WebSocket-Protocol` başlığında taşınır. Tarayıcılar için `Origin` denetlenir. Cloudflare `PIXEL_SECRET`'i hiç görmez.
- **Kontrol yetkisi yalnızca eşleşmiş tarayıcılarda:** Oda bağlantısı (yazma anahtarı) görev vermeye yetmez. Cihazla **tek kullanımlık kodla** eşleşmiş tarayıcı gerekir. Arkadaşlar karakterleri görür ama senin bilgisayarına komut veremez.
- **Eşleştirme:** Cihaz terminalinde 12 karakterlik bir kod görünür (`K7Q2-M4X5-Z6A9`, 10 dk geçerli, tek deneme). Kodun gizli kısmı (40 bit) PBKDF2-SHA256 (600.000 tur) ile güçlendirilir ve P-256 açık anahtarlarının karşılıklı doğrulanmasında kullanılır. Sonra terminalde 6 haneli bir doğrulama kodu (SAS) onaylanır. Röle araya girse bile fark edilir; yanlış kod, kodu yakar.
- **Uçtan uca şifreleme:** ECDH P-256 + HKDF-SHA256 + AES-256-GCM. Bağlantı başına el sıkışmada geçici ve kalıcı anahtarlar birlikte kullanılır (ileriye dönük gizlilik ve karşılıklı kimlik doğrulama). Yöne göre ayrı anahtar, sayaçlı nonce ve sıkı tekrar denetimi vardır. Tarayıcı yalnızca WebCrypto kullanır; özel anahtar dışa aktarılamaz biçimde IndexedDB'de durur. Bağlayıcı tarafında, RFC/NIST/Wycheproof test vektörleriyle ve WebCrypto ile çapraz test edilen **gömülü saf Python** gerçekleme kullanılır. `cryptography` paketi istenmez (stdlib sözü korunur). Röle yalnızca şifreli metni görür: istemler, çıktılar, klasör yolları, ortam adları ve kullanım sayıları rölede açık görünmez. Karakter durumu v2'deki gibi gider; buna yalnızca opak bir `ctl` alanı eklenir.
- **Bağlayıcı:** `pixel_connector/` paketi, tek dosyalık bir stdlib zipapp (`pixel-connector.pyz`, Python ≥ 3.8) olarak dağıtılır. Linux'ta systemd **kullanıcı** servisi olarak çalışır; macOS (launchd) ve Windows (Görev Zamanlayıcı) belgelenir. Kendini buluttan asla güncellemez; kurulumda SHA-256 doğrulanır.
- **Motorlar:** Claude Code `claude -p --output-format stream-json --verbose --include-partial-messages` ile çalıştırılır. İstem stdin'den verilir, oturum `--session-id`/`--resume` ile sürdürülür. Ollama `/api/chat` NDJSON akışıyla kullanılır (`prompt_eval_count`, `eval_count`). OpenAI uyumlu yerel sunucular (LM Studio, llama.cpp) da desteklenir.
- **Claude Code izinleri (bilinçli seçim):** Varsayılan profil **Düzenleyebilir**: `--permission-mode dontAsk --allowedTools Edit,Write,NotebookEdit`. Klasör içinde okur ve düzenler, kabuk komutu çalıştıramaz. Diğer profiller "Sadece okuma", "Geliştirici" (cihazda tanımlı komut önekleri) ve "Otomatik"tir; Otomatik yalnızca cihaz ayarında açıksa kullanılabilir. `bypassPermissions` / `--dangerously-skip-permissions` **hiçbir zaman** kullanılmaz ve ayarlanamaz.
- **Cihaz tarafı güvenlik:** İzinli çalışma klasörü listesi (realpath + ortak yol denetimi, sembolik bağ kaçışı engellenir, `~/.ssh` gibi yasak klasörler var), çalışma ortamları için izin listesi (buluttan yalnızca kimlik gelir, komutlar yerel dosyadan aynen çalışır, kabuk yok), hız limitleri, bütçeler, isteğe bağlı görev başına cihazda onay (127.0.0.1 sayfası, CLI ya da bildirim), eşleşme iptali ve cihazdaki denetim günlüğü (varsayılan olarak istemin yalnızca hash'i ve uzunluğu yazılır).
- **Token ve maliyet:** Cihazda SQLite defteri tutulur. Claude için `modelUsage` ve `total_cost_usd` kullanılır; bunlar Claude Code'un kendi tahminidir ve devam ettirilen oturumlarda fark alınarak düzeltilir. Bütçe ajan, cihaz ya da motor başına; gün, hafta ya da ay için; token ya da tahmini dolar olarak verilebilir. Yüzde göstergesi yalnızca bir bütçe varsa görünür. **Dürüstlük kuralı:** Claude aboneliğinde kalan limit bilinemez. Yalnızca Claude Code `rate_limit_event` gönderirse, gönderdiği değer olduğu gibi ve saatiyle gösterilir. Abonelikte maliyet "API eşdeğeri tahmini" diye etiketlenir.
- **Yerel mod:** `server.py` için istemle kontrol bu denemede **yok**. v3.1'de `dev_relay` yerel sunucuya bağlanarak eklenecek; protokol buna hazır.
- **Anthropic kullanım koşulları:** Değiştirilmemiş `claude` programı kullanıcının kendi bilgisayarında ve kendi girişiyle çalışır. Kimlik bilgisine hiç dokunulmaz, ajanlar paylaşılmaz, görevleri yalnızca insan başlatır. Girişim aşamasında API anahtarıyla kimlik doğrulamaya geçilmesi ve Anthropic'e danışılması önerilir (V3.md §18.1).
- **v4'e ertelenenler:** Otomasyon şablonları ve çok ajanlı iş akışları bu sürümde yok. Ayrı ve ayrıntılı bir **"v4 — Otomasyonlar"** çalışmasında ele alınacak.
- **Yerleşik ajanlar:** Mete ve Saltuk odadan kaldırıldı (`POST /api/residents` ile `[]`). Kod (`pixel_cloud/residents.py`, `api/residents.py`) yerinde kalabilir; v3 ona dokunmaz.

| # | Aşama | Sorumlu | Teslimatlar | Durum |
|---|---|---|---|---|
| 11 | v3 Mimari | Yazılım/Güvenlik Mimarı | `docs/V3.md`, bu bölüm | ✅ Tamamlandı |
| 12 | Gerçek Zamanlı Röle | Gerçek Zamanlı/Edge Mühendisi | `relay/` (Worker + Durable Object), `tools/dev_relay.py`, `pixel_connector/ws.py`, `web/js/relay.js`, `api/relay_ticket.py`, `pixel_cloud` eklemeleri, `dev_server.py --relay`, testler | ✅ Tamamlandı |
| 13 | Bağlayıcı Uygulama | Bağlayıcı/Güvenlik Mühendisi | `pixel_connector/`, `web/js/e2e.js`, `web/js/idb.js`, `tools/build_connector.py`, `web/connector/`, testler | ✅ Tamamlandı |
| 14 | Arayüz | Frontend Mühendisi | `web/js/{control,prompt,devices,usage}.js`, `main.js`/`ui.js`/`cloud.js`/`index.html`/`style.css`/`sw.js` düzenlemeleri, `vercel.json` CSP | ✅ Tamamlandı |
| 15 | Güvenlik İncelemesi & QA & Dokümantasyon | Güvenlik İnceleyici + QA Mühendisi | inceleme, düzeltmeler, uçtan uca testler, README, `rehber.html`, kullanıcının dağıtım listesi | ✅ Tamamlandı |

Bağımlılıklar: **Aşamalar kesin sırayla yürür, her aşamayı tek bir uzman yapar:** 12 → 13 → 14 → 15. 13, 12'nin röle protokolünü ve `dev_relay`'i kullanır. 14, 13'ün `e2e.js` ve bağlayıcısıyla (sahte motorlar) geliştirilir. 15 en son yapılır. Bir aşama tüm testleri yeşil olmadan ve günlüğe kayıt düşülmeden bitmiş sayılmaz.

### Aşama 11 — v3 Mimari (Yazılım/Güvenlik Mimarı)
Teslimat: `docs/V3.md`, PROJECT.md §5c.
- [x] Mevcut kodun incelenmesi (`pixel_cloud`, `api/`, `web/js`, `web/hook`, `server.py`); v3'ün yalnızca ekleme yapacak biçimde tasarlanması
- [x] Röle araştırması: Cloudflare Workers Free ve Durable Objects (ücretsiz planda var, yalnızca SQLite), Hibernation, Workers Builds, Python Workers; alternatiflerin karşılaştırılması
- [x] Node'suz derleme, test ve dağıtım yolu (Workers Builds + `dev_relay.py` + ortak uyumluluk testleri)
- [x] Röle kimlik doğrulaması (bilet, `RELAY_SECRET`, Origin), tam mesaj protokolü (L1 röle çerçeveleri, L2 şifreli mesajlar) ve JSON şemaları
- [x] Şifreleme tasarımı: algoritmalar, anahtar türetme, nonce, anahtar saklama, eşleştirme ve el sıkışma sıra diyagramları; saf Python mı `cryptography` mi kararı
- [x] Tehdit modeli tablosu (22 satır)
- [x] Claude Code stream-json ve Ollama akış ayrıştırma (token alanları dahil), izin profilleri
- [x] Token ve maliyet modeli, bütçe ve gösterge kuralları, bilinemeyenler
- [x] Çalışma ortamı izin listesi ve `connector.json` biçimi
- [x] Türkçe arayüz tasarımı: görev kutusu, yeni ajan penceresi, cihazlar sayfası, telefon düzeni, hata metinleri
- [x] Ücretsiz plan bütçe hesabı (Cloudflare, Vercel, Upstash; 5 kullanıcı)
- [x] Kullanıcının elle yapacağı dağıtım adımları, test planı, riskler ve açık sorular (Anthropic koşulları dahil)

**Kabul kriterleri:** V3.md, Aşama 12–15'in birbirinden bağımsız ve sırayla uygulanmasına yetecek kesinlikte olmalı. Her karar tek seçenek ve gerekçeyle verilmeli. Kaynaklar tarihli olmalı. Bütçe açık formüllerle yazılmalı. ✔️

### Aşama 12 — Gerçek Zamanlı Röle (Gerçek Zamanlı/Edge Mühendisi)
Teslimat: `relay/wrangler.jsonc`, `relay/package.json`, `relay/src/{index,room,lib}.js`, `relay/test/lib.test.js`, `tools/dev_relay.py`, `pixel_connector/__init__.py` + `ws.py`, `web/js/relay.js`, `api/relay_ticket.py`, `pixel_cloud/relay.py` ve `settings`/`validate`/`derive`/`app` eklemeleri, `dev_server.py --relay`, `.vercelignore`/`.gitignore`, `tests/test_relay_ticket.py`, `tests/relay_conformance.py`, `tests/test_dev_relay.py` (V3.md §3, §4, §6.2, §13, §17.1).
- [x] `lib.js`: bilet çözme ve doğrulama (`crypto.subtle.verify`), çerçeve doğrulama (rol başına), token bucket, kimlik biçimleri
- [x] `index.js`: `/health`, `/v1/rooms/<rid>/ws` yükseltmesi, bilet + Origin denetimi, Durable Object'e yönlendirme
- [x] `room.js`: `acceptWebSocket` ve etiketler, ek veri (attachment), `welcome`/`presence`/`msg`/`left`/`err`, `pair:` yuvaları, cihaz değiştirme (4009), oda sınırları, otomatik ping yanıtı; alarm ve zamanlayıcı yok, depolama yazımı yok
- [x] `wrangler.jsonc` (`exports` sqlite ya da eski `migrations` yedeği) ve sabitlenmiş `wrangler` sürümüyle `package.json`
- [x] `node:test` birim testleri (Workers Builds'te çalışır)
- [x] `pixel_connector/ws.py`: stdlib RFC 6455 istemci + sunucu çerçeveleme, TLS, ping/pong, kapatma kodları
- [x] `tools/dev_relay.py`: aynı protokol, limitler ve hata kodları; `dev_server.py --relay`
- [x] Vercel: `POST /api/relay_ticket` (yazma anahtarı, hız limiti, 0 Redis komutu), `/api/config` içinde `relay`, durum alanı `ctl` → Agent JSON `control`
- [x] `web/js/relay.js`: bilet, WebSocket, yeniden bağlanma, 50 sn ping, gizliyken bağlantıyı kapatma, `quota` durumu
- [x] `relay_conformance.py`; `dev_relay`'e karşı CI'da çalışır

**Kabul kriterleri:** Yalnızca stdlib (Python) ve sıfır çalışma zamanı npm bağımlılığı (JS). `python3 -m unittest discover -s tests` geçer ve mevcut 251 test değişmeden geçmeye devam eder. Uyumluluk testleri `dev_relay`'e karşı tamamen geçer. Bilet olmadan, süresi geçmiş ya da sahte biletle bağlanma reddedilir. Origin'siz tarayıcı ve Origin'li cihaz reddedilir. Röle hiçbir zaman `b` alanını ayrıştırmaz, günlüğe yazmaz, saklamaz (testle doğrulanır). v3 ortam değişkenleri yokken `/api/config` `"relay": null` döner ve v2 davranışı birebir aynıdır. Kullanıcı Workers Builds'i kurduktan sonra Worker derlenir, `npm test` geçer, `/health` 200 döner ve uyumluluk testleri dağıtılmış Worker'a karşı da geçer (kullanıcıyla birlikte). ✔️ (yerelde; dağıtılmış Worker'a karşı kısım kullanıcının Workers Builds kurulumundan sonra yapılacak)

### Aşama 13 — Bağlayıcı Uygulama (Bağlayıcı/Güvenlik Mühendisi)
Teslimat: `pixel_connector/` (`__main__`, `relay`, `crypto`, `session`, `peers`, `config`, `workspaces`, `agents`, `tasks`, `engines/*`, `runtimes`, `usage`, `approvals`, `audit`, `service`), `web/js/e2e.js`, `web/js/idb.js`, `tools/build_connector.py`, `web/connector/{install.sh,install.ps1,pixel-connector.pyz,SHA256SUMS}`, `tests/test_crypto_vectors.py` + `tests/vectors/`, `tests/test_e2e_protocol.py`, `tests/test_connector_*.py`, `tests/fixtures/claude_stream_*.jsonl`, `tests/test_v3_browser.py` (V3.md §5–§10, §12, §17.2).
- [x] `crypto.py`: P-256 ECDH (nokta doğrulaması), HKDF, AES-256-GCM, PBKDF2 sarmalayıcı; RFC 5903, NIST CAVP, RFC 5869, RFC 7914, NIST GCM ve Wycheproof vektörleri
- [x] `e2e.js`: tarayıcı anahtarı (dışa aktarılamaz, IndexedDB), eşleştirme (PBKDF2, MAC, SAS), el sıkışma, `Channel.seal/open`, dolgu
- [x] Python ↔ WebCrypto çapraz testleri (headless Chrome, CDP) ve uçtan uca eşleştirme + el sıkışma
- [x] `session.py`: eşleştirme durum makinesi (tek deneme, 10 dk), terminalde SAS onayı, kanallar, seq/tekrar denetimi, 3 hatada kanalı kapatma
- [x] `config.py` + `workspaces.py`: şema, yasak klasörler, argv kuralları, `bypassPermissions` yasağı, `config-check`
- [x] `engines/claude.py`: argv kurucu (istem stdin'den), sürüm ve yetenek tespiti, `auth status`, stream-json ayrıştırma, oturum sürdürme, SIGINT → SIGTERM → SIGKILL ile iptal, `PIXEL_OFFICE_DISABLE=1`
- [x] `engines/ollama.py`, `engines/openai_compat.py`: akış, son token sayıları, geçmiş dosyası, bağlantıyı kapatarak iptal
- [x] `runtimes.py`: izin listesindeki argv, durum yoklaması, Ollama `/api/ps`, model yükleme ve boşaltma
- [x] `usage.py`: SQLite defteri, devam ettirilen oturumda fark alma, dönemler, ağırlıklı token ölçüsü, bütçe durumları, `--max-budget-usd` sınırlaması
- [x] `tasks.py`: ajan başına sıralama, cihaz eşzamanlılığı, hız limitleri, zaman aşımı; `agents.py`: ofis durumu (`ctl`, 20 dk keep-alive)
- [x] `approvals.py` (127.0.0.1 onay sayfası + CLI + bildirim), `audit.py` (JSONL, döndürme), `peers` iptali
- [x] `service.py`: systemd kullanıcı birimi, launchd plist, schtasks; `install-service`/`uninstall-service`/`doctor`
- [x] `build_connector.py`, kurulum betikleri (SHA-256 doğrulaması), CLI çıktıları Türkçe
- [x] Gizlilik kanarya testi: istem ve çıktı hiçbir röle çerçevesinde, `/api/status` gövdesinde ya da günlükte açık görünmez

**Kabul kriterleri:** Bağlayıcı yalnızca stdlib kullanır (`ast` taramasıyla doğrulanır) ve Python 3.8 sözdizimiyle uyumludur. Tüm kripto vektörleri ve çapraz testler geçer. Röle MITM simülasyonu, yanlış kod, tekrar, sıra dışı ve kurcalanmış mesajlar reddedilir. Sahte `claude` ile her izin profili doğru argv'yi üretir. argv'de hiçbir zaman istem ya da `--dangerously-skip-permissions` yoktur. Klasör kaçışı (`..`, sembolik bağ) reddedilir. Çalışma ortamı isteğindeki hiçbir alan argv'ye ulaşmaz. İptal 15 sn içinde süreci sonlandırır. Bütçe `block` iken yeni görev reddedilir. Bu makinede `dev_server --relay` + bağlayıcı + headless Chrome ile eşleştirme ve sahte motorla görev akışı baştan sona çalışır. Gerçek `~/.claude` ve `~/.pixel-office` dizinlerine testlerde dokunulmaz. ✔️ (gerçek Claude Code ve gerçek Ollama ile deneme Aşama 15'te, kullanıcıyla)

### Aşama 14 — Arayüz (Frontend Mühendisi)
Teslimat: `web/js/control.js`, `prompt.js`, `devices.js`, `usage.js`; `main.js`, `ui.js`, `cloud.js`, `index.html`, `style.css`, `sw.js` (VERSION artırımı) düzenlemeleri; `vercel.json` CSP ve `/connector/*` başlıkları (V3.md §11, §13.5, §17.3).
- [x] `DeviceHub`: varlık (presence), cihaz başına kanal, istek/yanıt zaman aşımları, görev akışları, `agent.watch`
- [x] Ayrıntılar panelinde görev kutusu: gönder (Ctrl+Enter), "Yeni sohbet", izin profili rozeti, akan çıktı (yalnızca `textContent` ile çalışan markdown-lite), araç satırları, Durdur, "Son görevler"
- [x] Canlı token sayacı, ajan ve cihaz için günlük toplamlar, bütçe göstergesi (yalnızca bütçe varsa yüzde), dürüstlük açıklamaları, `tr-TR` sayı biçimi
- [x] "Yeni ajan" penceresi: cihaz, çalışma klasörü ve alt klasör, motor, model, ad, izin profili, bütçe
- [x] "Cihazlar" sayfası: cihaz listesi, ajanlar, çalışma ortamları (başlat/durdur, model yükle/boşalt), eşleşen tarayıcılar ve iptal, 3 adımlı "Cihaz ekle" sihirbazı
- [x] Telefon düzeni: alt sekme çubuğu (Ofis · Ajanlar · Cihazlar), tam ekran ajan sayfası, altta sabit görev kutusu, geri dönüşte yeniden bağlanma ve `task.list`
- [x] Hata kodlarının Türkçe karşılıkları; çevrimdışı, eşleşmemiş ve meşgul durumları
- [x] v3 kapalıyken (röle yok ya da izleme bağlantısı) hiçbir v3 denetimi görünmez; yerel mod değişmez

**Kabul kriterleri:** Bağımlılık ve derleme adımı yok. `dev_server.py --demo --relay` ve gerçek bağlayıcı (sahte motorlar) ile headless Chrome'da şunlar çalışır: eşleştirme (doğru kod, yanlış kod, SAS reddi), ajan oluşturma, görev akışı, iptal, ikinci sekmeden izleme, çalışma ortamı denetimleri ve bütçe göstergesi. Çıktıdaki ve adlardaki XSS metinleri düz metin kalır. 390 ve 360 px genişlikte yatay kaydırma yoktur ve görev kutusu altta sabittir. Konsolda hata yoktur. `server.py --demo` ve v2 odası görsel ve davranışsal olarak değişmemiştir. Tüm testler geçer. ✔️

### Aşama 15 — Güvenlik İncelemesi & QA & Dokümantasyon (Güvenlik İnceleyici + QA Mühendisi)
- [x] V3.md §14'teki her tehdit satırının bir testle ya da yazılı bir notla doğrulanması; kripto kodunun satır satır incelenmesi (V3.md §20 "Phase 15 findings")
- [x] Yasaklı bayrak ve kabuk kullanımı taraması (argv kurucular, `shell=True` yok), stdlib dışı import taraması, dosya izinleri (0700/0600)
- [x] Uçtan uca (yerel): ayrı süreçlerle `dev_server.py --relay` + gerçek `python3 -m pixel_connector run` (geçici HOME) + sahte Ollama + sahte `claude` + headless Chrome; gizlilik kanaryası (röle çerçeveleri, Vercel durum gövdeleri, `/api/state`, bağlayıcı günlüğü, denetim günlüğü)
- [ ] Uçtan uca (dağıtılmış): Worker + Vercel + gerçek bağlayıcı; kota ölçümü — **yayına almadan sonra, kullanıcıyla** (kontrol listesi 9–12)
- [ ] Gerçek Claude Code (abonelik girişi) ile her profil, oturum sürdürme, araç sırasında iptal, maliyet etiketleri — **kullanıcıyla bekliyor**
- [ ] Gerçek Ollama: sohbet, model yükleme ve boşaltma, polkit kuralıyla başlatma ve durdurma — **kullanıcıyla bekliyor**
- [ ] Gerçek telefon (mobil veri): PWA, eşleştirme, Ollama'yı başlatma, görev, arka plandan dönüş — **kullanıcıyla bekliyor**
- [x] `README.md` (Türkçe + İngilizce özet): bağlayıcı kurulumu, eşleştirme, profiller, `connector.json`, servis, gizlilik ve güvenlik uyarıları, Anthropic notu, ücretsiz plan limitleri, sorun giderme, sınırlamalar, `.pyz` SHA-256 özeti
- [x] `web/rehber.html`: v3 adımı (eşleştir → ajan oluştur → görev ver, hareketli telefon maketi) ve SSS maddesi; 1280/390/360 px'te headless Chrome ekran görüntüsüyle doğrulandı
- [x] V3.md §20'ye bulgular ve sapmalar; `sw.js` sürümü `po-v3.0.1`; kullanıcının dağıtım listesinin son hâli (aşağıda ve README'de)
- [x] CSP röle adresi kararı: `tools/set_relay_host.py` (tek doğrulanmış komut, joker yok) + tarayıcıda CSP ihlali algılama

**Kabul kriterleri:** Tüm testler geçer. Kritik ya da yüksek önem derecesinde açık bulgu kalmaz. Kullanıcıyla yapılan gerçek denemelerin sonuçları günlüğe yazılır, yapılamayanlar listede açıkça işaretlenir. README'deki komutlar denenmiştir. İlk haftanın kullanım sayıları V3.md §15 ile karşılaştırılmıştır. ✔️ (kod tarafı; gerçek denemeler ve ilk hafta sayıları yayına almadan sonra kullanıcıyla)

### Aşama 16 — Siber Güvenlik Denetimi (Uygulama Güvenliği Mühendisi)
Teslimat: `docs/SECURITY_AUDIT.md` (İngilizce: kapsam, yöntem, bulgu tablosu, gizli bilgi taraması, canlı yapılandırma, öneriler, kullanıcı eylem listesi); düzeltmeler ve `tests/test_phase16_security.py`. Depo herkese açık olduğu için geçmiş de tarandı.
- [x] Gizli bilgi ve depo temizliği: `git log -p --all` taraması (token biçimleri, özel anahtarlar, `.env`, `PIXEL_SECRET`/`RELAY_SECRET`/Upstash değerleri, oda anahtarları, e-posta, telefon, ev dizini yolları). Bu bilgisayardaki gerçek değerler geçmişle maskeli karşılaştırıldı, her eski `.pyz` açılıp incelendi; `.gitignore`/`.vercelignore` gözden geçirildi; canlı sitede `/.git/`, `/README.md`, `/tests/`, `/pixel_cloud/`, `/api/*.py`, `/.env`, `/relay/`, `/docs/`, `/PROJECT.md` → 404
- [x] Canlı yapılandırma (salt okunur, ≈ 60 istek): güvenlik başlıkları, TLS, her `/api/*` ucu (kimliksiz, yanlış anahtar, büyük gövde, yanlış yöntem), röle `/health` ve biletsiz, yanlış Origin'li ve URL'de biletli WS yükseltmeleri
- [x] Kod denetimi (risk sırasıyla): uzaktan komut çalıştırma (bağlayıcı komutları, eşleştirme, `/onay`, çalışma klasörü ve ortam izin listeleri, Claude izin profilleri, argv ve ortam değişkenleri), sunucu (HMAC, yazma anahtarı, bilet, zamanlama, enjeksiyon, yol geçişi, SSRF, hız sınırı, kota tüketme, hata mesajları), istemci (XSS, SW önbelleği, anahtar saklama, CSP), tedarik zinciri, kripto kullanımı, gizlilik sözü
- [x] S16-01 (Yüksek): Claude Code'a her profilde, düzenlenince kod çalıştıran dosyalar için yazma yasakları (`.git/`, `.claude/settings*.json`, `.mcp.json`, `.vscode/`, `.envrc`, `~/.claude`, `~/.config`, `~/.local/bin`, `~/.local/share`, kabuk başlangıç dosyaları) ve ek kimlik bilgisi okuma yasakları
- [x] S16-02 (Orta): v1 yerel sunucuda DNS rebinding'e karşı Host denetimi (`421`; ek adlar için `PIXEL_OFFICE_ALLOWED_HOSTS`)
- [x] S16-03 (Orta): kimliksiz `/api/state` için depolamadan önce IP başına hız sınırı; `/api/health` PING sonucunu 15 sn yeniden kullanır (Upstash/Vercel ücretsiz kotası korunur)
- [x] S16-05 (Orta): v2 kanca kurulum betikleri: yarım inen betik hiçbir şey çalıştırmaz; yalnızca `https://` (ya da yerel ağdaki sunucu için `http://`), `install.ps1` dahil
- [x] S16-06 (Orta): ev dizininin üst klasörü (`/home`, `C:\Users`) çalışma klasörü olamaz
- [x] Düşük/Bilgi: sondaki satır sonu kabul eden `$` düzenli ifadeleri → `\Z` (canlıda doğrulandı), CSP'ye `object-src 'none'; form-action 'self'` ve `Permissions-Policy`, belgede gerçek oturum kimliği yerine örnek kimlik, `.gitignore` eklemeleri; kabul edilenlerin gerekçeleri yazıldı (S16-07, -09, -12…-18)
- [x] Bağlayıcı yeniden derlendi: `build_connector.py --check` geçer, `SHA256SUMS` ve README özeti eşleşir; `sw.js` sürümü `po-v3.0.2`
- [x] Aşama 17 için güvenlik kısıtları yazıldı (SECURITY_AUDIT.md §5)

**Kabul kriterleri:** Tüm testler geçer (481, 1 atlandı: `pwsh` yok). Kritik ya da yüksek önem derecesinde açık bulgu kalmaz; her düzeltmenin bir regresyon testi vardır. Herkese açık geçmişte döndürülmesi gereken gizli bilgi yoktur (varsa kullanıcı listesinde açıkça yazılır). Canlı denetimler salt okunurdur (oda oluşturma, kaba kuvvet, yük testi yok). Kabul edilen ve kullanıcıya kalan maddeler gerekçeleriyle belgededir. ✔️

### Aşama 17 — Çalışma klasörü kurulum düzeltmesi (Bağlayıcı Mühendisi) ✅
Sorun: yeni kurulan bir bağlayıcıda hiç çalışma klasörü olmadığı için Claude Code ajanı oluşturulamıyor; kullanıcı `connector.json` dosyasını elle düzenlemek zorunda kalıyor. Ayrıca `doctor`, CDN önbelleğinden gelen yanıtın `Date` başlığıyla saat farkı hesapladığı için yanlış alarm veriyor.
- [x] Kurulum betikleri (`install.sh`, `install.ps1`) `~/PixelOfficeIsler` klasörünü oluşturur ve varsayılan çalışma klasörü olarak ekler (zaten varsa yeniden kullanır)
- [x] `pixel-connector workspace add|list|remove` komutu (yalnızca yerel komut satırı)
- [x] Cihazda hiç çalışma klasörü yoksa arayüzde açık bir ipucu (ne yapılacağı ve yerel komut)
- [x] `doctor` saat farkı yanlış alarmı: `no-store` bir uçtan ölçmek ya da `Age` başlığını çıkarmak
- [x] Testler; bağlayıcı yeniden derlenir, `SHA256SUMS` ve README özeti güncellenir; `sw.js` sürümü artırılır (web dosyaları değişirse; `web/js/devices.js` değişti, sürümü koordinatör birleştirmede artırır)

**Güvenlik kısıtları (Aşama 16'dan, ayrıntı: `docs/SECURITY_AUDIT.md` §5):** klasör `mkdir` ile 0700 oluşturulur, sembolik bağ ya da dosya ise reddedilir, `realpath` ile kaydedilir; ev dizini, üst klasörleri ve var olan depolar varsayılan olamaz. `workspace add` yalnızca yerel komut satırından çalışır: E2E komutu ya da `/onay` sayfa jetonuyla ulaşılabilen bir `/ctl` yolu olamaz, yani tarayıcı izin listesini genişletemez. `check_denied()` ve `realpath` kuralları aynen uygulanır, `connector.json` atomik ve 0600 yazılır. Arayüz ipucu tarayıcıdan yol almaz, metinler yalnızca `textContent` ile gösterilir. `EXEC_SENSITIVE_DENY`/`SECRET_READ_DENY` değişmez, kurulum `~/.claude`'a dokunmaz. Bilet `iat` toleransı (30 sn) saat farkını gizlemek için genişletilmez.

**Kabul kriterleri:** Temiz bir geçici HOME'da kurulumdan sonra `~/PixelOfficeIsler` vardır ve `config-check` geçer. Arayüzden "Yeni ajan" ile Claude Code ajanı elle düzenleme yapmadan oluşturulabilir. `workspace add` yasaklı yolları (ev dizini, üst klasörleri, `~/.ssh`…) reddeder. `doctor` CDN önbelleğindeki eski bir `Date` başlığıyla alarm vermez. Tüm testler geçer.

### Aşama 18 — Antigravity bağlantısı (Entegrasyon Mühendisi) ✅
Sorun: Antigravity IDE'nin kanca (hook) sistemi yok, bu yüzden ofiste görünmüyor. Ama her konuşmanın adımlarını `~/.gemini/antigravity-ide/brain/<konuşma>/.system_generated/logs/transcript.jsonl` dosyasına yazıyor (her satırda `step_index`, `type`, `status` RUNNING/DONE, `created_at`, araç çağrılarında `Cwd`).
- [x] `pixel_agent.py antigravity` komutu: kayıt dosyalarını izler (yalnızca sona eklenen satırlar), her konuşmayı bir ajan olarak raporlar: adım geliyorsa `working` (araç adıyla), model yanıtı ve sessizlikte `idle`, kullanıcı girdisi bekleniyorsa `waiting_for_user`, uzun sessizlikte `sleeping`
- [x] Proje adı yalnızca `Cwd`'nin son klasör adı; ajan adı ayarlanabilir (varsayılan "Antigravity")
- [x] `pixel-antigravity` kısayolu (`install-shims`) ve isteğe bağlı systemd kullanıcı servisi / Windows ve macOS yolları
- [x] Antigravity'nin `agentapi` aracının dışarıdan komut ya da onay kabul edip etmediği araştırılır (Aşama 19 için)
- [x] Belgeler (`docs/INTEGRATIONS.md`, rehber, bulut sayfasındaki örnek komutlar) ve testler

**Gizlilik kısıtları:** konuşma metni, düşünce, komut satırı, dosya yolu, araç argümanları ve dosya içeriği **asla** gönderilmez; yalnızca durum, araç adı (izin listesindeki biçimde), proje klasörünün adı, ajan adı ve sahip. Kayıt dosyaları salt okunur açılır; Antigravity dosyalarına yazılmaz. Testler gerçek `~/.gemini` klasörünü değil geçici klasörleri kullanır.

**Kabul kriterleri:** Antigravity'de bir konuşma çalışırken ofiste karakter yazı yazar, durunca kahve içer, uzun süre dokunulmayınca uyur. Gönderilen her güncelleme gizlilik testinden geçer. Tüm testler geçer.

### Aşama 19 — Ofisten kontrol: uzaktan onay (Bağlayıcı/Güvenlik Mühendisi + Frontend) ✅
Amaç: bir ajan izin istediğinde ya da soru sorduğunda kullanıcı telefondan ofise girip **onaylayabilsin ya da reddedebilsin**. Bu her kullanıcı için kendi eşleşmiş cihazlarıyla ve desteklenen her ajan türü için geçerli olur.
- [x] Claude Code kancası: `PermissionRequest` → yerel bağlayıcı → E2E röle → eşleşmiş cihaz → `allow`/`deny`; yanıt yoksa ya da bağlayıcı kapalıysa bugünkü davranış (VS Code penceresi)
- [x] Claude Code seçmeli soruları (`AskUserQuestion`): seçenekler telefonda gösterilir, seçilen cevap ajana iletilir (Claude Code desteği önce doğrulanır)
- [x] Bağlayıcının çalıştırdığı ajanlar (Claude Code başsız modu): incelendi; bu ajanlar soru sormuyor (`dontAsk` + `--permission-prompts none`, profil dışı her şey reddediliyor). Telefona bağlamak izin profillerini genişletmek demek; kullanıcı kararına bırakıldı, `docs/V3.md` §6.9'da belgelendi
- [x] Diğer ajanlar (Antigravity, `pixel-run`): mümkün değil, nedeni belgelendi (`docs/INTEGRATIONS.md` §1 ve §6, `docs/V3.md` §6.9)
- [x] Arayüz: ajanın üstünde ve Cihazlar sayfasında onay kartı (araç + kısa özet, Onayla/Reddet), bilgisayardaki `/onay` sayfasında da aynı istek (önce gelen cevap geçerli)
- [x] Gerçek telefon testi (2026-10-22 13:41): eşleşmiş Android telefondan `approval.request` → `approval.approve` ~5 sn'de, araç çalıştı ve dosya oluştu

**Not (karar):** Bağlayıcının kendi çalıştırdığı Claude Code ajanları bilerek soru sormaz ve telefona onay göndermez; sınır izin profilleridir (`readonly`/`edit`/`dev`/`auto`). Kullanıcı ileride isterse yeniden değerlendirilir.

**Güvenlik kısıtları:** varsayılan **kapalı** (`pixel-connector remote-approvals on` ile yalnızca yerel komut satırından açılır). Yalnızca eşleşmiş ve kumanda yetkisi olan cihazlar görür ve cevaplar; oda bağlantısını bilen başkaları, sunucu ve röle içeriği göremez. Her istek tek kullanımlık kimlik ve süre sınırıyla bağlanır (tekrar oynatma yok). "Hepsine izin ver" yok. Her karar denetim kaydına yazılır. Belirsizlikte reddetmek değil, bilgisayardaki normal pencereye düşmek (fail safe: kendiliğinden izin verilmez).

**Kabul kriterleri:** VS Code'da çalışan bir Claude Code oturumunun izin isteği telefondan onaylanınca araç çalışır, reddedilince çalışmaz; özellik kapalıyken ya da telefon cevap vermezken davranış eskisiyle aynıdır. Eşleşmemiş bir tarayıcı isteği göremez. Tüm testler geçer.

### Aşama 20 — Kalan görevler (Koordinatör + uzmanlar) ✅
Genel inceleme raporu: [`v2.md`](v2.md) (2026-10-21). Paketler sırayla, her biri ayrı bir uzman ajanla yürütülür:
- [x] Paket 1 — Güvenlik (v2.md: H1, H7, H8, H13, H30)
- [x] Paket 2 — Bağlayıcı sağlamlığı (E1, H4–H6, H9, H11, H24–H29, H32, E5)
- [x] Paket 3 — Arayüz hataları (H2, E4, H3, H14–H16, H33–H35, E3; E2'nin testleri de burada yazıldı)
- [x] Paket 4 — Aşama 18 (Antigravity) yarım işin tamamlanması
- [x] Paket 5 — Aşama 19 (ofisten onay)
- [x] Paket 6 — Bulut temizliği (Antigravity A11) (F1, H12, H17, H19–H23, D5, D9, F3)
- [x] Paket 7 — Windows/macOS (H10, H31)
- [x] Paket 8 — Belgeler ve testler (D1–D4, D7, D8, E6–E7, F4–F6, H36, E9, Antigravity v4 belgesi; E2 Paket 3'te yapıldı) — 8a Claude (`39cfb27`), 8b Antigravity A12
- [x] Kapanış (2026-10-22): H38 (eskiyen Claude Code yolu çalışırken yenileniyor, bağlayıcı 3.2.3), röle özel alan adı `wss://relay.pixeloffice.com.tr` (`cbeb4e8`) ve belgeleri, Aşama 19 gerçek telefon testi
- [x] Son güvenlik taraması S23 (2026-10-23, `docs/SECURITY_AUDIT.md` §11)
- [ ] Kullanıcıyla: push, bağlayıcı güncellemesi (iki bilgisayar ve arkadaşın bilgisayarı, 3.2.5; uzaktan onay kullananlarda kanca da); `relay/package-lock.json` yalnızca onayla (indirme gerekir)

Kullanıcı kararları (2026-10-21): `tanitim.html` ana sayfa yapılmaz, olduğu gibi kalır; kullanılmayan A5 parçalarına (zoom, ek sesler) dokunulmaz.

### Yayına alma kontrol listesi (v3)

Yayına alma adımlarının tek ve güncel kontrol listesi [`docs/YAYIN.md`](docs/YAYIN.md) belgesinde toplanmıştır:

👉 **[`docs/YAYIN.md`](docs/YAYIN.md)**

**Kullanıcının elle yapması gerekenler (v3)** (ayrıntılar: V3.md §16, [`docs/YAYIN.md`](docs/YAYIN.md)):
- [x] Cloudflare hesabı açmak (ücretsiz, kart gerekmez) ve `workers.dev` alt alan adını seçmek
- [ ] Aşama 12'den sonra: Workers & Pages → Import a repository → GitHub (yalnızca `timuryurdakan/Pixel` deposuna izin). Ad `pixel-relay`, kök dizin `relay`, derleme komutu `npm install --no-audit --no-fund && npm test`, dağıtım komutu `npx wrangler deploy`, izlenen yol `relay/*`
- [ ] `RELAY_SECRET` üretmek (`python3 -c "import secrets;print(secrets.token_urlsafe(32))"`) ve **aynı değeri** hem Cloudflare'de (Secret) hem Vercel'de girmek; Vercel'e `PIXEL_RELAY_URL=wss://pixel-relay.<alt-alan>.workers.dev` (veya `wss://relay.pixeloffice.com.tr`) eklemek
- [ ] `python3 tools/set_relay_host.py <alt-alan>` ile CSP'deki röle adresini ayarlamak, push etmek, `/health` ve `/api/config` kontrolü, uyumluluk testlerini dağıtılmış Worker'a karşı çalıştırmak (sıralı liste: [`docs/YAYIN.md`](docs/YAYIN.md))
- [ ] Her bilgisayarda: Cihazlar → Cihaz ekle → kurulum komutu → `pixel-connector pair` → kodu tarayıcıya girmek → SAS'ı karşılaştırıp `e` demek (telefon için ayrı kod)
- [ ] İsteğe bağlı: `loginctl enable-linger $USER`, Ollama için polkit kuralı, `connector.json` içinde çalışma klasörleri ve ortamlar
- [x] Yerleşik ajanlar hâlâ odadaysa kaldırmak (`/api/residents` ile `[]`)
- [ ] İlk ay haftada bir Cloudflare (Durable Objects istek ve süre), Vercel ve Upstash kullanımını kontrol etmek
- [ ] Girişim aşamasından önce: Anthropic koşulları sorusunu netleştirmek (V3.md §18.1, Q1) ve API anahtarıyla kimlik doğrulamaya geçmeyi değerlendirmek

## 6. İlerleme Günlüğü

Önceki tüm geliştirme ve aşama kayıtları günlüğe arşivlenmiştir:
👉 **[`docs/gunluk/2026-10.md`](docs/gunluk/2026-10.md)** (Aşama 1–17, A1–A5, Paket 1–4, Paket 6 / A11).

Aşağıda yalnızca son 5 kayıt tutulmaktadır (her yeni kayıt en alta eklenir):

- **2026-10-26 — Patron Odası P4: Patron ve odasının çizimleri (Pixel Art Uzmanı, Claude):** `web/js/sprites.js`'e yalnızca ekleme. Patron karakteri (16 × 24, kendi sabit paleti: koyu lacivert takım, beyaz gömlek, kırmızı kravat, gri şakaklar ve bıyık; `getBossFrame`, `BOSS_ANIMS`): `boss_sit` (1), `boss_write` (2), `boss_read` (2, gazete), `boss_give` (1, dosya uzatır), `boss_stand` ve `boss_walk` (4 yön, 4 kare; ajanların kare düzeni). Oturan kareler belden yukarı: koltuk → masa → Patron sırasıyla çizilince elleri masa üstünde görünür (P5 için §3.3). Mobilya: `boss_desk` (48 × 24, maun, yeşil sümen, banker lambası) + `getBossDeskSprite(0–5)` / `boss_desk_files_1..5`, `boss_chair`, `file_cabinet`, `sofa` (32 × 16), `boss_sign` (16 × 8, kravat simgeli pirinç levha). Karolar: `boss_floor` (yeşil halı), iç kapı `door_side` (dikey duvar) ve `door_front` (yatay duvar); kapı duvarını P5 seçer. Balon: `folder` (`BUBBLE_KINDS` sonuna). Mevcut çizim, palet ve adlar değişmedi; yeni adlar listelerin sonuna eklendi. Önizleme sayfasında Patron kareleri ajanların yanında, masa çeşitleri ve örnek oda; başsız Chrome ekran görüntüleriyle tarz denetlendi (çekmece kulpları, levha kravatı, dolap çekmeceleri düzeltildi). Sapmalar [`docs/PATRON_ODASI.md`](docs/PATRON_ODASI.md) §11. Yeni `tests/js/boss_sprites.test.js` (7 test; boyutlar, kare sayıları, palet, masa çeşitleri, `BUBBLE_KINDS`, `validateSprites()` boş); tüm paket 946 test, OK (1 atlandı). Web `po-v4.0.3`. Commit/push yapılmadı.
- **2026-10-29 — Patron Odası P5: ofis haritası ve sahne (Frontend/Oyun Mühendisi, Claude):** 27 Ekim'de oturum limitinde yarım kalan WIP (`cf6d1ef`) tamamlandı ve doğrulandı. Harita 22 × 14 kaldı: sağ üstte duvarlı Patron Odası (dosya dolapları, Patron koltuğu ve masası, misafir kanepesi, yeşil halı; kapı alt duvarda (17,6), üstünde levha), kahve tezgâhı ve su sebili sol üste, Görev Tahtası (9–11,1) ortaya, kedi dinlenme odasına (18,11); kitaplıklar ve üst saksılar kaldırıldı, kahve/uyku taşma noktaları yeniden seçildi. Odada normal ajana nokta yok (taşma dahil). Yeni `web/js/boss_scene.js`: Patron tarayıcıda çizilen sahne karakteri (buluta gitmez, etiketsiz); izleyicide hep boşta (yazar ↔ gazete), eşleşmiş tarayıcıda `boss.state`'e göre planlıyor/onay ("!" + dosyalar)/çalışıyor/değerlendiriyor; `boss.event` `take`/`deliver` → uzman masanın önündeki teslim karesine `folder` balonuyla yürür, Patron dosyayı uzatır, uzman yerine döner (`agents.js` `startErrandTo`/`queueErrandTo`; A9 handoff aynı). Azaltılmış harekette yalnızca balonlar. Mini pencere aynı sahne. Sapmalar ve gerçek harita `docs/PATRON_ODASI.md` §11 P5. Testler: yeni `tests/js/boss_scene.test.js` (12; yol bulma, oda, poz, yürüyüş, olay doğrulama); 1280/390/360 px ve mini pencere ekran görüntüleri, konsol hatası yok. Bu makinede format sonrası Chrome yok; tarayıcı testleri Brave ile çalıştırıldı. Tüm paket 946 test: 2 hata yalnızca `docs/claude-hafiza/` yedeğindeki kişisel yollardan (P5 dışı), 1 atlandı (`pwsh`). Web `po-v4.0.4`. Push yapılmadı.
- **2026-10-29 — Patron Odası P6: Patron paneli ve Görev Tahtası (Frontend Mühendisi, Claude):** Yeni `web/js/boss.js`: Patrona tıklayınca (`renderer.js` `pickBoss`) "Patron Odası" paneli açılır. Eşleşmiş ve "boss" özellikli bağlı bilgisayarda: istek (≤ 4 KB), çalışma klasörü (`workspace.list`, alt klasör var olmalı), maliyet tavanı (bilgisayarın `boss.maxCostUsd` tavanından yukarı çıkamaz, 0,10 $ altı olmaz; aralık dışı sayfada reddedilir, kırpılmaz) ve "Patrona ver" (`boss.ask`); plan kartı (özet, iş emirleri, rol, kabul ölçütleri, dosyalar, "o1 bitince başlar", tahmini maliyet ve kesin tavan yan yana, kalan süre) ile Onayla/Reddet (`boss.approve`); `boss.state.orderStates`'ten ilerleme ve Test iş emrinden önce onay (`workflow.answer {run, decision, step}`); bitince rapor düz metin; İptal (`boss.cancel`); "Hafızayı sıfırla" panel içi onayla (`boss.reset`). Durum her bağlantıda `boss.get` ile geri gelir (sekme yenilenince plan kartı yeniden görünür); mesajlar izin listesiyle okunur, bozuk/bilinmeyen olanlar ve biten işin geç "etkin" durumu yok sayılır; tüm metinler `textContent`. Görev Tahtası'nda plan beklerken "Plan onayı · Siz" kartı (yalnızca sayılar; tıklayınca panel). İzleyici, eşleşmemiş tarayıcı ve özelliksiz/çevrimdışı cihaz: yalnızca bilgi metni, eylem yok; mini pencerede panel açılmaz. Uçuştaki istekte düğmeler kilitli, hata kodları Türkçe. Telefonda düğmeler ≥ 44 px, 360/390 px yatay kaydırma yok. Sapmalar `docs/PATRON_ODASI.md` §11 P6. Testler: yeni `tests/js/boss.test.js` (17) ve `tests/test_boss_ui.py` (gerçek sayfa ⇄ `dev_relay` ⇄ gerçek bağlayıcı, sahte `claude`: istek → plan → yeniden yükleme → onay → Test onayı → rapor → sıfırlama, XSS kanaryası, 1280/390/360 px, izleyici, mini pencere, konsol hatası yok); tüm paket 947 test, OK (1 atlandı: `pwsh`; tarayıcı testleri Brave ile). Web `po-v4.0.5`. Commit/push yapılmadı.
- **2026-10-29 — Patron Odası P7: QA ve belgeler (QA Mühendisi, Claude):** Sahte motorla gerçek sayfa ⇄ `dev_relay` ⇄ gerçek bağlayıcı ⇄ başsız tarayıcı üzerinden uçtan uca senaryolar (yeni `tests/test_boss_e2e.py`, P3'ün yönlendirmeli sahte `claude`'u ve zamanlama kaydı; mutlu yol P6 `test_boss_ui.py`). Sonuçlar: mutlu yol (istek → plan → onay → Belge → Test onayı → rapor) geçti; iki çakışmasız iş emri aynı anda (süreler örtüşüyor, panelde ikisi "Çalışıyor", değerlendirme sonra, tam rapor) geçti; plan reddi (`cancelled/denied`, uzman çalışmadı, rol ajanı oluşmadı) geçti; çalışırken İptal (görev kesildi, sonraki başlamadı, "Rapor (iş yarım kaldı)") geçti; maliyet tavanı (`error/cost_cap`, "Maliyet tavanı doldu", sonraki iş emri başlamadı) geçti; sayfa kendi eşleşmesini kaldırınca (`cancelled/revoked`, görev kesildi, panelde yalnızca bilgi metni, eylem yok) geçti; bilgisayarda `peers revoke` (`cancelled/revoked`, yenilemede el sıkışma reddi, eylem yok) geçti; Test onayı bilgisayarda verilince açık panel yenilemeden güncellendi, geçti. P6 açıkları: (a) yeniden üretilemedi (bağlayıcı her adım değişiminde `boss.state` gönderiyor; regresyon testi), (b) düzeltildi: Patron sahnesi paneldeki `BossStore`'dan okuyor, bağlantı başına tek `boss.get` (önce başarısız test: `tests/js/boss_shared_state.test.js`, uçtan uca sayaç). P8 notu: bilgisayarda yapılan eşleşme kaldırma tarayıcıya bildirilmiyor; sayfa yeniden bağlanana kadar eski paneli gösteriyor (istekler yok sayılır, kapalı başarısızlık). Belgeler: `README.md` "Patron Odası" (kullanım, tavanlar, abonelikte eşdeğer / API anahtarıyla gerçek ücret uyarısı), `web/rehber.html` 8. adım, `docs/V3.md` §20 P4–P7 özeti, `docs/PATRON_ODASI.md` §11 P7. Tüm paket 954 test, OK (1 atlandı: `pwsh`; tarayıcı testleri Brave ile). Web `po-v4.0.6`, bağlayıcı değişmedi (3.8.0). Commit/push yapılmadı.
- **2026-10-29 — Patron Odası P8: bağımsız güvenlik incelemesi (Uygulama Güvenliği Mühendisi, Claude):** P1–P7 saldırgan gözüyle incelendi (§5.1 T1–T14; plan doğrulaması, görev politikası ve alt ajan reddi, tavanlar, çitler ve karar bloğu, `--resume` hafızası, eşleşme kaldırma, gizlilik, arayüzde XSS). Sonuç [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md) §13: **1 Orta, 3 Düşük düzeltildi, 5 Bilgi kabul; açık Orta ve üzeri yok.** S29-04 (Orta): Patron ve rol ajanları devir hedefi olabiliyordu; Test rol ajanı böylece saklı `dev` profiliyle, cihazın geniş dev listesiyle, Test onayı ve `Agent`/`Task` reddi olmadan çalışıyordu → devir hedefi `_not_boss` ile ve merkezde `precheck`'te (politikasız görev yönetilen ajanda `not_allowed`) kapatıldı. S29-01: bilgisayarda onay bekleyen `boss.ask` tarayıcı kaldırıldıktan sonra onaylanırsa planlama başlıyordu → `launch` reddeder. S29-02 (P7 notu 3): bilgisayarda kaldırma artık sayfaya `peer.revoked` ile bildiriliyor, sayfa cihazı hemen unutuyor (açık panel yenilemeden bilgi metnine dönüyor). S29-03: Test onayı metni ve yerel onay kartı, test komutlarının uzmanların kodunu kullanıcının yetkileriyle ve tavanlar dışında çalıştırdığını açıkça söylüyor (§10/3 kararı aynı). Testler: yeni `tests/test_phase_boss_security.py` (10) ve `tests/js/boss_security.test.js` (3), her düzeltmeden önce başarısız görüldü; tüm paket TESTS_LINE (tarayıcı testleri Brave ile). Bağlayıcı 3.8.1 (`1263e5d7…`), web `po-v4.0.7`; kullanıcı push sonrası `pixel-connector update` çalıştırmalı. Gerçek Claude çalıştırılmadı. Commit/push yapılmadı.
