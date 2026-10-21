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
| `pixel_antigravity.py` | Antigravity IDE izleyicisi (`pixel-antigravity`). `pixel_agent.py` ile aynı klasörde durmalı. |

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

### Telefondan onay (Aşama 19)

Claude Code bir izin istediğinde (ör. bir `Bash` komutu) ya da seçmeli soru sorduğunda (`AskUserQuestion`), eşleşmiş telefonunuzda ofisin üstünde bir kart çıkar: araç, kısa özet (komut, dosya adı ya da adres), ajan, proje ve kalan süre; **Onayla** / **Reddet** düğmeleri, sorularda seçenekler ve **Gönder**. Bilgisayardaki Claude Code penceresi de aynı anda açık kalır; önce hangisi cevaplanırsa o geçerli olur, kart diğer cihazlardan kalkar.

Açmak için (bağlayıcı kurulu ve telefon eşleşmiş olmalı):
```sh
pixel-connector remote-approvals on      # kapatmak: off · durum: status (doctor da gösterir)
```
Sonra kancayı bir kez yeniden kurun (Ajan bağla komutunu yeniden çalıştırın): kurulum artık `PermissionRequest` için beklemeli bir giriş yazar (`pixel_hook.py hook --decide`, `"timeout": 300`). Açık Claude Code oturumlarını yeniden başlatın.

- Varsayılan **kapalı**; kapalıyken kanca eskisi gibi yalnızca durumu bildirir.
- Cevap 240 sn içinde gelmezse, bağlayıcı kapalıysa ya da bir sorun olursa kanca hiçbir şey yazmaz: Claude Code'un kendi penceresi kalır. Kanca kendiliğinden asla izin vermez.
- Özet bilgisayarda hazırlanır, yalnızca uçtan uca şifreli kanaldan eşleşmiş tarayıcılarınıza gider; sunucu ve röle göremez, ofiste yine yalnızca "İzin bekliyor" görünür.
- Plan onayı (`ExitPlanMode`) telefona gönderilmez. Bilgisayarda `http://127.0.0.1:8766/onay` sayfası (`pixel-connector approve --link`) ve `pixel-connector approve|deny <id>` de aynı istekleri cevaplar (soruları yalnızca reddedebilir).
- Çalışmayanlar: PowerShell istemcisi (`pixel_hook.ps1`; Windows'ta Python istemcisini kullanın), Antigravity (onay arayüzü yok, §6), `pixel-run` / `pixel-proxy` (izin istemi yok), bağlayıcının çalıştırdığı Claude ajanları (izin profilleriyle çalışır, soru sormaz).

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
python3 pixel_agent.py install-shims      # ~/.pixel-office/bin/pixel-run, pixel-proxy (Windows'ta .cmd);
                                          # yanında pixel_antigravity.py varsa pixel-antigravity de
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

## 6. Antigravity IDE (`pixel-antigravity`)

Antigravity'nin kanca (hook) sistemi yok. Ama her konuşmanın adımlarını bilgisayara bir kayıt dosyasına yazıyor:
```
~/.gemini/antigravity-ide/brain/<konuşma>/.system_generated/logs/transcript.jsonl
```
`pixel_antigravity.py` bu dosyaları salt okunur izler ve her konuşmayı ofiste bir karakter olarak gösterir. Antigravity'ye hiçbir şey kurulmaz, dosyalarına yazılmaz.

**Kurulum.** İzleyici ayrı bir dosyadır ve `pixel_agent.py` kütüphanesini kullanır; ikisi aynı klasörde durmalı. Kanca kurulumu (`install.sh` / `install.ps1`) ikisini de `~/.pixel-office/` içine indirir. Elle:
```sh
curl -fsSL https://<host>/hook/pixel_agent.py -o pixel_agent.py
curl -fsSL https://<host>/hook/pixel_antigravity.py -o pixel_antigravity.py
```

```sh
python3 pixel_antigravity.py                             # aynısı: python3 pixel_agent.py antigravity
python3 pixel_antigravity.py --name "Gemini"             # karakter adı (varsayılan "Antigravity")
python3 pixel_antigravity.py --install-service           # oturum açılınca başlasın (Linux, macOS, Windows)
python3 pixel_antigravity.py --install-service --dry-run # yalnızca ne yapılacağını yazar, hiçbir şeyi değiştirmez
python3 pixel_antigravity.py --uninstall-service
python3 pixel_agent.py install-shims                     # sonra: pixel-antigravity
```
Hedef adres öbür araçlarla aynı sırayla bulunur (§5). Servis `--url/--room/--key` almaz, `~/.pixel-office/config.json` dosyasını okur; böylece anahtar servis dosyasına yazılmaz. `--install-service` iki dosyayı `~/.pixel-office/` içine kopyalar, servis oradan çalışır.

**Neden ayrı dosya:** İzleyici yalnızca Antigravity'nin olduğu bilgisayarda gerekiyor. `pixel_agent.py` küçük bir kütüphane olarak kalıyor (v2.md D6) ve bağlayıcı paketi (`pixel-connector.pyz`, `pixel_agent.py`'yi içerir) izleyiciyi taşımıyor. İzleyici `pixel_connector` paketini içe aktarmaz, çünkü yalnızca kanca dosyalarını indiren bilgisayarlarda o paket yok. Bu yüzden servis tanımı için gereken küçük `_quote_systemd` yardımcısı bilerek kopyalandı; bir test iki kopyanın aynı sonucu verdiğini denetler.

