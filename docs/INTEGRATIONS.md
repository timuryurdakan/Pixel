# Pixel Office — Ajan Entegrasyonları

Bu belge, ajanları ofise bağlamanın tüm yollarını anlatır. Teknik sözleşme [`CLOUD.md`](CLOUD.md) §7, §16 ve §17'dedir.

Tüm istemciler aynı `POST /api/status` ucuna yazar. **Yerel sunucu** (`python3 server.py`, `http://127.0.0.1:8765`) ile **bulut odası** (`https://<host>`) arasındaki tek fark taban adrestir. Bulut ayrıca `?room=<oda>` ve `Authorization: Bearer <yazma anahtarı>` ister.

Dosyalar `web/hook/` altındadır. Bulutta bunlar `https://<host>/hook/...` adresinden düz metin olarak indirilebilir.

| Dosya | Ne işe yarar |
|---|---|
| `pixel_hook.py` | Claude Code kancası (Python ≥ 3.8): kurulum, kaldırma, test, durum CLI'ı, doctor |
| `pixel_hook.ps1` | Aynı istemcinin Windows PowerShell 5.1 ikizi. Python gerekmez. |
| `install.sh` / `install.ps1` | Tek satırlık kurulum betikleri |
| `pixel_agent.py` | Yerel yapay zeka ajanları için kütüphane, `report` CLI'ı, `pixel-run` ve `pixel-proxy` |

## Gizlilik (hepsi için geçerli)

Bilgisayarınızdan yalnızca şunlar çıkar:
- durum
- araç adı
- sabit Türkçe bir etiket
- proje klasörünün **adı** (tam yolu değil; takma ad verilebilir ya da gizlenebilir)
- sahip adı
- hash'lenmiş oturum kimlikleri

İstem, kod, araç girdisi ve çıktısı, Claude'un yanıtı, dosya yolları, ham oturum kimlikleri, komut satırı argümanları, program çıktısı ve model istek/yanıt gövdeleri **asla gönderilmez**. Bunlar diske de yazılmaz. Testlerde bu kurala uyulduğu bir "kanarya" metniyle doğrulanır.

---

## 1. Claude Code kancası (`pixel_hook.py`)

**Kurulum** (macOS/Linux). Komut "Ajan bağla" penceresinde hazır olarak gösterilir:
```sh
curl -fsSL https://<host>/hook/install.sh | sh -s -- --server https://<host> --room <oda> --key <anahtar> --name "Timur"
```
Kurulum şunları yapar:
- `~/.pixel-office/config.json` dosyasını yazar (izin 0600). Rastgele bir `ownerId` ve hash tuzu üretir.
- İstemciyi `~/.pixel-office/pixel_hook.py` konumuna kopyalar.
- `~/.claude/settings.json` dosyasına 13 olay için kanca ekler:
  - Önce dosyanın bir kez yedeğini alır (`settings.json.pixel-backup`).
  - Başka kancalara dokunmaz.
  - Tekrar çalıştırılabilir.
  - Dosya geçersiz JSON ise hiçbir şey yazmadan durur.
- Ofise bir "Kurulum testi" karakteri gönderir. Karakter 20 sn sonra çıkar.

Kurulumdan sonra açık Claude Code oturumlarını yeniden başlatın.

Diğer komutlar:
```sh
python3 ~/.pixel-office/pixel_hook.py doctor        # yapılandırma (anahtar maskeli), kancalar, günlük, sunucu erişimi
python3 ~/.pixel-office/pixel_hook.py test
python3 ~/.pixel-office/pixel_hook.py uninstall     # yalnızca bizim kancaları kaldırır
python3 ~/.pixel-office/pixel_hook.py status --session cursor --state working --tool Edit --project web
```

Kurulum seçenekleri:
- `--color 0-15`: gömlek rengi tercihi.
- `--settings-path P`: başka bir settings.json dosyasını düzenler.
- `--command-style shell`: kancayı `args` dizisi yerine tek bir komut satırı olarak yazar.
- `--no-test`: test karakterini göndermez.
- Yerel sunucu için `--room` ve `--key` gerekmez: `--server http://127.0.0.1:8765 --name Timur`. Yerel modda Claude Code oturumları zaten transcript'lerden görünür. Kanca bu yüzden genelde bulut içindir, yerelde ikinci bir karakter oluşturur.

