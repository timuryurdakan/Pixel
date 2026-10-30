# Faz 3 — Tasarım ve Güvenlik Belgesi (T0)

Durum: **taslak, kullanıcı onayı bekliyor** (2026-10-30, Claude). Onay gelmeden E3 / E4a / E4b kodu yazılmaz
(`docs/GOREV_PANOSU.md`). Kaynak öneri: `docs/EVRENSEL_PLATFORM_ARASTIRMA_RAPORU.md` §2.1–§2.2.

Faz 3 iki iş getiriyor:

- **E3:** Bağlayıcıdaki Claude dışı motorlar (`openai_compat.py`, `ollama.py`) bugün yalnızca sohbet ediyor. E3 ile
  dosya okuyup yazabilecekler.
- **E4:** Hiçbir şey kurmayan biri, kendi API anahtarıyla doğrudan tarayıcıda ajan çalıştırabilecek.

İkisi de projeye yeni bir güven sınırı ekliyor. E3'te bir yapay zekâ modeli, insan onayı olmadan bilgisayardaki
dosyalara erişiyor. E4'te ücretli bir API anahtarı web sayfasının içinde duruyor. Bu belge sınırları kodlamadan önce
sabitler.

## 1. Kararlar (onayınız gereken kısım)

| # | Karar | Önerim | Neden |
|---|---|---|---|
| D1 | Ajanlara web aracı (`web_fetch` / `web_search`) verilsin mi? | **Hayır, Faz 3'te yok** | Dosya okuyabilen bir model, web isteğiyle dosyaları dışarı sızdırabilir (`https://saldirgan/?veri=...`). Okunan bir dosyadaki gizli talimat bunu tetikleyebilir. Ayrıca bağlayıcıda SSRF riski açılır. İhtiyaç olursa ayrı bir güvenlik incelemesiyle ele alınır. |
| D2 | Patron'un uzmanları Claude dışı motorlarla çalışsın mı? | **Faz 3'te hayır.** Patron ve uzmanları Claude'da kalır; E3 motorları normal ajan, şablon ve devir işlerinde çalışır. | Patron'un tavan, onay ve rapor zinciri Claude'un ölçülmüş maliyetine dayanıyor. Diğer sağlayıcıların fiyatı bağlayıcıdan bilinemez (`costBasis: unknown`). |
| D3 | Tarayıcıda API anahtarı nerede saklansın? | **Varsayılan: yalnızca bellekte**, sekme kapanınca silinir. İsteğe bağlı **"Bu cihazda hatırla"** seçilirse IndexedDB'de, dışa aktarılamayan bir WebCrypto anahtarıyla (AES-GCM) şifreli tutulur. | Şifreleme diskteki kopyayı ve meraklı göz atmayı korur, sayfadaki bir XSS'i durdurmaz. Asıl koruma XSS'in olmamasıdır (§3.2). Bu yüzden "hatırla" bilinçli bir seçim olmalı. |
| D4 | Tarayıcıdan hangi sağlayıcılara bağlanılabilsin? | **Sabit liste:** Google Gemini, Groq, OpenRouter, OpenAI, DeepSeek, Mistral. Anthropic, tarayıcı erişimi için özel başlık gerektirdiği için şimdilik dışarıda. Serbest URL yok. | CSP `connect-src` yalnızca bu adreslere açılır. Serbest URL demek, her yere bağlanabilen bir sayfa demek. |
| D5 | Tarayıcıdan yerel Ollama'ya (`http://127.0.0.1:11434`) bağlanılsın mı? | **Hayır.** Yerel modeller bağlayıcı üzerinden kullanılır. | Bunun için Ollama'da CORS açmak gerekir (`OLLAMA_ORIGINS`), bu da yerel sunucuyu her siteye açmaya yol açabilir. Ayrıca CSP'ye `http://` eklemek gerekir. |
| D6 | Tarayıcı ajanları odadaki diğer kişilere görünsün mü? | **İlk sürümde hayır.** Yalnızca o tarayıcının ofisinde görünürler. | Odaya durum göndermek için oda yazma anahtarının o tarayıcıda olması ve yeni bir veri yolu gerekir. Görünürlük daha sonra yalnızca durum ve araç adıyla eklenebilir, metin gönderilmez. |
| D7 | Varsayılan tavanlar | Görev başına en fazla 20 tur, 40 araç çağrısı ve 8.000 çıkış jetonu. Saatte en fazla 30 görev (tarayıcı). Dosya okuma en fazla 256 KB, yazma en fazla 512 KB. Tarayıcıdaki sanal klasörde en fazla 200 dosya ve 20 MB. | Sağlayıcının fiyatı bilinmediği için para tavanı konamaz. Tavanlar tur, jeton ve sayı üzerinden konur. Sağlayıcı panelinde harcama limiti koymak kullanıcıya önerilir. |