**Nasıl izler:**
- İki kök klasöre bakar: `~/.gemini/antigravity-ide/brain` ve `~/.gemini/antigravity/brain`. Başka bir yer için `--root KLASÖR` (birden çok kez verilebilir) ya da `PIXEL_ANTIGRAVITY_DIR` ortam değişkeni (`:` ile ayrılmış; Windows'ta `;`).
- Saniyede bir dosya boyutuna bakar, yalnızca sona eklenen tamamlanmış satırları okur. Yarım kalan son satır tamamlanınca okunur; bozuk JSON satırları ve 1 MB'tan uzun satırlar atlanır. Dosya kısalırsa ya da yenisiyle değiştirilirse baştan okunur. inotify gibi ek bir bağımlılık yok.
- Yeni konuşmalar ve sonradan oluşan `brain` klasörleri 5 sn içinde fark edilir.
- **Geçmiş yeniden oynatılmaz.** Bir konuşma fark edildiğinde (açılışta da, sonradan da) dosyanın yalnızca son 256 KB'ı okunur. Durum son adımdan, yaşı da son adımın `created_at` zamanından çıkarılır (yoksa dosyanın değişme zamanı). Bu yaş aşağıdaki eşiklerden geçer: 10 dakika önce biten bir konuşma "çalışıyor" değil "uyuyor" görünür, 45 dakikadan eski bir konuşma hiç görünmez. Sonrasında yalnızca yeni eklenen satırlar canlı sayılır.
- `created_at` adımın başladığı an. Uzun süren bir adım bu yüzden olduğundan eski görünebilir; bu bilerek seçildi, eski bir konuşma asla yanlışlıkla "çalışıyor" gösterilmez.

**Kayıt biçimi** (bu bilgisayardaki gerçek dosyalarla 2026-10-21'de doğrulandı, yalnızca yapı incelendi): her satır bir JSON nesnesi. Hep bulunan alanlar `step_index` (sayı), `source` (`MODEL`, `USER_EXPLICIT`, `SYSTEM`), `type`, `status` (`DONE`, `RUNNING`), `created_at` (saniye hassasiyetinde `YYYY-MM-DDTHH:MM:SSZ`). Ara sıra `content`, `thinking`, `tool_calls` (`name` + `args`; `args.Cwd` çoğunlukla JSON tırnaklı metin), `truncated_fields`, `exit_code`, `error`, `error_code`. Görülen türler: `USER_INPUT`, `PLANNER_RESPONSE`, `VIEW_FILE`, `RUN_COMMAND`, `CODE_ACTION`, `LIST_DIRECTORY`, `GREP_SEARCH`, `GENERIC`, `SEARCH_WEB`, `READ_URL_CONTENT`, `SYSTEM_MESSAGE`, `CONVERSATION_HISTORY`, `KNOWLEDGE_ARTIFACTS`, `CHECKPOINT`, `ERROR_MESSAGE`. Satır, adım bitince yazılıyor (dosyanın değişme zamanı son `created_at`'ten 8–30 sn sonra). Biçim belgelenmemiştir ve Antigravity güncellenince değişebilir. Bu yüzden ayrıştırıcı eksik alanlara ve bilinmeyen türlere dayanıklıdır: bilinmeyen bir model adımı "çalışıyor" sayılır, kaynağı ve türü olmayan satır yok sayılır.

**Durum eşlemesi** (süreler ayarlanabilir):

| Kayıttaki son adım | Ofiste |
|---|---|
| Kullanıcı mesajı (`USER_INPUT`) ya da sistem adımı | `thinking` (hata mesajında etiket "Hata") |
| Model bir araç çağırdı ya da araç sonucu geldi | `working`, araç adıyla: `run_command` → Bash, `view_file` → Read, `replace_file_content` → Edit, `write_to_file` → Write, `grep_search` → Grep, `list_dir` → LS, `search_web` → WebSearch, `read_url_content` → WebFetch, `manage_task` → TodoWrite; bilinmeyen araçlar kendi adıyla (izin verilen karakterlere süzülerek) |
| Model araç çağırmadan cevabını bitirdi | `idle` (kahve). Sıra insanda; Claude Code'un `Stop` olayıyla aynı davranış |
| `--idle-after` sn (varsayılan 60) yeni adım yok, karakter çalışıyordu | `idle` |
| `--sleep-after` sn (varsayılan 180) yeni adım yok | `sleeping` (uyur, ofisten çıkmaz) |
| `--gone-after` sn (varsayılan 2700 = 45 dk) yeni adım yok, izleyici kapandı (Ctrl+C, SIGTERM) ya da konuşma silindi | `offline` (kapıdan çıkar) |

Neden bu değerler:
- **`waiting_for_user` kullanılmıyor.** Antigravity bir aracın sonucunu ve onu çağıran model adımını araç bittiğinde birlikte yazıyor. Bu yüzden bir komutun onay mı beklediği yoksa hâlâ mı çalıştığı kayıttan anlaşılamıyor. Yanlış "!" göstermek yerine bu sessizlik 60 sn sonra `idle` olur.
- **60 sn:** Bu bilgisayardaki gerçek kayıtlarda (2026-10-21, 1135 adım arası) bir tur içindeki beklemelerin yaklaşık %9'u 20 sn'den, %2,5'i 60 sn'den uzun. 20 sn'lik eşik karakteri çalışırken sık sık kahveye gönderirdi.
- **180 sn ve 45 dk:** Sunucu da 180 sn güncelleme almayan boştaki karakteri uyutur ve kanca ajanlarını 45 dk sonra kaldırır. Kütüphane 5 dakikada bir canlı tutma güncellemesi gönderdiği için daha uzun bir "boşta" süresi karakteri uyku ile kahve arasında gidip getirirdi.

**Birden çok konuşma:** Her konuşma ayrı bir karakterdir. En son kullanılan konuşma `Antigravity`, diğerleri `Antigravity 2`, `Antigravity 3`… adını alır; ad konuşma ofisten çıkana kadar değişmez. Oturum kimliği `ag-` + konuşma kimliğinin SHA-256 özetinin ilk 10 hanesidir; ham konuşma kimliği gönderilmez.

**Proje adı:** komutların çalıştığı klasörün (`Cwd`) yalnızca son klasör adı. Henüz komut çalışmadıysa proje boş kalır. Sabit bir ad için `--project AD`.

**Gizlilik:** Ofise yalnızca durum, araç adı, sabit "Hata" etiketi, proje klasörünün adı, ajan adı ve sahip gider. Konuşma metni, düşünce, hata metni, komut satırı, dosya yolları, araç argümanları ve dosya içerikleri satır okunurken atılır; diske de yazılmaz. Testler (`tests/test_phase18_antigravity.py`) gönderilen her güncellemeyi bir kanarya metniyle denetler.

**Oturum açılınca başlatma** (`--install-service`, önce `--dry-run` ile bakılabilir):
- **Linux:** `~/.config/systemd/user/pixel-antigravity.service` yazılır, `systemctl --user daemon-reload`, `enable`, `restart` çalışır. Durum: `systemctl --user status pixel-antigravity`, günlük: `journalctl --user -u pixel-antigravity -f`. Bilgisayar açılır açılmaz (oturum açmadan) çalışsın isterseniz: `loginctl enable-linger $USER`.
- **macOS:** `~/Library/LaunchAgents/tr.pixeloffice.antigravity.plist` yazılır, `launchctl bootout` (eski sürümü kapatır) ve `launchctl bootstrap gui/$(id -u) …` çalışır. Günlük: `~/.pixel-office/antigravity.err.log`. Elle yapmak isterseniz plist şöyledir:
  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0"><dict>
    <key>Label</key><string>tr.pixeloffice.antigravity</string>
    <key>ProgramArguments</key><array>
      <string>/usr/bin/python3</string><string>/Users/ADINIZ/.pixel-office/pixel_antigravity.py</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  </dict></plist>
  ```
  Kaldırmak: `launchctl bootout gui/$(id -u)/tr.pixeloffice.antigravity && rm ~/Library/LaunchAgents/tr.pixeloffice.antigravity.plist`.
- **Windows:** Görev Zamanlayıcı'da oturum açılışında çalışan `PixelOffice\Antigravity` görevi oluşturulur ve hemen başlatılır. Görev `~/.pixel-office/pixel-antigravity.task.xml` dosyasından kurulur (`schtasks /Create /XML … /F`, yönetici izni gerekmez): `pythonw.exe` ile çalışır (pencere açılmaz), pildeyken de başlar ve fiş çekilince durmaz, 72 saat sınırı yoktur, hata verirse dakikada bir, 3 kez yeniden başlatılır (bağlayıcıyla aynı ayarlar, `docs/V3.md` §12.3). `PIXEL_OFFICE_HOME` varsayılan değilse göreve aktarılır. Kaldırmak: `--uninstall-service` ya da `schtasks /Delete /TN "PixelOffice\Antigravity" /F`. Görev Zamanlayıcı kullanılamıyorsa Başlangıç klasörüne kısayol da olur: `Win+R` → `shell:startup` → yeni kısayol, hedef `"C:\…\pythonw.exe" "%USERPROFILE%\.pixel-office\pixel_antigravity.py"`.
- macOS ve Windows yolları burada sahte komut çalıştırıcıyla test edildi; gerçek bir Mac ya da Windows bilgisayarda denenmedi.

**Bilinen sınırlar:**
- Kayıt yalnızca adım bitince yazıldığı için karakter, adım başladığında değil bittiğinde hareket eder (genelde birkaç saniye gecikme).
- Antigravity'nin izin istemleri kayıtta görünmüyor; onay bekleyen bir konuşma 60 sn sonra kahve içer.

**Dışarıdan kontrol (`agentapi`; Aşama 19 için araştırma, 2026-10-19, 2026-10-21'de yeniden doğrulandı):** `~/.gemini/antigravity-ide/bin/agentapi` iki satırlık bir kabuk betiği; Antigravity'nin dil sunucusunu (`/opt/antigravity-ide/resources/app/extensions/antigravity/bin/language_server_linux_x64 agentapi …`) çağırıyor. Yalnızca `--help` çalıştırıldı (her alt komut için de). Üç komut var:
- `get-conversation-metadata <konuşma>`: konuşma bilgisi.
- `new-conversation [--model=flash_lite|flash|pro] [--title=…] [--profile=…] <istem>`: yeni konuşma başlatır.
- `send-message [--title=…] <alıcı> <içerik>`: bir konuşmaya (kendine de) mesaj gönderir.

Bunlar dil sunucusunun `/_sidecar/get-conversation-metadata`, `/_sidecar/new-conversation`, `/_sidecar/send-message` uçlarına gidiyor; araç Antigravity içindeki arka plan "sidecar"ları ve zamanlanmış görevler için yapılmış (`ANTIGRAVITY_AGENTAPI_EXE`). **Araç onayı ya da reddi için bir komut yok.** Dil sunucusunun içinde `LanguageServerService/HandleCascadeUserInteraction` ve `CancelCascadeInvocation` gibi iç RPC adları var; onaylar büyük olasılıkla IDE arayüzü ile dil sunucusu arasındaki bu belgelenmemiş iç bağlantıdan geçiyor. Bu bağlantı dışarıya açık bir arayüz değil, kullanmak desteklenmez ve her güncellemede bozulabilir.

Sonuç (Aşama 19'da uygulandı ve böyle kaldı, 2026-10-21): Antigravity için telefondan "onayla/reddet" yapılamaz; uzaktan onay yalnızca Claude Code kancasıyla çalışır (§1, "Telefondan onay"). Yapılabilecek olan, `send-message` ile bir konuşmaya metin cevap ya da `new-conversation` ile yeni görev göndermek. Bu da yalnızca açık bir kullanıcı eylemiyle olmalı. Araştırmada hiçbir konuşmaya mesaj gönderilmedi.

---

## 7. Sorun giderme
- **Karakter görünmüyor:**
  - `pixel_hook.py doctor` çalıştırın. Sunucuya ulaşılabildiğini ve kancalı olay sayısının 13/13 olduğunu kontrol edin.
  - Claude Code oturumunu yeniden başlattığınızdan emin olun.
- **`hook.log`'da 401:** Yazma anahtarı yanlış. Kurulum komutunu davet bağlantısından yeniden alın.
- **`hook.log`'da `neterr`:** Sunucuya ulaşılamıyor. Bağlantıyı ve sunucu adresini kontrol edin.
- **Kancayı kaldırmak:** `pixel_hook.py uninstall` yalnızca Pixel Office kancalarını siler. Yedek dosya (`settings.json.pixel-backup`) olduğu gibi kalır.
