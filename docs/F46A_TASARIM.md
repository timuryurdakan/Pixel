# F4-6a — Bağlayıcıda ACP istemcisi: Tasarım (taslak)

Durum: **onaylandı** (2026-11-01) ve uygulandı (bağlayıcı 3.16.0). Uygulamadaki tek sapma, daha sıkı yönde: A2'deki onaylar ilk sürümde yalnızca bilgisayarda verilir (`/onay`, `pixel-connector approve`). Tarayıcıdan cevaplanabilen onaylar şimdilik yalnızca Claude Code'un araç onayları. Kaynak: `docs/ARASTIRMA2_ORTAK.md` §3.1/2 ve F4-6. F4-6 ikiye bölündü:

- **F4-6a (bu belge):** ACP istemcisi. K2'ye bağlı değil.
- **F4-6b:** Çok motorlu Patron. K2 gereği, tarayıcıdaki Patron gerçek anahtarla denenip kalitesi görüldükten sonra
  ele alınacak.

Protokol bilgisi agentclientprotocol.com'daki v1 sayfalarından alındı (2026-11-01): overview, initialization,
session-setup, prompt-turn, tool-calls, file-system, terminals.

## 1. Amaç

ACP (Agent Client Protocol), editörlerin kodlama ajanlarıyla konuştuğu açık bir protokol. Ajan, alt süreç olarak
çalışır ve stdin/stdout üzerinden JSON-RPC 2.0 konuşur. Codex CLI, Gemini CLI, OpenCode ve GitHub Copilot gibi
ajanların ACP desteği var. Bağlayıcı bir ACP istemcisi olursa, bu ajanlar Pixel Office'te normal bir ajan gibi
oluşturulur, görev alır, ofiste çalışırken görünür ve telefondan yönetilir. Her ajan için ayrı bir adaptör yazmak
gerekmez.

## 2. Protokolden bize düşenler

| Yön | Yöntem | Bizim kullanımımız |
|---|---|---|
| İstemci → ajan | `initialize` | `protocolVersion: 1`, `clientCapabilities: {fs: {readTextFile: true, writeTextFile: true}}`. `terminal` ilan edilmez. `auth.terminal` ilan edilmez. |
| İstemci → ajan | `session/new` | `cwd` = çalışma klasörünün mutlak yolu, `mcpServers: []` (K3: MCP kapalı). |
| İstemci → ajan | `session/prompt` | Görevin istemi. Cevaptaki `stopReason` (`end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`) görevin sonucunu belirler. |
| İstemci → ajan | `session/cancel` (bildirim) | Görev iptal edilince gönderilir. Bekleyen izin istekleri `cancelled` ile cevaplanır. |
| Ajan → istemci | `session/update` | `agent_message_chunk` → canlı metin, `tool_call` / `tool_call_update` → ofiste araç simgesi. Diğer türler yok sayılır. |
| Ajan → istemci | `session/request_permission` | §4'teki kurala göre cevaplanır. |
| Ajan → istemci | `fs/read_text_file`, `fs/write_text_file` | E3'teki dosya araçlarının kurallarıyla (`tools.py` `split_path`, ret listesi, boyut tavanı, profil) cevaplanır. |
| Ajan → istemci | `terminal/*`, `elicitation/*`, bilinmeyen yöntem | Hata `-32601` (yöntem yok). |

## 3. Asıl risk: izinleri biz zorlayamayız

ACP'de ajanın izin istemesi zorunlu değil, belirtim "**MAY** request permission" diyor. Dosya ve komut işlerini
ajan kendi araçlarıyla da yapabilir. Bu durumda bize hiçbir şey sormaz. Bizim `readonly`/`edit` profillerimiz yalnızca
iki durumda gerçekten zorlanır: ajan bize izin sorduğunda ya da dosyayı bizim `fs/*` yöntemlerimizle okuyup
yazdığında. Claude Code'da durum farklı: orada izinleri komut satırı bayraklarıyla biz veriyoruz.

Bu yüzden bir ACP ajanı, **kullanıcının o ajanın kendi komut satırını çalıştırmasıyla aynı güvende** sayılır. Aşağıdaki
kararlar bu gerçeğe göre alındı.

## 4. Kararlar (onay gereken)

