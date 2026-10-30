# Araştırma 2 — Claude'un Raporu

Tarih: 2026-10-30. Yazan: Claude. Okuyucu: kullanıcı ve Antigravity (karşılıklı inceleme için). Antigravity'nin
raporu `docs/ARASTIRMA2_ANTIGRAVITY.md`; iki rapor sonra `docs/ARASTIRMA2_ORTAK.md` olarak birleşir.

**Soru:** Pixel Office'in bugünkü hâlini herkesin kullanabildiği bir platforma dönüştürmek için ne yapılabilir?

**Bugünkü hâl:** Patron Odası, Faz 1–3 bitti, connector 3.11.0, web `po-v4.0.14`.

**Kurallar:**
- Sunucu maliyeti $0 kalır.
- Yeni bağımlılık ancak lisans ve güvenlik kontrolü ile kullanıcı onayından sonra eklenir.
- `docs/FAZ3_TASARIM.md` D1–D7 geçerlidir.
- Doğrulanmamış bilgi **(doğrulanmadı)** diye işaretlenmiştir.

## 1. Özet

Bulduğum en önemli üç şey:

1. **Rakipler aynı fikri yapıyor, ama yalnızca yerel olarak.**
   - Pixel Agents (VS Code eklentisi, MIT): Open VSX'te yaklaşık 40 bin indirme, hooks ile çalışıyor, ofis düzenleyicisi var.
   - pixel-office (Electron), pixtuoid (terminal, Rust) ve AIOffice (Phaser) da var.
   - Hiçbirinde bizim farklarımız yok: arkadaşlarla **paylaşılan bulut oda**, telefondan **uçtan uca şifreli uzaktan kumanda**, **Patron** ve tarayıcı ajanı.
   - Sonuç: rekabeti "daha güzel animasyon" üzerinden değil, **"ekip ve uzaktan kumanda"** üzerinden kazanırız.
2. **"Her ajanı bağla" sorunu artık standartlarla çözülebilir.**
   - **Agent Client Protocol (ACP, Zed, Apache-2.0):** Bir kayıt defterinde 25 ile 50 arasında ajan var (sayı kaynağa göre değişiyor). Claude Code, Codex CLI, Gemini CLI, OpenCode, Copilot ve Cursor bunlar arasında.
   - Bağlayıcı bir ACP istemcisi olursa, bugün yalnızca Claude Code'a verdiğimiz uzaktan kumanda **tek bir adaptörle onlarca ajana** açılır.
   - Codex CLI, Cursor ve Antigravity CLI'nin artık **kanca (hooks)** sistemi var. Böylece ofiste "durum gösterme" kısmı da kayıt dosyası okumadan, doğrudan olaylarla yapılabilir.
3. **"Anahtarsız ve kurulumsuz" ilk deneyim mümkün hâle geldi.**
   - Chrome'un yerleşik yapay zekâsı (Prompt API, Gemini Nano) cihazda çalışıyor. API anahtarı ve jeton ücreti yok.
   - Kısıtları var: yalnızca masaüstü Chrome'da çalışıyor, kararlı sürümde araç çağırma (function calling) yok.
   - Yine de tarayıcı ajanına "hiçbir şey girmeden dene" modu olarak eklenebilir.

## 2. Rakipler ve benzer projeler