## 2. E3 — Bağlayıcı motorlarına araç kullanma (Claude)

**Kapsam:** `pixel_connector/engines/{openai_compat,ollama}.py`, ortak yeni `engines/tools.py`, `config.py`
(sağlayıcı hazır ayarları), testler.

**Araçlar.** OpenAI `tools` şemasıyla tanımlanır, Ollama da aynı şemayı kabul eder.

| Araç | Profil | Not |
|---|---|---|
| `list_dir(path)` | readonly, edit, dev | Yalnızca çalışma klasörü içinde. En fazla 500 giriş. |
| `read_file(path)` | readonly, edit, dev | En fazla 256 KB. İkili dosyalar reddedilir. |
| `write_file(path, content)` | edit, dev | En fazla 512 KB. Gerekirse klasör oluşturur, var olan dosyanın üstüne yazar. |

**Bilerek olmayanlar:**
- Komut çalıştırma yok, `dev` profilde bile. Test komutu yalnızca Claude'un Test rolünde kalır.
- Web aracı yok (D1).
- Silme ve taşıma yok.
- `auto` profil bu motorlarda araç açmaz.

**Sınırlar:**
- Her yol `workspaces.resolve` ve S26-03'teki `O_NOFOLLOW` zinciriyle açılır. Sembolik bağ, `..` ve mutlak yol
  reddedilir.
- Claude için geçerli yasak liste aynen uygulanır: `.git/`, `.claude/`, `.mcp.json`, `.vscode/`, `.envrc`, kabuk
  başlangıç dosyaları ve benzerleri (README "yazmak yasaktır" listesi). Okumada `.env*`, anahtar ve kimlik dosyaları
  da yasaktır.
- Tavanlar D7'deki gibidir. Sınır aşılınca görev `max_turns` ya da `tool_limit` koduyla biter.

**Model çıktısı güvenilmezdir:**
- Araç argümanları JSON şemasına göre doğrulanır (izin listesi, bilinmeyen alan reddedilir).
- Araç sonuçları modele "veri" olarak, sabit çerçeve metniyle verilir.
- Dosya içeriğindeki bir talimat hiçbir zaman profil, klasör ya da tavanı değiştiremez, çünkü bunlar görev
  politikasından (`TaskPolicy`) gelir ve tarayıcıdan da modelden de gelmez.

**API anahtarları:**
- Bugünkü gibi yalnızca bilgisayardaki ortam değişkeninde durur (`apiKeyEnv`).
- `connector.json` dosyasına, denetim kaydına, tarayıcıya ya da röleye asla gitmez.
- Hazır ayarlar yalnızca `https://` adres ve model önerisi getirir.

**Ofiste görünüm:** Araç adları `Read`, `Write` ve `LS` olarak gösterilir. Dosya yolu ve içerik gönderilmez,
mevcut gizlilik sözü korunur.

## 3. E4 — Tarayıcıda sanal cihaz (Antigravity: E4a, Claude: E4b)

### 3.1 Yapı

- **`web/js/browser_device.js` (E4a):** `DeviceHub`'a yerel bir cihaz gibi takılır. Röleye ve sunucuya hiçbir şey
  göndermez. Ajan oluşturur, görev çalıştırır, sağlayıcıya `fetch` ile akış (SSE) isteği yapar.
- **Sanal klasör:** Dosyalar bu tarayıcının IndexedDB'sinde tutulur. Araçlar (`list_dir`, `read_file`,
  `write_file`) yalnızca bu sanal klasörde çalışır, tavanlar D7'deki gibidir. Dosyalar tek tek `.md` ya da metin
  olarak indirilir, HTML yalnızca E2 önizlemesinde (izinsiz kum havuzu) gösterilir.
