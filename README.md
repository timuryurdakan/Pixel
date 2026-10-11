# Pixel Office

Pixel Office, tarayıcıda çalışan pixel-art bir sanal ofistir. Bilgisayarınızda çalışan her **Claude Code** oturumu ve alt-ajanı, ofiste küçük bir karakter olarak görünür. Ajan çalışırken masasında klavyeye yazar, düşünürken başının üstünde "..." balonu çıkar. Sizden onay beklediğinde kırmızı bir "!" yanıp söner. Turu bittiğinde kahve makinesine gider, uzun süre sessiz kalırsa yatakta uyur. Oturum kapanınca kapıdan çıkar.

İki şekilde kullanılır:

- **Yerel mod** (v1): `python3 server.py`. Yalnızca sizin bilgisayarınızdaki oturumlar görünür. Kurulum yok, hesap yok.
- **Bulut Ofis** (v2): Vercel'e ücretsiz yüklediğiniz bir site. Arkadaşlarınız tek bir bağlantıyla aynı ofise girer ve birbirlerinin ajanlarını canlı görür. Masaüstü uygulaması olarak yüklenebilir ve her zaman üstte duran bir mini pencerede açılabilir.

![Pixel Office demo modunda: masalarda çalışan, düşünen ve onay bekleyen ajanlar, kahve makinesinde bir ajan; sağda ajan listesi](docs/screenshot.png)

*Yerel mod, demo. Masalarda araç ikonu (çalışıyor), "..." (düşünüyor) ve "!" (kullanıcıyı bekliyor) balonları var, kahve makinesinin önünde mola veren bir ajan duruyor. Sağda durum sayaçları ve ajan listesi var. Bir karaktere tıklayınca ayrıntılar açılır.*

> **English summary:** Pixel Office is a zero-dependency (Python stdlib + vanilla JS) web app that shows AI coding agents as pixel-art office workers: typing at a desk while working, "..." while thinking, a blinking "!" while waiting for you, a coffee break when idle, sleeping when done. **Local mode:** `python3 server.py` watches your Claude Code transcripts under `~/.claude/projects` (read-only) and serves http://localhost:8765 (`--demo` for fake agents). **Cloud Office (v2):** deploy the repo to Vercel Hobby (GitHub import, no build settings) with the free Upstash Redis integration and a `PIXEL_SECRET` env var; create a room, share the invite link, and each friend runs one command that installs a privacy-filtering Claude Code hook (Python or built-in Windows PowerShell). Only state, tool name, a fixed label, the project folder name and the owner name ever leave a machine. Local AI agents (Ollama, LM Studio, aider, …) can report too via `pixel_agent.py`. Installable PWA with an always-on-top mini window (Document Picture-in-Picture). MIT licensed.

## İçindekiler