| # | Karar | Önerim | Neden |
|---|---|---|---|
| A1 | Hangi ajanlar? | **Yalnızca bilgisayarda, `config.json`'da** tanımlananlar: `engines.acp: [{id, name, command: [mutlak yol, argümanlar…], enabled}]`. Varsayılan kapalı. Tarayıcıdan ACP motoru eklenemez ya da değiştirilemez. `npx -y …` gibi her seferinde paket indiren komutlar reddedilir: kullanıcı ajanı kendisi kurar, mutlak yolunu verir. | Uzaktan komut satırı tanımlamak, uzaktan kod çalıştırmak demek. Her çalıştırmada indirme yapmak da tedarik zinciri riski. |
| A2 | İzin istekleri | `readonly`: yalnızca `kind: "read"` olan ve bütün konumları çalışma klasöründe kalan istek kabul (`allow_once`), gerisi ret (`reject_once`). `edit`: buna ek olarak `edit`, `delete` ve `move` istekleri, bütün konumları klasördeyse kabul. Diğer her şey (`execute`, `fetch`, `other`, konumsuz istekler) **bilgisayarda ya da tarayıcıda onaya** gider; 120 sn cevap gelmezse ret. `allow_always` hiçbir zaman seçilmez. `dev` ve `auto` profilleri ACP motorunda sunulmaz. | Zorlayabildiğimiz yerde güvenli tarafta kalmak (fail-closed). |
| A3 | Dürüst etiket | Ajan oluştururken ve ajan kartında şu yazar: "İzinleri ajanın kendi ayarları belirler. Pixel Office yalnızca ajan sorduğunda karar verir." | §3. Kullanıcı yanlış güvence varsaymasın. |
| A4 | Uzaktan ajan oluşturma | Tarayıcıdan ACP ajanı oluşturmak **her zaman bilgisayarda onay** ister (`confirm.agentCreate` kapalı olsa bile; `workflow.save`'deki gibi). | ACP ajanı Claude Code'dan daha az kısıtlanabiliyor. |
| A5 | Süreç ömrü | **Görev başına bir süreç:** başlat, `initialize`, `session/new`, `session/prompt`, kapat. Konuşma geçmişi ilk sürümde yok (`session/load` kullanılmaz). | Basit, sızıntısız. Ajan arada çökse bile sonraki görev temiz başlar. |
| A6 | Ortam ve giriş | Alt süreç ortamı Claude motorundaki izin listesiyle kurulur (`child_env`). Bizim API anahtarı ortam değişkenlerimiz geçmez. Ajan giriş isterse (`authMethods` gelir ve `session/new` kimlik hatası verirse) görev "Ajan giriş istiyor: önce terminalde ajanın kendi girişini yapın." hatasıyla biter. Giriş akışını biz yürütmeyiz. | Kimlik bilgisi bizim elimizden geçmesin. |
| A7 | Tavanlar | Görev süresi `limits.taskTimeoutS`. stdout'ta satır başına en fazla 1 MB, toplam canlı metin `fit_result` tavanı. `fs/read_text_file` ve `fs/write_text_file` boyutu E3 araçlarıyla aynı. İptalde önce `session/cancel` gönderilir, 5 sn içinde bitmezse süreç grubu öldürülür. | Kaynak tüketimi sınırlı kalsın. |
| A8 | Maliyet ve Patron | Maliyet bilinmez (`costBasis: unknown`, OpenAI uyumlu motorlardaki gibi). ACP ajanları ilk sürümde **Patron'da ve şablon adımlarında kullanılmaz**. | Patron F4-6b'nin konusu. Şablon motorları `claude`/`ollama` ile sınırlı. |

## 5. Güvenlik özeti

- Ajan çıktısı (metin, araç başlıkları, yollar) güvenilmezdir. Arayüze yalnızca `textContent` ile yazılır, uzunluğu
  kesilir, gizli karakterler temizlenir (mevcut `short`).
- `fs/*` isteklerindeki yollar mutlak gelir. Çalışma klasörünün çözülmüş yolunun altında değilse ret. Sembolik bağ ve
  ret listesi kuralları E3'teki gibi.
- JSON-RPC ayrıştırıcısı katıdır: tanınmayan yöntemler, aşırı büyük satırlar ve beklenmeyen `id`'ler reddedilir. Bunlar
  süreci düşürmez, görevi hata ile bitirir.
- Kanarya testi: bağlayıcının ortamındaki bir sahte anahtar, alt sürecin ortamına, olaylara ve kayıtlara girmez.

## 6. Test planı

Gerçek bir ajana gerek kalmadan, testlerde stdlib ile yazılmış **sahte bir ACP ajanı** (`tests/_fake_acp_agent.py`)
çalıştırılır. Bu sahte ajan senaryoya göre şunları yapar: metin akıtır, izin ister, `fs` ile okur ve yazar, klasör
dışına yazmayı dener, iptali yok sayar, bozuk JSON gönderir, giriş ister.

Gerçek ajanla deneme kullanıcıya kalır. Örneğin bilgisayarında Codex CLI ya da Gemini CLI varsa onunla denenir.

## 7. İş bölümü ve büyüklük

| Parça | Dosyalar | Büyüklük |
|---|---|---|
| JSON-RPC stdio istemcisi, motor | yeni `pixel_connector/engines/acp.py` | M |
| Yapılandırma (`engines.acp`), cihaz bilgisi, ajan oluşturma kuralı (A4) | `config.py`, `app.py`, `agents.py` | S–M |
| İzin eşlemesi ve onay (A2) | `acp.py` + mevcut `approvals` | S |
| Arayüz: motor seçeneği, A3 etiketi | `web/js/prompt.js`, `devices.js`, `i18n_en.js` | S |
| Sahte ajan, testler, belgeler, güvenlik notu | `tests/`, `docs/INTEGRATIONS.md`, `SECURITY_AUDIT.md` | M |

Toplam yaklaşık L. Bağlayıcı sürümü artar, kullanıcı `pixel-connector update` çalıştırır.