- **Anahtar saklama (E4b, Claude):** Yeni `web/js/keystore.js` (D3). E4a yalnızca
  `keystore.get(provider)` / `keystore.set(...)` arayüzünü kullanır, saklama ayrıntısına dokunmaz.

### 3.2 Anahtar için tehdit modeli

| Tehdit | Önlem |
|---|---|
| Sayfada XSS → anahtar çalınır | Asıl risk bu. Mevcut kural sürer: dinamik metin yalnızca `textContent` ile yazılır, model çıktısı yalnızca kum havuzunda gösterilir. E4b'de CSP'ye `require-trusted-types-for 'script'` eklenmesi denenir (kırılma olmazsa). Yeni `connect-src` adresleri yalnızca D4 listesidir. |
| Anahtar sunucumuza ya da röleye gider | Anahtar `DeviceHub` mesajlarına, `cloud.js` isteklerine, URL'ye, `localStorage`'a, loglara ve hata metinlerine hiçbir zaman girmez. Bunu kanarya anahtarla test eden bir test yazılır (E4b). |
| Diskteki tarayıcı profili kopyalanır | "Hatırla" kapalıyken diskte hiçbir şey yok. Açıksa şifreli, şifreleme anahtarı dışa aktarılamaz. |
| Ortak bilgisayar | "Hatırla" varsayılan olarak kapalı. "Anahtarı unut" düğmesi var. Ayarlarda anahtarın yalnızca son 4 hanesi gösterilir. |
| Anahtar başka cihaza ya da odaya taşınır | Eşleştirme, davet bağlantısı ve dışa aktarmada anahtar yok. |

### 3.3 CSP değişikliği (E4b, `vercel.json`, Claude)

`connect-src` şu şekle gelir:

```
connect-src 'self' wss://relay.pixeloffice.com.tr https://generativelanguage.googleapis.com https://api.groq.com https://openrouter.ai https://api.openai.com https://api.deepseek.com https://api.mistral.ai
```

Bedeli: bir XSS gerçekleşirse veri bu adreslere de gönderilebilir. Bu adreslerin hepsi kimlik doğrulamalı API'ler
olduğu için sızdırma kanalı olarak dar, ama sıfır değil. Bu yüzden XSS önlemleri (§3.2) E4'ün ön koşuludur.

### 3.4 Maliyet

- Para kullanıcının kendi anahtarından çıkar. Pixel Office'in sunucu maliyeti yine $0 olur, çünkü yapay zekâ trafiği
  Vercel, Upstash ve Cloudflare'den geçmez.
- Hazır ayarlarda ücretsiz katmanı olan modeller önce gelir.
- Arayüz, sağlayıcı panelinde harcama limiti koymayı önerir.
- Tavanlar D7'deki gibidir. Saatlik görev sınırı, yanlışlıkla bir döngüye girildiğinde zararı sınırlar.

## 4. İş bölümü ve teslim koşulları

| İş | Kim | Dosyalar | Teslim koşulu |
|---|---|---|---|
| E3 | Claude | `pixel_connector/engines/*`, `config.py`, testler | Yol kaçışı, sembolik bağ, yasak dosya, boyut ve sayı tavanı ile bilinmeyen alan için negatif testler. Bağlayıcı sürümü yükselir. |
| E4a | Antigravity | yeni `web/js/browser_device.js`, `tests/js/*` | `keystore.js` arayüzünü taklitle (stub) kullanır. `vercel.json`, `keystore.js`, `e2e.js` ve `cloud.js` dosyalarına dokunmaz. Model çıktısını yalnızca `textContent` ile ya da önizleme kum havuzunda gösterir. |
| E4b | Claude | yeni `web/js/keystore.js`, `vercel.json`, E4a incelemesi ve birleştirme | Kanarya anahtar testi geçer. CSP canlıda başlık olarak doğrulanır. `docs/SECURITY_AUDIT.md`'ye bölüm eklenir. |

E3 ve E4a paralel yürür. E4b, E4a teslim edildikten sonra yapılır.