1. [Gereksinimler](#gereksinimler)
2. [Yerel mod](#yerel-mod)
3. [Bulut Ofis (v2)](#bulut-ofis-v2)
   - [Adım adım dağıtım](#adım-adım-dağıtım-vercel--upstash)
   - [Arkadaşlar nasıl katılır](#arkadaşlar-nasıl-katılır)
   - [Uygulama olarak yükleme ve mini pencere](#uygulama-olarak-yükleme-ve-mini-pencere)
   - [Claude Code ve yerel yapay zeka ajanlarını bağlama](#claude-code-ve-yerel-yapay-zeka-ajanlarını-bağlama)
   - [Gizlilik ve güvenlik](#gizlilik-ve-güvenlik)
   - [Ücretsiz plan limitleri](#ücretsiz-plan-limitleri)
4. [Ajanları yeniden adlandırma](#ajanları-yeniden-adlandırma)
5. [Durumlar nasıl belirleniyor?](#durumlar-nasıl-belirleniyor)
6. [Proje yapısı](#proje-yapısı)
7. [Testler](#testler)
8. [Sorun giderme](#sorun-giderme)
9. [Bilinen sınırlamalar](#bilinen-sınırlamalar)

## Gereksinimler

- **Python 3.11 veya üzeri** (yerel sunucu ve geliştirme sunucusu için). Proje yalnızca standart kütüphaneyi kullanır, `pip install` gerekmez. 3.11 alt sınırı `enum.StrEnum` ve `datetime.fromisoformat` içindeki `Z` desteğinden gelir. Geliştirme ve testler Python 3.14 ile yapıldı. Vercel'de Python 3.12 kullanılır (`.python-version`).
- Arkadaşların bilgisayarlarındaki kanca istemcisi **Python 3.8+** ister. Python yoksa Windows'la gelen **PowerShell 5.1** yeterlidir.
- **Güncel bir tarayıcı** (Chrome, Edge, Firefox, Safari). Mini pencere en iyi Chrome ve Edge'de çalışır.

Node.js, derleme adımı, CDN ya da harici resim **yoktur**. Tüm sprite'lar JavaScript içinde prosedürel olarak üretilir.

## Yerel mod

```bash
cd Pixel                     # depoyu klonladığınız klasör
python3 server.py            # gerçek Claude Code oturumlarını izler (salt-okunur)
python3 server.py --demo     # gerçek veri olmadan, sahte ajanlarla demo
```

Ardından tarayıcıda **http://localhost:8765** adresini açın. Durdurmak için `Ctrl+C` yeterli.

| Seçenek | Varsayılan | Açıklama |
|---|---|---|
| `--demo` | kapalı | Gerçek transcript'ler yerine 5 sahte ajan çalıştırır. Ajanlar tüm durumlar arasında döner, biri alt-ajan başlatır, uyuyan ajanlar çıkıp yerlerine yenileri gelir. |
| `--port PORT` | `8765` | HTTP portu |
| `--host HOST` | `127.0.0.1` | Dinlenecek adres. Ağdan erişmek için `0.0.0.0` verebilirsiniz, ama transcript özetleri o ağdaki herkese açılır. |
| `--projects-dir DİZİN` | `~/.claude/projects` | İzlenecek Claude Code proje dizini |
| `--poll SN` | `1.0` | Dosya yoklama aralığı (saniye) |
| `--names-file DOSYA` | `~/.local/share/pixel-office/names.json` | Arayüzden verilen adların saklandığı dosya (ilk ad değişikliğinde oluşur) |
| `--write-key ANAHTAR` | yok | `POST /api/status` ve ad değişikliği için anahtar. Loopback dışında dinlerken zorunludur (verilmezse üretilir). |
| `-v`, `--verbose` | kapalı | Ayrıntılı günlük (hangi oturumların izlendiği vb.) |
| `--version` | | Sürümü yazdırır |

Örnek: `python3 server.py --port 9000 --projects-dir ~/.claude/projects`

Üst çubukta mod (**CANLI MOD** / **DEMO MOD**) ve bağlantı durumu (**Bağlı** / **Yeniden bağlanıyor…**) yer alır. Sunucu yeniden başlatılırsa sayfa kendiliğinden yeniden bağlanır. Adrese `?debug` eklerseniz FPS'i ve karakter durumlarını gösteren bir hata ayıklama katmanı açılır.

Yerel sunucu da bulutla aynı `POST /api/status` ucunu sunar. Böylece Ollama, aider ya da kendi betikleriniz de yerel ofiste görünebilir (bkz. [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md)).

## Bulut Ofis (v2)

![Bulut Ofis: "Demo Oda" adlı odada üç arkadaşın ajanları; etiketlerde sahip ve proje adı, sağda sahibe göre gruplanmış ajan listesi ve ayrıntılar](docs/screenshot-cloud.png)

*Bulut odası. Her karakterin üstünde `sahip·proje` yazar, her sahibin kendi gömlek rengi vardır. Sağdaki liste sahibe göre gruplanır. Üst çubukta Davet et, Ajan bağla, Mini pencere ve Uygulamayı yükle düğmeleri var.*

Nasıl çalışır, kısaca:

- Site Vercel'in ücretsiz **Hobby** planında çalışır. `web/` klasörü statik dosya olarak, `api/*.py` dosyaları Python fonksiyonu olarak yayınlanır.
- Odaların durumu ücretsiz **Upstash Redis** veritabanında tutulur.
- Tarayıcılar odayı birkaç saniyede bir yoklar (oda aktifken 10 sn, sakinken 30 sn). Yeni açılan boş bir odada ve "Ajan bağla" penceresi açıkken 5 sn'de bir yoklanır, böylece ilk ajan birkaç saniyede görünür.
- Her arkadaşın bilgisayarındaki küçük bir kanca, Claude Code olaylarını **orada** süzer ve yalnızca durumu gönderir.

### Adım adım dağıtım (Vercel + Upstash)

Bunları **bir kez**, ofisi kuracak kişi yapar. Toplam süre yaklaşık 15 dakika. Kredi kartı gerekmez.

**1. git'i kurun ve projeyi GitHub'a yükleyin.**

```bash
sudo apt install git                       # Ubuntu/Debian. macOS: xcode-select --install
git config --global user.name "Adınız"
git config --global user.email "eposta@ornek.com"
```

[github.com](https://github.com) adresinde (gerekirse ücretsiz hesap açın) sağ üstteki **+ → New repository** ile yeni bir depo oluşturun:
- Ad: örneğin `pixel-office`.
- **Private** seçebilirsiniz, Vercel özel depoları da içe aktarır.
- README, .gitignore ve lisans **eklemeyin**, bunlar projede zaten var.

Depoyu **kişisel hesabınızda** açın. Vercel'in ücretsiz planı GitHub organizasyonlarına ait depoları içe aktaramaz.

Sonra proje klasöründe:

```bash
cd Pixel
git init
git add .
git commit -m "Pixel Office"
git branch -M main
git remote add origin https://github.com/<kullanıcı-adınız>/pixel-office.git
git push -u origin main
```

GitHub `push` sırasında parola yerine bir **token** ister. GitHub → Settings → Developer settings → Personal access tokens bölümünden `repo` yetkili bir token oluşturup parola yerine onu yapıştırın. Alternatif olarak GitHub Desktop'ı ya da depo sayfasındaki **"uploading an existing file"** bağlantısını kullanabilirsiniz. Dosyaları sürükleyip bırakırken `.gitignore`, `.vercelignore` ve `.python-version` gibi gizli dosyaları da eklemeyi unutmayın.

`.gitignore` sayesinde `__pycache__`, `names.json` (yerel oturum adları), `.vercel` ve `.env` dosyaları depoya girmez.

**2. Vercel'de projeyi içe aktarın.**

1. [vercel.com](https://vercel.com) adresinde **GitHub ile** ücretsiz hesap açın (Hobby planı).
2. **Add New… → Project** seçin ve listede `pixel-office` deposunu bulup **Import** deyin. Depo görünmüyorsa "Adjust GitHub App Permissions" ile Vercel'e o depoya erişim verin.
3. Ayarlara **dokunmayın**: Framework Preset "Other", Root Directory `./`, Build ve Install Command boş kalabilir. Gerekli her şey `vercel.json` içinde tanımlı (statik dosyalar `web/`, fonksiyonlar `api/`, bölge Frankfurt).
4. **Deploy** deyin. İlk dağıtım yaklaşık bir dakika sürer. Henüz veritabanı olmadığı için oda oluşturma çalışmaz, sıradaki adımlar bunu tamamlar.

**3. Upstash Redis'i ekleyin (Vercel Marketplace).**

1. Vercel'de projenizi açın → üstteki **Storage** sekmesi → **Create Database** (ya da **Browse Marketplace**).
2. **Upstash → Upstash for Redis** seçin.
3. Plan **Free**, bölge **Frankfurt (eu-central-1)** olsun (fonksiyonlarla aynı yer, daha hızlı).
4. Oluşturduktan sonra veritabanını projeye bağlayın (**Connect Project**, tüm ortamlar seçili kalsın).

Vercel bağlantı bilgilerini (`KV_REST_API_URL` / `KV_REST_API_TOKEN` ya da `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`) ortam değişkeni olarak kendisi ekler. Kod iki adlandırmayı da tanır, sizin bir şey yazmanız gerekmez.

**4. Ortam değişkenlerini girin.**

Önce gizli anahtarı kendi bilgisayarınızda üretin:

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(32))"
```

Çıkan metni kopyalayın. Vercel'de **Settings → Environment Variables** bölümüne gidin ve şunları ekleyin (Environment: en azından Production):

| Ad | Değer | Zorunlu mu? |
|---|---|---|
| `PIXEL_SECRET` | yukarıda ürettiğiniz metin (en az 32 karakter) | **Evet** |
| `PIXEL_CREATE_TOKEN` | oda oluşturmak için bir parola, ör. `kahve-2026-oda` | Önerilir |
| `PIXEL_PUBLIC_URL` | `https://<proje>.vercel.app` (davet bağlantılarında hep bu adres kullanılır) | İsteğe bağlı |

> ⚠️ **`PIXEL_SECRET`'i bir kez belirleyin ve sonra değiştirmeyin.** Tüm davet bağlantılarındaki yazma anahtarları bundan türetilir. Değiştirirseniz herkesin kurulumu bozulur ve bağlantılar yeniden dağıtılmalıdır. Bu değeri kimseyle paylaşmayın ve GitHub'a yazmayın.

`PIXEL_CREATE_TOKEN` tanımlıysa sitede oda oluşturmak için bu parola sorulur. Tanımlı değilse adresi bilen herkes oda oluşturabilir (yine de saatte 5 oda ile sınırlıdır).

**5. Yeniden dağıtın ve test edin.**

Ortam değişkenleri yalnızca **yeni** dağıtımlarda geçerli olur:
1. **Deployments** sekmesi → en üstteki dağıtımın **⋯** menüsü → **Redeploy**.
2. Bittikten sonra `https://<proje>.vercel.app/api/health` adresini açın. Şunu görmelisiniz:

   ```json
   {"ok":true,"version":"2.0.0","storage":"upstash","storageOk":true}
   ```

   `"storage":"unconfigured"` görürseniz 3. adımı, oda oluştururken hata alırsanız 4. adımı kontrol edin.
3. `https://<proje>.vercel.app` adresini açın, adınızı yazıp **Oda oluştur** deyin.
4. Açılan **Davet et** penceresinden katılma bağlantısını kopyalayın.
5. **Ajan bağla** penceresindeki komutu kendi bilgisayarınızda çalıştırın ve Claude Code'u yeniden başlatın. Birkaç saniye içinde ofiste "Kurulum testi" karakteri, sonra Claude Code oturumunuz görünür.

Hep **production** adresini (`<proje>.vercel.app` ya da kendi alan adınız) kullanın. Vercel'in "preview" dağıtım adresleri ücretsiz planda varsayılan olarak giriş ister ve arkadaşlarınız açamaz.

Sonraki güncellemeler için `git push` yeterlidir, Vercel her push'ta yeniden dağıtır. Yeni sürüm yayımlarken `web/sw.js` içindeki `VERSION` değerini artırın; böylece yüklü uygulamalar yeni dosyaları hemen alır.

### Arkadaşlar nasıl katılır

1. Arkadaşınız davet bağlantısını açar (`https://<proje>.vercel.app/?room=…#key=…`). Anahtar tarayıcıya kaydedilir ve adres çubuğundan silinir.
2. **Ajan bağla** düğmesine basar, adını yazar ve işletim sistemine uygun tek satırlık komutu kopyalayıp terminalde çalıştırır:
   - **macOS / Linux:** `curl … | sh -s -- …` (Python 3.8+ gerekir, yoksa `pwsh` kullanılır).
   - **Windows:** PowerShell ya da Komut İstemi'nde `powershell … irm …/install.ps1 …`. **Python gerekmez.**
3. Açık Claude Code oturumlarını yeniden başlatır. Hepsi bu.

Komutu çalıştırmadan önce betiği okumak isteyenler için pencerede `install.sh` / `install.ps1` bağlantıları vardır. Kurulum `~/.claude/settings.json` dosyasının bir kez yedeğini alır (`settings.json.pixel-backup`), başka kancalara dokunmaz, tekrar çalıştırılabilir ve kaldırılabilir:

```bash
python3 ~/.pixel-office/pixel_hook.py doctor      # her şey yolunda mı?
python3 ~/.pixel-office/pixel_hook.py uninstall   # yalnızca Pixel Office kancalarını kaldırır
```

**Sadece izleme bağlantısı** (`#key=` olmadan) ile açılan oda yalnızca izlenebilir. Ajan bağlanamaz, adlar değiştirilemez. Ekranda göstermek ya da merak eden birine vermek için bunu kullanın.

### Uygulama olarak yükleme ve mini pencere

- **Uygulamayı yükle:** Chrome ve Edge'de odadaki **Uygulamayı yükle** düğmesi (ya da adres çubuğundaki yükleme simgesi) Pixel Ofis'i ayrı bir masaüstü penceresi olarak yükler. Uygulama açılınca son oda doğrudan açılır. Android'de "Ana ekrana ekle", iPhone/iPad'de Safari'de **Paylaş → Ana Ekrana Ekle** kullanılır.
- **Mini pencere:** **Mini pencere** düğmesi ofisi küçük, **her zaman üstte duran** bir pencerede açar (Chrome/Edge 116+, Firefox 151+). Pencereyi bir kez ekranın sağ altına sürükleyin; tarayıcı genellikle konumu hatırlar. Ana pencereyi küçültebilirsiniz, mini pencere canlı kalır. Mini pencere kapanınca ofis ana pencereye döner.
- Safari ve eski tarayıcılarda bunun yerine sağ alta yerleşen küçük bir açılır pencere açılır. Bu pencere her zaman üstte kalmaz.
- Sekme gizliyse ve mini pencere de kapalıysa yoklama 30 sn sonra durur. Bu, ücretsiz kotayı korur.

### Claude Code ve yerel yapay zeka ajanlarını bağlama

- **Claude Code:** yukarıdaki tek satırlık kurulum yeterli.
- **Yerel yapay zeka ajanları** (Ollama, LM Studio, llama.cpp, aider, Open Interpreter, kendi betikleriniz): `pixel_agent.py` bir kütüphane, bir komut sarmalayıcısı (`pixel-run`) ve akışı bozmayan bir OpenAI/Ollama ara sunucusu (`pixel-proxy`) içerir. Hazır komutlar "Ajan bağla → Genel API" sekmesinde, gerçek adres ve anahtarla doldurulmuş olarak gösterilir.
- **Her şey için:** `POST /api/status` genel API'si (curl, Python, PowerShell, JavaScript örnekleri).
- Kurulum yapılamayan bilgisayarlar için "Gelişmiş" sekmesinde bir HTTP kancası var. **Bu yöntem gizlilik garantisini bozar** (ham kanca verisi sunucuya gider), yalnızca son çare olarak kullanın.

Ayrıntılar, tüm seçenekler ve örnekler: [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md).

### Gizlilik ve güvenlik

**Ne gönderilir?** Kanca, Claude Code olaylarını sizin bilgisayarınızda süzer. Sunucuya yalnızca şunlar gider:
- durum (çalışıyor, düşünüyor, bekliyor…)
- araç adı (ör. `Bash`, `Edit`; MCP araçları yalnızca "MCP" olarak)
- sabit Türkçe bir etiket (ör. "İzin bekliyor")
- proje klasörünün **adı** (tam yolu değil)
- sizin adınız
- hash'lenmiş oturum kimlikleri

İstemleriniz, kod, dosya içerikleri, komutlar, araç çıktıları, Claude'un yanıtları ve dosya yolları **gönderilmez**. Bu kural testlerde bir "kanarya" metniyle doğrulanır. Proje adını gizlemek ya da takma ad vermek için `~/.pixel-office/config.json` içindeki `hide_projects` ve `project_aliases` ayarlarını kullanın. Bir klasörde hiç rapor gönderilmesin istiyorsanız oraya boş bir `.pixel-office-ignore` dosyası koyun. Tek seferlik kapatmak için `PIXEL_OFFICE_DISABLE=1` ortam değişkeni yeterli.

**Güvenlik uyarıları:**

- 🔑 **Davet (katılma) bağlantısını gizli tutun.** İçindeki `#key=` kısmı odaya yazma yetkisi verir: bağlantıyı alan herkes odaya sahte ajan ekleyebilir ve adları değiştirebilir. Bağlantıyı yalnızca güvendiğiniz kişilerle, özel mesajla paylaşın. Herkese açık yerlere yalnızca **izleme bağlantısını** koyun.
- **Bağlantı sızarsa yeni bir oda oluşturun** ve yeni davet bağlantısını arkadaşlarınıza gönderin. Kişi başına anahtar iptali yoktur; bu, küçük bir arkadaş uygulaması için bilinçli bir sadeleştirmedir. Eski oda 30 gün boyunca hiç yazılmazsa kendiliğinden silinir.
- Oda kimliği (izleme bağlantısı) odayı **görme** yetkisi verir. Ajan durumları, proje adları ve kişi adları dışında bir şey görünmez, ama yine de bunu da herkese dağıtmayın.
- `PIXEL_SECRET` ortam değişkenini kimseyle paylaşmayın ve depoya yazmayın. Sızdıysa değiştirin; bu durumda herkes kurulumu yeni bir bağlantıyla yeniden yapmalıdır.
- Siteyi herkese açık bir adreste tutuyorsanız `PIXEL_CREATE_TOKEN` ile oda oluşturmayı parolayla koruyun.
- Yazma anahtarı adres çubuğundan silinir, sunucuya, CDN'e ve `Referer` başlığına hiç gitmez, servis çalışanının önbelleğine yazılmaz. Tarayıcıda `localStorage`'da ve arkadaşınızın bilgisayarında `~/.pixel-office/config.json` (izin 0600) dosyasında saklanır. Odayı bu tarayıcıdan silmek için giriş ekranındaki "Son odalar" listesinde **Unut** düğmesini kullanın.

### Ücretsiz plan limitleri

Varsayılan ayarlar 10 arkadaşın günde 8 saat ofisi açık tuttuğu, 4 saat aktif Claude Code kullandığı bir senaryoya göre hesaplandı (ayrıntılar: [`docs/CLOUD.md`](docs/CLOUD.md) §12):

| Kaynak | Ücretsiz limit | Zorlama senaryosu (30 gün) | Gerçekçi (22 iş günü) |
|---|---|---|---|
| Vercel CDN istekleri | 1M / ay | ≈ 787K (%79) | ≈ 577K (%58) |
| Vercel fonksiyon çağrıları | 1M / ay | ≈ 345K (%35) | ≈ 253K (%25) |
| Vercel Active CPU | 4 saat / ay | ≈ 1,5 saat | ≈ 1 saat |
| Upstash Redis komutları | 500K / ay | ≈ 363K (%73) | ≈ 266K (%53) |

**Kullanımı nasıl kontrol ederim?**
- **Vercel:** proje → **Usage** sekmesi (ya da hesap menüsü → Usage). En kritik satır "Edge Requests / CDN Requests".
- **Upstash:** [console.upstash.com](https://console.upstash.com) → veritabanınız → **Usage** (aylık komut sayısı).
- İlk ay, haftada bir bakmanız önerilir.

Limite yaklaşırsanız Vercel'de şu ortam değişkenleriyle yoklamayı yavaşlatabilirsiniz (istemcileri güncellemek gerekmez, yeniden dağıtmak yeterli):
- `PIXEL_POLL_ACTIVE_MS=15000` (varsayılan 10000): CDN isteklerini yaklaşık %28 azaltır.
- `PIXEL_POLL_CALM_MS=45000` (varsayılan 30000).

Vercel Hobby'de limit aşılırsa hesap duraklatılabilir (ücret kesilmez). Upstash limiti aşılırsa oda okuma/yazma geçici olarak 503 döner, ofis "Yeniden bağlanıyor…" gösterir. Yerel mod bundan etkilenmez. Hobby planı yalnızca ticari olmayan kullanım içindir.

## Ajanları yeniden adlandırma

Bir karaktere tıklayın, ayrıntılar panelinde adın yanındaki **✎** düğmesine basın, yeni adı yazıp **Enter**'a basın (Esc iptal eder). Ad en fazla 24 karakterdir.

- Varsayılan olarak ad **yalnızca o ajana** verilir. Hook güncellemeleri adı asla ezmez.
- **"Yeni oturumlara da ver"** kutusunu işaretlerseniz ad varsayılan olur: bulutta o kişinin sonraki oturumlarına, yerel modda o projedeki sonraki oturumlara.
- **Varsayılana dön** bağlantısı verilen adı kaldırır.
- Bulutta ad değişikliği odadaki herkese birkaç saniye içinde yansır. ✎ yalnızca katılma bağlantısıyla açılan odalarda görünür.
- Yerel modda adlar `~/.local/share/pixel-office/names.json` dosyasında saklanır (macOS: `~/Library/Application Support/pixel-office/`, Windows: `%APPDATA%\pixel-office\`) ve açık tüm sekmelere anında yansır. Eski yöntem olan proje kökündeki `names.json` (`oturum-id → isim`) dosyası da çalışmaya devam eder.

## Durumlar nasıl belirleniyor?

**Yerel mod:** sunucu her oturumun transcript dosyasını (`~/.claude/projects/<proje>/<oturum-id>.jsonl`) yaklaşık saniyede bir, yalnızca yeni eklenen satırları okuyarak takip eder. Alt-ajanlar `<oturum-id>/subagents/agent-*.jsonl` dosyalarından okunur. Dosyalara **hiçbir zaman yazılmaz**. Durum, son satırlardan aşağıdaki kurallarla çıkarılır (ilk uyan kural geçerlidir):

| Durum | Ne zaman? | Ofiste |
|---|---|---|
| **Uyuyor** | Oturum kapandı (`cost-state` satırı), alt-ajanın sonucu ana ajana döndü, ya da 10 dk boyunca dosyaya hiçbir şey yazılmadı | Yatağa gider, Zzz |
| **Kullanıcıyı bekliyor** | `AskUserQuestion`/`ExitPlanMode` açık, ya da hızlı bir araç (Edit, Write, Read…) 8 sn'dir sonuçlanmadı ve dosya da 8 sn'dir sessiz (büyük olasılıkla izin istemi) | Masada oturur, kırmızı "!" yanıp söner |
| **Çalışıyor** | Sonucu henüz gelmemiş bir araç çağrısı var. Bash, Agent, WebFetch gibi uzun araçlar her zaman bu durumda sayılır. | Masada yazar, araç ikonu |
| **Kahve molasında** | Model turunu bitirdi (`end_turn`) ya da kullanıcı kesti, 3 dk'dan kısa süre önce | Kahve makinesine gider |
| **Uyuyor** | Tur 3 dk'dan uzun süre önce bitti (alt-ajanlar tur bitince hemen uyur) | Yatağa gider |
| **Düşünüyor** | Yeni istem geldi, araç sonucu döndü ya da model yanıt üretiyor | Masada oturur, "..." |

30 dk boyunca hiç yazılmayan oturumlar ofisten çıkar (kapıya yürür). Biten alt-ajanlar 2 dk uyuduktan sonra ayrılır. Ayrıntılı kurallar: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §2.

**Bulut:** durum doğrudan Claude Code kancalarından gelir (istem gönderildi → düşünüyor, araç başladı → çalışıyor, izin istendi → bekliyor, tur bitti → kahve, oturum kapandı → kapıdan çıkış). Kahvedeki ajan 3 dk sonra uyur, 30 dk ses vermeyen ajan uyur, 45 dk sonra ofisten çıkar. Eşleme tablosu: [`docs/CLOUD.md`](docs/CLOUD.md) §6–§7.

## Proje yapısı

```
Pixel/
├── server.py               Yerel mod giriş noktası
├── pixel_office/           Yerel sunucu: transcript izleme, durum makinesi, SSE, adlar, yerel durum API'si
├── dev_server.py           Bulut modunu yerelde çalıştıran geliştirme sunucusu (web/ + /api/*)
├── api/*.py                Vercel fonksiyonları (ince giriş noktaları)
├── pixel_cloud/            Bulut backend'i: uçlar, doğrulama, durum türetme, Upstash/bellek depolama, hız limitleri
├── vercel.json, .vercelignore, .python-version
├── web/
│   ├── index.html, css/, js/   Arayüz (vanilla ES modülleri): ofis, karakterler, panel, bulut, mini pencere
│   ├── hook/               Arkadaşların indirdiği istemciler: pixel_hook.py/.ps1, install.sh/.ps1, pixel_agent.py
│   ├── sw.js, manifest.webmanifest, icons/   PWA
│   └── sprite-preview.html Tüm sprite'ların önizleme sayfası
├── tools/make_icons.py     PWA ikonlarını üretir
├── tests/                  unittest testleri
├── docs/                   ARCHITECTURE.md (v1), CLOUD.md (v2), INTEGRATIONS.md, ekran görüntüleri
├── PROJECT.md              Proje planı ve ilerleme günlüğü
└── LICENSE                 MIT
```

Bulut modunu dağıtmadan yerelde denemek için:

```bash
python3 dev_server.py --demo      # http://127.0.0.1:8787, 3 sahte arkadaşlı "Demo Oda" (bağlantı terminalde yazar)
python3 dev_server.py             # boş; oda oluşturup kendi kancanızı bağlayabilirsiniz
```

Geliştirme sunucusu verileri bellekte tutar ve her başlatmada rastgele bir `PIXEL_SECRET` kullanır (yani anahtarlar yeniden başlatınca değişir).

## Testler

```bash
python3 -m unittest discover -s tests
```

251 test, ağ bağlantısı gerektirmeden çalışır (`pwsh` yoksa 1 test atlanır). Kapsam:
- yerel mod: transcript ayrıştırma, durum eşikleri, dosya izleme, SSE
- bulut: tüm uçlar ve hata kodları, CORS, önbellek başlıkları, hız limitleri, sahte bir Upstash sunucusuna karşı depolama
- kanca istemcisi: 61 eşleme vektörü, gizlilik kanarya testi, `settings.json` yükleyicisi (gerçek `~/.claude`'a dokunmadan)
- `pixel_agent.py`, `pixel-run`, `pixel-proxy`
- yeniden adlandırma, yerel durum API'si
- dağıtım: Vercel'in fonksiyonları algılayacağı biçim, `vercel.json`, `.vercelignore`, `.gitignore`, lisans

Sprite önizleme: yerel sunucu çalışırken **http://localhost:8765/sprite-preview.html**.

## Sorun giderme

**Yerel mod**

- **`Cannot listen on 127.0.0.1:8765: Address already in use`**: sunucu büyük olasılıkla zaten açık. Başka bir port seçin (`--port 8766`) ya da portu kullanan süreci bulun (`ss -ltnp | grep 8765`).
- **Ofis boş.** Yalnızca son 30 dakikada yazılmış oturumlar gösterilir. Claude Code'da bir şey yazın. `ls ~/.claude/projects` altında `.jsonl` dosyaları olmalı. `python3 server.py -v` hangi oturumların izlendiğini gösterir. Arayüzü denemek için `--demo` kullanın.
- **"Yeniden bağlanıyor…" kaybolmuyor.** Sunucu kapanmış olabilir. Yeniden başlayınca sayfa kendiliğinden bağlanır.
- **Bir ajan uzun süre "Kullanıcıyı bekliyor".** Claude Code'da bekleyen bir onay olup olmadığına bakın.

**Bulut Ofis**

- **`/api/health` 404 veriyor ya da tüm `/api/...` adresleri 404.** Vercel projesinde Framework Preset'in "Other" olduğundan ve `api/` klasörünün depoda bulunduğundan emin olun. Dağıtım günlüğünde (Deployments → dağıtım → Build Logs / Functions) `api/*.py` fonksiyonları listelenmeli.
- **`"storage":"unconfigured"`** ya da sayfada oda oluşturulamıyor (`storage_not_configured`): Upstash entegrasyonu projeye bağlı değil, `PIXEL_SECRET` eksik ya da 32 karakterden kısa, ya da değişkenleri ekledikten sonra **Redeploy** yapılmadı.
- **`"storageOk":false`**: Upstash'e ulaşılamıyor ya da aylık komut limiti doldu. Upstash konsolunu kontrol edin.
- **"Oluşturma parolası yanlış."** `PIXEL_CREATE_TOKEN` değerini girin (büyük/küçük harf duyarlı).
- **"Oda bulunamadı".** Bağlantı eksik kopyalanmış olabilir.
- **Kurulumdan sonra ajanım görünmüyor.**
  1. `python3 ~/.pixel-office/pixel_hook.py doctor` çalıştırın: kancalar, sunucu erişimi ve son gönderimler görünür (anahtar maskelenir).
  2. Claude Code'u gerçekten yeniden başlattığınızdan emin olun (kancalar oturum açılışında okunur).
  3. `python3 ~/.pixel-office/pixel_hook.py test` bir test karakteri gönderir.
  4. Proje klasöründe ya da üst klasörlerinde `.pixel-office-ignore` olmadığını kontrol edin.
- **Windows'ta "betik çalıştırma devre dışı" hatası.** Komut zaten `-ExecutionPolicy Bypass` içerir; şirket bilgisayarlarında bu da engellenmiş olabilir. O durumda "Gelişmiş" sekmesine bakın (gizlilik uyarısını okuyun).
- **401 / "Missing or invalid write key".** Bağlantı izleme bağlantısıdır ya da `PIXEL_SECRET` değiştirilmiştir. Katılma bağlantısıyla kurulumu yeniden yapın.
- **Arkadaşım siteyi açamıyor, Vercel giriş sayfası çıkıyor.** Preview adresini paylaşmışsınız. Production adresini (`<proje>.vercel.app`) kullanın.
- **Mini pencere düğmesi yok ya da pencere her zaman üstte değil.** Chrome ya da Edge kullanın. Diğer tarayıcılarda her zaman üstte kalmayan bir açılır pencere açılır; mobilde düğme gizlenir.
- **Yeni sürüm yüklenmiyor.** Yüklü uygulama ilk açılışta eski dosyaları gösterip arka planda yenilerini indirir; bir kez daha açın. Yayımlarken `web/sw.js` içindeki `VERSION` değerini artırmayı unutmayın.

## Bilinen sınırlamalar

- Yerel modda durumlar, transcript'lerden sezgisel kurallarla çıkarılır, kısa süreli yanlış sınıflandırmalar olabilir. Canlı modda otomatik isimler oturum kimliğinden türetilir ve çakışabilir (✎ ile ad verebilirsiniz).
- Ofiste 9 masa, 3 kahve noktası ve 4 yatak vardır. Fazlası için yedek noktalar kullanılır. Bir oda en fazla 48 ajan alır.
- Bulutta hız limitleri sunucu örneği başınadır (en iyi çaba). Asıl koruma yazma anahtarı ve oda oluşturma parolasıdır.
- Kişi başına anahtar iptali yoktur: bağlantı sızarsa yeni oda açılır.
- Mini pencerenin konumunu site belirleyemez; bir kez sürüklemeniz gerekir. Safari'de her zaman üstte duran mini pencere yoktur.
- Windows'ta `pixel-run` boşta algılaması yapamaz (pty yok), süreç boyunca "çalışıyor" gösterir.
- PowerShell istemcisi (`pixel_hook.ps1`) geliştirme makinesinde `pwsh` olmadığı için yalnızca statik olarak incelendi; ilk Windows kurulumunda `doctor` çıktısını kontrol edin.

## Lisans

[MIT](LICENSE) © 2026 Timur Yurdakan