**Ayarlar** (`config.json`):
- `"project_aliases": {"/tam/yol": "Takma ad"}`: alt klasörler de eşleşir.
- `"hide_projects": true`: proje adı yerine "Gizli proje" gönderilir.
- `"send_mcp_names": false`: MCP araçları "MCP" olarak görünür.
- `"enabled": false`: istemciyi kapatır.

Bir projeyi hiç bildirmemek için o klasöre (ya da bir üst klasörüne) boş bir `.pixel-office-ignore` dosyası koyun. Tek bir çalıştırmayı susturmak için `PIXEL_OFFICE_DISABLE=1` ortam değişkenini kullanın.

**Davranış.** İstemci her olayda en fazla birkaç saniye çalışır. Her zaman 0 ile çıkar ve ekrana hiçbir şey yazmaz. Kancalar `async` çalıştığı için Claude'u bekletmez. Yazma sayısı bilinçli olarak düşük tutulur:
- Durum değişiklikleri 1 sn bekletilip birleştirilir.
- Yalnızca araç değişirse en fazla 30 sn'de bir gönderilir (alt-ajanlarda 60 sn).
- Aynı durum 5 dakikada bir tazelenir.
- Tipik bir tur en fazla 4 yazma üretir.

Durum dosyaları `~/.pixel-office/state/` altında tutulur. Günlük `~/.pixel-office/hook.log` dosyasındadır; günlükte yalnızca olay adı ve HTTP kodu yer alır.

Testlerde kullanılan ortam değişkenleri `PIXEL_OFFICE_HOME` ve `PIXEL_OFFICE_CLAUDE_SETTINGS`'tir. Bunlarla gerçek `~/.claude` klasörüne hiç dokunulmaz.

## 2. Windows (`pixel_hook.ps1`, Python gerekmez)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm https://<host>/hook/install.ps1))) -Server https://<host> -Room <oda> -Key <anahtar> -Name 'Timur'"
```
`install.ps1` önce Python'u dener (`py -3` ya da `python` ≥ 3.8). Python bulunamazsa ya da `-ForcePowerShell` verilmişse PowerShell istemcisini kurar.

PowerShell istemcisi Python istemcisiyle aynı eşleme tablosunu, aynı durum dosyalarını ve aynı izin listesini kullanır. Yolları hem `/` hem `\` ile böler.

Komutlar:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File $env:USERPROFILE\.pixel-office\pixel_hook.ps1 doctor
...\pixel_hook.ps1 uninstall
...\pixel_hook.ps1 status -Session cursor -State working -Tool Edit
```

Not: Her olayda PowerShell'in açılması yaklaşık 0,3–0,6 sn sürer. Kanca `async` çalıştığı için bu Claude'u yavaşlatmaz. Şirket bilgisayarlarında PowerShell betikleri engellenmiş olabilir.

> ⚠️ PowerShell istemcisi bu makinede **çalıştırılmadı** (`pwsh` kurulu değil). Yalnızca statik olarak gözden geçirildi. `tests/hook_vectors.json` testi `pwsh` bulunduğunda otomatik çalışır.

## 3. Gelişmiş: HTTP kancası (son çare, açık onayla)

Ne Python ne de PowerShell çalıştırılabiliyorsa Claude Code'un `type: "http"` kancası doğrudan `POST /api/cc_hook?room=<oda>` adresine gönderilebilir. Gereken başlıklar: `Authorization: Bearer <anahtar>`, `X-Pixel-Owner: <ad>`.

**Bu yöntem istem ve kod içeriklerini sunucuya *gönderir*.** Sunucu aynı `map_event` işleviyle yalnızca izin verilen alanları alır, gerisini saklamaz ve günlüğe yazmaz. Ama veri bilgisayarınızdan çıkar. Ayrıca HTTP kancaları senkron çalışır, yani her araç çağrısı sunucuyu bekler.

---

## 4. Genel API: curl, JS, Python, PowerShell