| Proje | Biçim | Lisans | Güçlü yanı | Bizden eksik |
|---|---|---|---|---|
| [Pixel Agents](https://github.com/pablodelucca/pixel-agents) | VS Code eklentisi + bağımsız sunucu | MIT | ~40 bin indirme; hooks modu (SessionStart, PreToolUse, PermissionRequest, Stop), JSONL yedeği; alt ajanlar ayrı karakter; **64×64'e kadar ofis düzenleyici**; masa ↔ klasör eşleme | Bulut oda, telefon, uzaktan kumanda, Patron |
| [pixel-office (Sebaspelusa)](https://github.com/Sebaspelusa/pixel-office) | Electron + Next.js | (doğrulanmadı) | Claude Code + OpenCode (SQLite) birlikte, 7 tema | Aynı |
| [pixtuoid](https://www.pixtuoid.dev/) | Terminal, Rust | MIT | Claude Code + Codex, kurulumu hafif | Aynı |
| AIOffice | Phaser 3 | (doğrulanmadı) | Claude Code + Copilot CLI | Aynı |
| [AI Town (a16z)](https://github.com/a16z-infra/AI-town) | Web simülasyonu | MIT | Ajanların kendi aralarında yaşadığı kasaba | Convex arka ucuna bağlı; bizim modele (gerçek iş yapan ajan) uymuyor. Yalnızca ilham olarak alınmalı. |

**Çıkarımlar:**
- Pixel Agents'ın **ofis düzenleyicisi** ve **masa ↔ klasör eşlemesi** kullanıcıların sevdiği özellikler. Bunu bizde yapmak orta boy (M) bir iş (§4.6).
- Pixel Agents'ın **dağıtım kanalı** (VS Code ve Open VSX) en çok indirmeyi getiriyor. Bizim de orada bir varlığımız olmalı (§4.4).

## 3. Mevcut durumun kısa haritası (kod tabanından)

- **Durum gösterme:**
  - Claude Code: kanca ile, gizlilik süzgeci var.
  - Antigravity: kayıt dosyası okuyarak (onay anı görünmüyor).
  - Diğer ajanlar: `pixel_agent.py` ile elle durum bildirme.
- **Uzaktan kumanda:** Bağlayıcı yalnızca Claude Code'u başsız (headless) çalıştırıyor. Ollama ve OpenAI uyumlu motorlar E3 ile araç kullanabiliyor.
- **Kurulumsuz:** Tarayıcı ajanı var, ama kullanıcının kendi anahtarını girmesi gerekiyor.
- **Gerçek zaman:** Oda durumu Vercel + Upstash üzerinden 5 saniyelik yoklama (polling) ile geliyor. Kumanda Cloudflare röle üzerinden WebSocket ile çalışıyor.
- **Dil:** Yalnızca Türkçe.

## 4. Öneriler

Her öneride şunlar var: kime hitap ettiği, iş büyüklüğü (S/M/L), riskleri ve dış bağımlılık gerekip gerekmediği.

### 4.1 Kanca adaptörleri: Codex CLI, Cursor, Antigravity CLI (öncelik 1)

**Ne:** Bugünkü Claude Code kancasıyla aynı gizlilik süzgecini kullanan üç yeni adaptör. Her biri yalnızca durum, araç adı ve klasör adı gönderir.

- **Codex CLI** ([resmi belge](https://developers.openai.com/codex/hooks)): `~/.codex/hooks.json` ya da `config.toml`.
  - Olaylar: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `Stop`, `SubagentStart/Stop`.
  - Bir kaynağa göre hâlâ "deneysel" ve özellik bayrağı gerekebilir **(doğrulanmadı)**.
  - `PermissionRequest`, Claude'daki gibi **kesin "onay bekliyor" (!)** gösterir.
- **Cursor** ([belge](https://cursor.com/docs/hooks)): `~/.cursor/hooks.json`.
  - Olaylar: `beforeSubmitPrompt`, `preToolUse`, `postToolUse`, `beforeShellExecution`, `afterFileEdit`, `stop`, `sessionStart`…
  - CLI'da her olay gelmeyebilir; bir forum yazısı sonradan düzeldiğini söylüyor **(doğrulanmadı)**.
- **Antigravity CLI (`agy`)** ([rehber](https://medium.com/google-cloud/a-developers-guide-to-agent-hooks-in-antigravity-cli-4c1440febd11), [resmi: antigravity.google/docs/hooks]):
  - Olaylar: `PreInvocation`, `PostInvocation`, `PreToolUse`, `PostToolUse`, `Stop`. Yapılandırma `~/.gemini/config/hooks.json` dosyasında.
  - **Bu, kenara koyduğumuz "Antigravity'de onay görünmüyor" sorununu kökten çözebilir.** `PreToolUse` komut öncesinde çalışıyor, IDE'de de hooks var.
  - Kayıt dosyası izleyicisi (`pixel_antigravity.py`) yedek olarak kalır.
- **Gemini CLI'ya yatırım yapma.** Google, Gemini CLI'nın bireysel ücretsiz ve Pro/Ultra kullanımını 18 Haziran 2026'da kapatıp Antigravity CLI'ya yönlendirdi ([Google blog](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)).

| Kime | Büyüklük | Risk | Bağımlılık |
|---|---|---|---|
| Codex, Cursor ve Antigravity kullanıcıları (büyük kitle) | Her biri S–M | Kanca biçimleri "deneysel", değişebilir. Gizlilik süzgeci her biri için kanarya testli olmalı. Kancalar senkron çalıştığı için hızlı ve hata toleranslı olmalı (`|| true` deseni, zaman aşımı). | Yok (stdlib) |

### 4.2 Bağlayıcıyı ACP istemcisi yapmak: "her ajanı uzaktan yönet" (öncelik 2)

**Ne:** ([Zed ACP](https://zed.dev/acp), [kayıt defteri](https://zed.dev/blog/acp-registry))
- Bağlayıcı, ACP konuşan bir ajanı alt süreç olarak başlatır ve stdio üzerinden JSON-RPC konuşur.
- Görev, akış, araç olayları ve **izin istekleri** bizim mevcut `task.*` ve onay (approvals) akışımıza eşlenir.
- Claude Code, Codex, OpenCode, Copilot, Cursor ve Antigravity (ACP destekliyorsa) aynı yoldan gelir.

**Neden güçlü:**
- Telefondan Patron'a ya da bir ajana iş verme özelliği bugün yalnızca Claude Code'da var.
- ACP ile her ajana açılır, bizim tarafta da ajan başına ayrı kod yazmak gerekmez.

**Güvenlik:**
- ACP'de araç izinlerini ajan ister, istemci karar verir. Bu, bizim "uzaktan onay" modelimize birebir uyuyor.
- Ama her ajanın kendi izin modeli farklı. Bizim profillerimiz (readonly / edit / dev) ajanın kendi ayarlarına çevrilemeyebilir. O durumda **varsayılan reddetme** ve "bu ajan yalnızca readonly ile" kuralı uygulanır.
- Ayrı bir güvenlik incelemesi şart (P8 benzeri).

| Kime | Büyüklük | Risk | Bağımlılık |
|---|---|---|---|
| Claude dışı ajan kullanan herkes | L (protokol istemcisi, izin eşlemesi, testler) | Protokol genç ve sürüm değişebilir. Ajanların izin uygulaması farklı. Alt süreç yönetimi gerekiyor. | Yok. stdlib'de JSON-RPC + subprocess yeterli. Resmî SDK'lar TS/Python/Rust ve Apache-2.0, ama stdlib kuralı nedeniyle kendimiz yazarız. |

### 4.3 Anahtarsız ilk deneyim: Chrome yerleşik yapay zekâ (öncelik 3)

**Ne:** Tarayıcı ajanına üçüncü bir seçenek olarak "Cihazdaki model (ücretsiz, anahtarsız)" eklenir. Chrome'un **Prompt API**'si (`LanguageModel`) Gemini Nano'yu cihazda çalıştırıyor ([Chrome belgesi](https://developer.chrome.com/docs/ai/prompt-api)).

**Kısıtlar:**
- Web sayfaları için kararlı olduğu sürüm Chrome 148 deniyor **(kesin doğrulanmadı)**. Eklentilerde Chrome 138'den beri kararlı.
- Android, iOS ve sıradan ChromeOS cihazlarda yok.
- Model boyutu ve donanım gereksinimi yüksek.
- Kararlı sürümde **araç çağırma yok** ([apidog](https://apidog.com/blog/what-is-chrome-prompt-api/)). Bu yüzden ilk sürüm yalnızca **sohbet ve metin üretimi** olur. Sanal klasöre yazma "yanıtı dosyaya kaydet" düğmesiyle kullanıcı eliyle yapılır, model kendisi dosya araçları kullanmaz.
- Brave'de çalışmaz: Brave yerleşik Gemini Nano'yu sunmuyor **(doğrulanmadı)**. Kullanıcımızın kendisi Brave kullanıyor, bunu hesaba katmak gerek.

**Neden:** Bir ziyaretçi demoyu gördükten sonra hiçbir şey girmeden **gerçek bir ajanla** konuşabilir. "Kayıt yok, anahtar yok, kart yok" mesajı herkese hitap eder.

| Kime | Büyüklük | Risk | Bağımlılık |
|---|---|---|---|
| Yazılımcı olmayan ziyaretçiler | S–M | Tarayıcı ve donanım desteği sınırlı, özellik algılama ve dürüst bir "bu cihazda yok" mesajı gerekir. Model zayıf, kullanıcıyı hayal kırıklığına uğratabilir, bu yüzden beklenti doğru ayarlanmalı. CSP değişikliği gerekmez (ağ yok). | Yok |

**Alternatifler (önermiyorum):**
- **[WebLLM](https://dev.co/ai/frameworks/web-llm)** (Apache-2.0): WebGPU gerektiriyor, yüzlerce MB model indiriyor, araç çağırma "yapım aşamasında". Ayrıca yeni bir bağımlılık. İleride, Chrome olmayan tarayıcılar için yeniden düşünülebilir.
- **[Puter.js](https://developer.puter.com/tutorials/free-llm-api/)** ("kullanıcı öder" modeli): Anahtar istemiyor, ama kullanıcı bir Puter hesabına bağlanıyor ve betik onların CDN'inden geliyor. Bu, CSP `script-src`'yi açmak ve verileri üçüncü bir tarafa göndermek demek. Gizlilik sözümüzle çelişir. **Önermiyorum.**

### 4.4 Dağıtım kanalları (öncelik 4)

| Kanal | Ne | Büyüklük | Risk ve not |
|---|---|---|---|
| **VS Code / Open VSX eklentisi** | Odanın sayfasını bir webview'de açan ince bir eklenti. Kanca kurulumunu tek tıkla yapar. Pixel Agents'ın 40 bin indirmesi bu kanalın gücünü gösteriyor. | M | Pazar yeri yayın hesabı gerekir (ücretsiz). Webview CSP'si ayrı. TypeScript ve `vsce` derleme gerektiriyor; projede Node derleme adımı yalnızca `relay/` için var. |
| **Microsoft Store / Google Play (PWA)** | [PWABuilder](https://blog.pwabuilder.com/posts/publish-your-pwa-to-the-microsoft-store-on-windows-for-free) (MIT) ile mevcut PWA'yı paketlemek. Play için TWA (Bubblewrap). | S | Mağaza hesabı ücretleri: Microsoft ~19 $ (PWABuilder'ın ücretsiz hesap teklifi bugün geçerli mi **doğrulanmadı**), Play 25 $ tek seferlik. Bu, $0 kuralına tek seferlik bir istisna olur, **kullanıcı kararı**. Apple yolu deneysel. |
| **MCP Apps** | Bir MCP sunucusu, Claude ya da ChatGPT içinde ofisi **etkileşimli bir arayüz** olarak gösterir. Spesifikasyon 2026-01-26'da kesinleşti ([SEP-1865](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp)): `ui://` kaynakları, kum havuzlu iframe, postMessage. | M | İstemci desteği: OpenAI'nin desteklediği söyleniyor, Claude tarafı **doğrulanmadı**. MCP sunucusu bağlayıcıya eklenebilir (stdlib, stdio). Güzel bir vitrin: "Claude'a ofisimi göster". |
| **Discord Activity** | Ofisi bir Discord ses kanalında birlikte izlemek. SDK MIT, barındırma bizde (Vercel). | M | İstekler Discord vekil sunucusu üzerinden gider, CSP ve vekil ayarı gerekir. Discord geliştirici şartları geçerli. Ekip ve topluluk kitlesi için çekici. |

### 4.5 Gerçek zaman: yoklamadan anlık güncellemeye (öncelik 5)

**Ne:**
- Oda durumu bugün 5 saniyede bir yoklanıyor (Vercel + Upstash).
- Cloudflare **Durable Objects** artık ücretsiz planda SQLite ile kullanılabiliyor ([fiyatlandırma](https://developers.cloudflare.com/durable-objects/platform/pricing)). Ücretsiz planda günde 100 bin istek var, aşılınca hata döner, para çıkmaz.
- **WebSocket Hibernation** ile boşta kalan bağlantılar süre ücreti almıyor.
- Oda durumunu röledeki gibi bir Durable Object'e taşımak, karakterlerin anlık hareket etmesini sağlar ve Vercel ile Upstash çağrılarını azaltır.

| Kime | Büyüklük | Risk | Bağımlılık |
|---|---|---|---|
| Herkes (daha canlı ofis) | M–L | Günlük 100 bin istek sınırı büyük odalarda dolabilir. O zaman yoklamaya geri dönülür (fail-safe). Taşıma sırasında gizlilik süzgeci aynı kalmalı. Ücretsiz plan sınır tablosu üçüncü taraf kaynaktan alındı **(doğrulanmalı)**. | Yok (mevcut `relay/` Worker'a eklenir) |

### 4.6 Özelleştirme ve kişilik (öncelik 6)

- **Ofis düzenleyici:**
  - Zemin, duvar ve eşyalar, düzen oda başına kaydedilir. Pixel Agents'taki gibi.
  - Düzen, odanın yazma anahtarıyla Upstash'te saklanır ve doğrulanmış bir JSON şemasıyla tutulur.
  - Büyüklük M–L.
  - Risk: kullanıcı verisi çizime giriyor. Yalnızca izin listesindeki karo ve eşya kimlikleri kabul edilir, metin olarak sadece adlar ve onlar da `textContent` ile yazılır.
- **Masa ↔ klasör eşleme:** Belirli bir projenin ajanı hep aynı masaya otursun. Büyüklük S.
- **Hazır temalar** (kafe, uzay istasyonu, okul):
  - **Kenney** varlıkları CC0 lisanslı: atıf gerekmiyor, kısıt yok ([itch.io sayfası](https://kenney-assets.itch.io/rpg-urban-kit)).
  - **LPC** varlıklarının çoğu CC-BY-SA / GPL ve varlık başına değişiyor ([LPC CREDITS](https://lpc.opengameart.org/collection/11755/CREDITS.TXT)). Paylaşımlı lisans, varlıkları türev işe "bulaştırır". **Çekirdek sprite setine LPC karıştırmayalım.**
- **Karakter özelleştirme:** Saç, renk ve aksesuar seçimi. Mevcut `sprites.js` paletlerinin genişletilmesiyle yapılır. Büyüklük S–M.

### 4.7 Dil ve erişilebilirlik (öncelik 7, ama ucuz)

- **İngilizce arayüz.** "Herkes" dendiğinde dünya kitlesi demek, Türkçe tek dil bunu kısıtlıyor.
  - Metinler zaten çoğunlukla sabit tablolarda (`STATE_TEXT`, `CODE_TEXT`…). Yeni bir `web/js/i18n.js` ve bir sözlük yeterli.
  - Bağımlılık gerekmez, tarayıcının `Intl` API'si var.
  - Büyüklük M: metin çok, mekanik bir iş, Antigravity'ye uygun.
- **Erişilebilirlik:** Karakter durumları ekran okuyucuya `aria-live` ile duyurulmalı (kısmen var). Renk körlüğü için durumlar renk dışında bir işaretle de gösterilmeli. Büyüklük S.

### 4.8 Yapılmaması gerekenler

- **Gemini CLI adaptörü:** Bireysel kullanımı kapatıldı (§4.1).
- **AI Town'u entegre etmek:** Convex arka ucu gerektiriyor, bu da maliyet ve yeni bir platform demek. Yalnızca "ajanlar boşta birbirleriyle konuşsun" fikri ilham olarak alınabilir.
- **Puter.js:** Gizlilik sözümüzle ve CSP'mizle çelişiyor (§4.3).
- **Sağlayıcı anahtarlarını sunucuda tutmak** (proxy): Bu, $0 maliyet ve "anahtar sunucumuza gelmez" sözüyle çelişir.
- **Ücretsiz katman rakamlarını arayüze sabit yazmak:**
  - Gemini ücretsiz katman rakamları kaynaklar arasında çelişiyor: Flash için günde 20 ile 1.500 arası ([Google sınırları](https://ai.google.dev/gemini-api/docs/rate-limits)).
  - OpenRouter `:free` modellerde günde 50 istek veriyor, 10 $ kredi alınmışsa 1.000 ([OpenRouter](https://openrouter.ai/blog/tutorials/free-llm-apis-compared/)).
  - Arayüzde rakam vermeyip sağlayıcının kendi sayfasına bağlantı vermeliyiz.

## 5. Açık sorular (bir sonraki adımda doğrulanmalı)

1. **Sağlayıcıların tarayıcıdan doğrudan istek (CORS) izni:** Gemini (OpenAI uyumlu uç), Groq, OpenRouter, OpenAI, DeepSeek ve Mistral için hiçbir kaynakta açık bilgi bulamadım. Tarayıcı ajanı (E4) buna bağlı. **Kullanıcının gerçek bir anahtarla ilk denemesi bunu gösterecek.** İzin vermeyen sağlayıcı listeden çıkarılmalı.
2. Codex hooks hâlâ özellik bayrağı istiyor mu? Antigravity hooks IDE'de de aynı mı çalışıyor? Resmî belgelerden kontrol edilmeli.
3. MCP Apps'i Claude istemcisi destekliyor mu?
4. Microsoft Store için ücretsiz hesap teklifi hâlâ geçerli mi?

## 6. Öncelik sıralı liste

| Sıra | İş | Büyüklük | Kim (öneri) | Neden bu sırada |
|---|---|---|---|---|
| 0 | Tarayıcı ajanı CORS doğrulaması (kullanıcı + Claude) | S | Kullanıcı dener, Claude düzeltir | E4'ün gerçekten çalıştığını bilmeden yeni şeye geçmeyelim |
| 1 | Kanca adaptörleri: Antigravity CLI, Codex CLI, Cursor (§4.1) | Her biri S–M | Claude (gizlilik süzgeci ve güvenlik) | En büyük kitle, en az risk, "?" sorununu çözüyor |
| 2 | İngilizce arayüz (§4.7) | M | Antigravity (mekanik, çok metin) | Dünya kitlesinin önündeki en büyük engel |
| 3 | Chrome yerleşik yapay zekâ ile anahtarsız deneme (§4.3) | S–M | Antigravity (tarayıcı) + Claude (inceleme) | "Kayıt yok, anahtar yok" ilk deneyim |
| 4 | Masa ↔ klasör eşleme + karakter özelleştirme (§4.6) | S–M | Antigravity | Ucuz, sevilen özellikler |
| 5 | ACP istemcisi (§4.2) | L | Claude (protokol ve güvenlik) + ayrı güvenlik incelemesi | En büyük kazanç, ama en büyük iş; tasarım belgesi ve onayla başlamalı |
| 6 | VS Code eklentisi + PWA mağazaları (§4.4) | M / S | Antigravity | Dağıtım; mağaza ücreti kullanıcı kararı |
| 7 | Durable Objects ile anlık oda (§4.5) | M–L | Claude | Altyapı; sınırlar doğrulandıktan sonra |
| 8 | Ofis düzenleyici + temalar (§4.6) | M–L | Antigravity | Sevilen ama büyük iş |
| 9 | MCP Apps, Discord Activity (§4.4) | M | Sonra | İstemci desteği netleşince |

## Kaynaklar

- Pixel Agents: [github.com/pablodelucca/pixel-agents](https://github.com/pablodelucca/pixel-agents), [Open VSX](https://open-vsx.org/extension/pablodelucca/pixel-agents)
- pixel-office: [github.com/Sebaspelusa/pixel-office](https://github.com/Sebaspelusa/pixel-office) · pixtuoid: [pixtuoid.dev](https://www.pixtuoid.dev/) · [Roundtable incelemesi](https://baeseokjae.github.io/posts/roundtable-pixel-office-replay/)
- AI Town: [github.com/a16z-infra/AI-town](https://github.com/a16z-infra/AI-town)
- ACP: [zed.dev/acp](https://zed.dev/acp), [ACP Registry](https://zed.dev/blog/acp-registry), [Harici ajanlar](https://zed.dev/docs/ai/external-agents), [ACP açıklaması](https://www.morphllm.com/agent-client-protocol)
- AG-UI: [CopilotKit mimari](https://docs.copilotkit.ai/ag-ui/concepts/architecture) (önermiyorum: bizim kendi akış protokolümüz var; yalnızca kıyas)
- Codex hooks: [developers.openai.com/codex/hooks](https://developers.openai.com/codex/hooks), [rehber](https://codex.danielvaughan.com/2026/04/15/codex-cli-hooks-complete-guide-events-policy-patterns/)
- Cursor hooks: [cursor.com/docs/hooks](https://cursor.com/docs/hooks), [GitButler incelemesi](https://blog.gitbutler.com/cursor-hooks-deep-dive)
- Antigravity CLI hooks: [Medium rehberi](https://medium.com/google-cloud/a-developers-guide-to-agent-hooks-in-antigravity-cli-4c1440febd11), [atamel.dev](https://atamel.dev/posts/2026/07-16_where_agy_hooks/), [vibeaudio issue](https://github.com/kiril6/vibeaudio/issues/47)
- Gemini CLI → Antigravity CLI: [Google Developers Blog](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)
- Gemini CLI hooks (tarihçe): [geminicli.com/docs/hooks](https://geminicli.com/docs/hooks/)
- Chrome Prompt API: [developer.chrome.com/docs/ai/prompt-api](https://developer.chrome.com/docs/ai/prompt-api), [DEV](https://dev.to/kenyalba/chromes-prompt-api-is-stable-on-device-ai-for-your-website-no-api-key-and-no-per-token-bill-555e), [apidog](https://apidog.com/blog/what-is-chrome-prompt-api/)
- WebLLM: [dev.co](https://dev.co/ai/frameworks/web-llm), [makale](https://arxiv.org/html/2412.15803v2)
- Puter.js: [developer.puter.com](https://developer.puter.com/tutorials/free-llm-api/)
- MCP Apps: [SEP-1865](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp), [ext-apps spesifikasyonu](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx), [MCP blog](https://blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps/)
- PWABuilder: [ücretsiz Microsoft Store](https://blog.pwabuilder.com/posts/publish-your-pwa-to-the-microsoft-store-on-windows-for-free), [web.dev PWA mağazalarda](https://web.dev/articles/pwas-in-app-stores?hl=tr), [capgo karşılaştırma](https://capgo.app/alternatives/pwabuilder/)
- Discord Embedded App SDK: [discord.com/developers](https://discord.com/developers/docs/developer-tools/embedded-app-sdk), [npm](https://npmjs.com/package/@discord/embedded-app-sdk)
- Durable Objects: [fiyatlandırma](https://developers.cloudflare.com/durable-objects/platform/pricing), [sürüm notları](https://developers.cloudflare.com/durable-objects/release-notes/)
- Varlıklar: [Kenney (itch.io)](https://kenney-assets.itch.io/rpg-urban-kit), [LPC CREDITS](https://lpc.opengameart.org/collection/11755/CREDITS.TXT), [cinevva rehberi](https://app.cinevva.com/guides/free-2d-sprites-tilesets)
- Ücretsiz katmanlar: [Gemini sınırları](https://ai.google.dev/gemini-api/docs/rate-limits), [OpenRouter karşılaştırma](https://openrouter.ai/blog/tutorials/free-llm-apis-compared/)
- Pyodide (tarayıcıda Python, MPL-2.0; önermiyorum, kapsam dışı): [github.com/pyodide](https://github.com/ryanking13/pyodide)