```sh
# yerel (anahtarsız)
curl -X POST http://127.0.0.1:8765/api/status -H "Content-Type: application/json" \
  -d '{"session":"my-agent","name":"Kâtip","owner":"Timur","project":"notes","state":"working","tool":"llm","source":"custom"}'
# bulut
curl -X POST "https://<host>/api/status?room=<oda>" -H "Authorization: Bearer <anahtar>" -H "Content-Type: application/json" \
  -d '{"session":"my-agent","owner":"Timur","state":"sleeping","final":true}'
# kaldır (karakter kapıdan çıkar)
curl -X POST "https://<host>/api/status?room=<oda>" -H "Authorization: Bearer <anahtar>" -H "Content-Type: application/json" \
  -d '{"session":"my-agent","owner":"Timur","state":"offline"}'
```
```js
// Tarayıcı, Node 18+, Deno, Bun — bağımlılık yok
const PIXEL = {url: 'http://127.0.0.1:8765', room: null, key: null};   // bulut: {url:'https://<host>', room:'<oda>', key:'<anahtar>'}
export async function pixel(state, extra = {}) {
  const q = PIXEL.room ? `?room=${PIXEL.room}` : '';
  try {
    await fetch(`${PIXEL.url}/api/status${q}`, {method: 'POST',
      headers: {'Content-Type': 'application/json', ...(PIXEL.key ? {Authorization: `Bearer ${PIXEL.key}`} : {})},
      body: JSON.stringify({session: 'js-agent', owner: 'Timur', source: 'custom', state, ...extra})});
  } catch { /* raporlama ajanı asla bozmasın */ }
}
// await pixel('thinking'); await pixel('working', {tool: 'search', label: "Web'de arıyor"}); await pixel('idle');
```
Tarayıcıda başka bir kökenden çalışan sayfalar **yerel** sunucuyu çağıramaz (CSRF koruması). Bunlar bulutu ya da `pixel-proxy`'yi kullanmalıdır.

```powershell
Invoke-RestMethod -Method Post -Uri "https://<host>/api/status?room=<oda>" -Headers @{Authorization="Bearer <anahtar>"} `
  -ContentType "application/json" -Body '{"session":"cursor-1","owner":"Mert","project":"web","state":"working","source":"cursor"}'
```
Durumlar şunlardır: `working`, `thinking`, `waiting_for_user`, `idle`, `sleeping`, `offline`. İsteği yalnızca durum değiştiğinde gönderin; oturum başına en fazla 10 sn'de bir yeterlidir. İş bitince `offline` (hemen çıkar) ya da `"state":"sleeping","final":true` (yatağa gider, 5 dk sonra çıkar) gönderin.

---

## 5. Yerel yapay zeka ajanları (`pixel_agent.py`)

Hedef adres şu sırayla belirlenir:
1. Komut argümanları (`--url --room --key --owner`)
2. Ortam değişkenleri: `PIXEL_OFFICE_URL`, `PIXEL_OFFICE_ROOM`, `PIXEL_OFFICE_KEY`, `PIXEL_OFFICE_OWNER`
3. `~/.pixel-office/config.json` (kanca kurulumunun yazdığı dosya)
4. `http://127.0.0.1:8765`

Kısayolları kurmak için:
```sh
python3 pixel_agent.py install-shims      # ~/.pixel-office/bin/pixel-run ve pixel-proxy (Windows'ta .cmd)
```

### 5.1 Kütüphane
```python
from pixel_agent import Agent
bot = Agent("research-bot", project="notes")
bot.report("thinking")                                  # hiç hata fırlatmaz, bloklamaz
with bot.working(tool="llm", label="Özetliyor"):        # çıkışta idle; istisnada idle + "Hata" (metin gönderilmez)
    summarize()
@bot.task(tool="embed")
def embed_all(): ...
bot.waiting("Onay bekliyor")
bot.done()                                              # uyku + final (5 dk sonra çıkar)
```
Gönderimler arka plandaki tek bir iş parçacığından yapılır:
- **Yerel hedef:** 0,3 sn birleştirme.
- **Bulut hedefi:** Durum grubu değişikliği (düşünüyor/çalışıyor ↔ bekliyor ↔ boşta) 1 sn içinde gönderilir. Grup içindeki değişiklikler en fazla 30 sn'de bir gider. Aynı anda gönderilmesi gereken güncellemeler tek istekte (≤ 10) toplanır.

Tek seferlik CLI:
```sh
python3 pixel_agent.py report --name aider --state working --tool Edit --label "Düzenliyor"
```

### 5.2 `pixel-run`: herhangi bir komutu sarmalar
```sh
pixel-run --name aider --project myapp -- aider --model ollama/qwen2.5-coder
pixel-run --name interpreter -- interpreter --local
pixel-run --name nightly --idle-after 30 --on-exit offline -- ./train.sh
```
- **POSIX:** Komut bir sözde terminalde (pty) çalışır, böylece aider ve Open Interpreter gibi interaktif programlar normal çalışır. Pencere boyutu aktarılır. Ctrl+C ve SIGTERM/SIGHUP alt sürece iletilir. Sarmalayıcı alt sürecin çıkış kodunu döndürür; süreç bir sinyalle öldürüldüyse 128+N döner.
- Çıktı geldikçe `working`, `--idle-after` saniye sessizlikte `idle` gösterilir (`--quiet-state waiting_for_user` da seçilebilir). Çıkışta `sleeping` + "Bitti" ya da "Hata: kod N" gönderilir; `--on-exit offline` ile karakter hemen çıkar.
- Çıktının yalnızca bayt sayısı ölçülür. İçeriği okunmaz ve gönderilmez.
- Çıktı bir terminale gitmiyorsa (CI, boru) otomatik olarak boru moduna geçilir.
- **Windows:** pty olmadığı için program konsolu doğrudan kullanır ve süreç boyunca `working` görünür. `--pipe` seçeneği boşta algılamayı açar ama interaktif programları bozar.

### 5.3 `pixel-proxy`: OpenAI uyumlu / Ollama ara sunucusu
İstekleri ve yanıtları bayt bayt aynen iletir. SSE ve NDJSON akışları geldikçe aktarılır.
- Üretim isteği sürerken `thinking`.
- Yanıtta `tool_calls` varsa `working` ve araç adı.
- Yoksa `idle`.
- Hata durumunda `idle` ve "LLM hatası".

Model adı varsayılan olarak gönderilmez. `--send-model` ile etikete yazılır.

Ajan kimliği şu sırayla belirlenir:
1. `X-Pixel-Agent: <ad>` başlığı
2. `/agent/<ad>/...` yol öneki (önek sunucuya iletilmeden önce silinir)
3. `--name`

Kurulum satırları:

| Araç | Proxy | Ajanı proxy'ye yönlendirme |
|---|---|---|
| **Ollama** | `pixel-proxy --listen 127.0.0.1:11435 --upstream http://127.0.0.1:11434 --name ollama` | `OLLAMA_HOST=http://127.0.0.1:11435`; OpenAI SDK için `base_url="http://127.0.0.1:11435/v1"` |
| **LM Studio** | `pixel-proxy --listen 127.0.0.1:1235 --upstream http://127.0.0.1:1234 --name lmstudio` | `base_url="http://127.0.0.1:1235/v1"` |
| **llama.cpp** (`llama-server`) | `pixel-proxy --listen 127.0.0.1:8081 --upstream http://127.0.0.1:8080 --name llamacpp` | `base_url="http://127.0.0.1:8081/v1"` (ya da `/completion`) |
| **aider** | (yukarıdaki proxy'lerden biri) | `OLLAMA_API_BASE=http://127.0.0.1:11435/agent/aider aider --model ollama/qwen2.5-coder` veya `pixel-run --name aider -- aider …` |
| **Open Interpreter** | (yukarıdaki proxy'lerden biri) | `interpreter --api_base http://127.0.0.1:11435/agent/interpreter/v1 --model openai/qwen2.5-coder` veya `pixel-run --name interpreter -- interpreter --local` |

Proxy varsayılan olarak yalnızca `127.0.0.1` adresinde dinler. Başka bir adreste dinlemesi için `--allow-remote` gerekir.

Bilinen sınırlar:
- Proxy, istemciye yanıtı `Transfer-Encoding: chunked` ile yeniden çerçeveler. Gövde baytları değişmez.
- `gzip` dışındaki sıkıştırmalarda (ör. `br`) `tool_calls` algılanamaz. Akış yine sorunsuz iletilir.

---

## 6. Sorun giderme
- **Karakter görünmüyor:**
  - `pixel_hook.py doctor` çalıştırın. Sunucuya ulaşılabildiğini ve kancalı olay sayısının 13/13 olduğunu kontrol edin.
  - Claude Code oturumunu yeniden başlattığınızdan emin olun.
- **`hook.log`'da 401:** Yazma anahtarı yanlış. Kurulum komutunu davet bağlantısından yeniden alın.
- **`hook.log`'da `neterr`:** Sunucuya ulaşılamıyor. Bağlantıyı ve sunucu adresini kontrol edin.
- **Kancayı kaldırmak:** `pixel_hook.py uninstall` yalnızca Pixel Office kancalarını siler. Yedek dosya (`settings.json.pixel-backup`) olduğu gibi kalır.
