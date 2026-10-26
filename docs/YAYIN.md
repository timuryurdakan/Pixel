# Yayına Alma Kontrol Listesi (v3)

Bu belge, Pixel Office v3 "Ajan Kontrol Merkezi" yayına alma adımlarının tek ve güncel kaynağıdır (README ve `PROJECT.md` buraya başvurur).

Sırayla yapın. Node.js kurmanız **gerekmez**. Ekrandaki düğme adları zamanla değişebilir; değerler değişmez.

> **Röle adresi özel bir alan adı olmalı.** Türkiye'deki bazı internet sağlayıcıları `*.workers.dev` adreslerini SNI ile
> engelliyor (22.10.2026'da doğrulandı: aynı Cloudflare IP'sine başka bir alan adıyla TLS açılıyor, `*.workers.dev` ile
> açılmıyor). Bu yüzden canlı röle `wss://relay.pixeloffice.com.tr` adresindedir; `workers.dev` adresi yalnızca ilk kurulum
> ve deneme içindir. Aşağıda `<röle-adresi>` bu özel alan adı demektir (ör. `relay.pixeloffice.com.tr`).

---

## 1. Röle ve Alan Adı Hazırlığı

1. **Röle adresini sabitleyin** (özel alan adıyla; alan adını Cloudflare'e bağlamak 3a adımında):
   ```bash
   python3 tools/set_relay_host.py wss://relay.pixeloffice.com.tr
   ```
   Bu komut `vercel.json` CSP'sindeki `connect-src` değerini yazar. *(Yalnızca deneme için `workers.dev` alt alan adı da
   verilebilir: `python3 tools/set_relay_host.py <alt-alan>` → `wss://pixel-relay.<alt-alan>.workers.dev`; bazı
   sağlayıcılarda engellidir.)*
2. **GitHub'a gönderin:**
   ```bash
   git add -A && git commit -m "v3 yayına alma" && git push
   ```
   (Vercel bu push ile kendiliğinden dağıtır; ortam değişkenleri henüz eklenmemişse sorun değil.)

## 2. Cloudflare Workers Dağıtımı

3. **Cloudflare Workers Builds:**
   - Hesap yoksa https://dash.cloudflare.com/sign-up (ücretsiz, kredi kartı gerekmez) → **Workers & Pages** → `workers.dev` alt alan adını seçin.
   - **Workers & Pages → Create → Import a repository → Connect GitHub** → yalnızca `timuryurdakan/Pixel` deposuna izin verin (*Only select repositories*) → depoyu seçin ve ayarları yapın:

     | Alan | Değer |
     |---|---|
     | Project name / Worker name | `pixel-relay` |
     | Production branch | `main` |
     | Root directory (Advanced settings) | `relay` |
     | Build command | `npm ci --no-audit --no-fund && npm test` |
     | Deploy command | `npx wrangler deploy` |
     | Build watch paths → Include | `relay/*` |

   - **Save and Deploy**'a tıklayın. Derleme günlüğünde `fail 0` görünmelidir.
3a. **Özel alan adını Worker'a bağlayın** (zorunlu; `workers.dev` engellenebilir):
   - Alan adının DNS bölgesi Cloudflare'de olmalı. Pixel Office'te bölge 22.10.2026'da Vercel'den Cloudflare'e taşındı:
     alan adı kayıt firmasında ad sunucuları `ali.ns.cloudflare.com` ve `jerome.ns.cloudflare.com` yapıldı; sitenin
     Vercel'i gösteren kayıtları (apex ve `www`) Cloudflare'de **DNS only** (gri bulut) olarak kaldı, böylece site yine
     Vercel'den sunulur.
   - **Workers & Pages → pixel-relay → Settings → Domains & Routes → Add → Custom domain** → `relay.pixeloffice.com.tr`.
     Cloudflare kaydı ve sertifikayı kendisi oluşturur (birkaç dakika sürebilir).
   - Deneme: `curl https://relay.pixeloffice.com.tr/health` → `{"ok":true,"v":1}`.
4. **`RELAY_SECRET` üretin:**
   ```bash
   python3 -c "import secrets;print(secrets.token_urlsafe(32))"
   ```
   (43 karakterlik rastgele bir sır üretir; kimseyle paylaşmayın, depoya eklemeyin).
5. **Cloudflare'e sırrı ekleyin:**
   - Worker **pixel-relay → Settings → Variables and Secrets → Add** → Type **Secret**, ad `RELAY_SECRET`, değer 4. adımdaki üretilen sır → **Deploy**.
   - *(Not: `ALLOWED_ORIGINS` panelden eklenmez; `relay/wrangler.jsonc` içindedir).*

## 3. Vercel Ortam Değişkenleri ve CSP

6. **Vercel'e iki ortam değişkeni ekleyin** (Project → Settings → Environment Variables, **Production**):
   - `RELAY_SECRET` = 4. adımdaki **aynı** sır değeri
   - `PIXEL_RELAY_URL` = `wss://relay.pixeloffice.com.tr` (1. adımdaki adresin **aynısı**; sonunda `/` olmamalıdır)
7. **CSP kontrolü:**
   ```bash
   python3 tools/set_relay_host.py --show
   ```
   Çıktı 6. adımdaki adresi göstermelidir.
8. **Vercel'de yeniden dağıtın:**
   - Deployments sekmesinde son dağıtımın yanındaki **⋯ → Redeploy** menüsüne tıklayın (değişkenler yeni dağıtımla etkinleşir).

## 4. Canlı Servis Doğrulama

9. **Uç noktaları doğrulayın:**
   ```bash
   # 1. Röle sağlık kontrolü
   curl https://<röle-adresi>/health
   # Yanıt: {"ok":true,"v":1}

   # 2. Site yapılandırması
   curl https://www.pixeloffice.com.tr/api/config
   # Yanıt içinde "relay":{"url":"wss://...","v":1} ve "features":["control"] olmalı

   # 3. CSP başlığı kontrolü
   curl -sI https://www.pixeloffice.com.tr/ | grep -i content-security

   # 4. Bağlayıcı indirme içerik türü
   curl -sI https://www.pixeloffice.com.tr/connector/pixel-connector.pyz | grep -i content-type
   # application/zip beklenir (text/plain olsa da çalışır)

   # 5. Uyumluluk testleri
   RELAY_SECRET='<secret>' python3 tests/relay_conformance.py --url wss://<röle-adresi>
   # Yanıt: OK
   ```

## 5. İstemci Kurulumu ve Eşleştirme

10. **Bilgisayara bağlayıcıyı kurun:**
    - https://www.pixeloffice.com.tr adresini katılma bağlantısıyla açın → **Cihazlar → Cihaz ekle** menüsündeki kurulum komutunu kopyalayıp terminalde çalıştırın.
    - Ekrana basılan SHA-256 özetini README ile karşılaştırın.
    - Çalışma klasörlerini ekleyin: `pixel-connector workspace add ~/projem`
    - Servis durumunu kontrol edin: `pixel-connector doctor` (hepsi ✓ olmalı).
11. **Eşleştirme yapın:**
    - Terminalde `pixel-connector pair` çalıştırın.
    - 6 haneli kodu tarayıcıya girin.
    - Doğrulama kodları (SAS) eşleşiyorsa terminalde `e` tuşuna basın. Telefon için yeni bir kodla işlemi tekrarlayın.
12. **Claude Code ile ilk gerçek deneme:**
    - Arayüzden **Yeni ajan** (Claude Code, Düzenleyebilir) oluşturun.
    - Küçük bir görev verin; karakterin masada çalışmasını ve çıktının akmasını izleyin.
    - Görevi **Durdur** ile kesin; `pixel-connector usage` ve `~/.pixel-office/connector/audit.log` çıktısını kontrol edin.
13. **İsteğe bağlı: Ollama entegrasyonu:**
    - Ollama kurulumu ve model indirme: `ollama pull qwen2.5-coder:7b`.
    - Polkit kuralı ve `connector.json` içinde `runtimes` tanımlandıktan sonra servisi yeniden başlatın.
    - Telefondan model kontrolü ve Ollama ajanına görev verin.

## 6. Periyodik Kontroller

14. **İlk ay haftada bir** kullanım takibi yapın:
    - Cloudflare: Workers & Pages → pixel-relay → Metrics (Durable Objects istekleri, süre).
    - Vercel: Usage (Function invocations).
    - Upstash: Command usage.

## 7. Röle adresi değişirse

- Sıra: Worker'a yeni özel alan adını ekleyin → `python3 tools/set_relay_host.py wss://<yeni-adres>` + commit + push →
  Vercel'de `PIXEL_RELAY_URL`'yi aynı değere çekin → **Redeploy** → `curl https://<site>/api/config` yeni adresi göstermeli.
- Bağlayıcıda bir şey değişmez: adresi her bağlantıda sunucudan (`/api/config`) alır.
- Telefonda eski sayfa önbellekte kaldıysa Cihazlar sayfası "güvenlik politikası (CSP) röle adresini engelliyor" der (eski
  CSP yeni adrese izin vermez). Sayfayı tamamen kapatıp yeniden açın; olmazsa site verilerini temizleyin (Chrome: adres
  çubuğundaki simge → Site ayarları → Verileri temizle). Ana ekrana eklenmiş uygulama için de aynısı geçerlidir.

## 8. Yeni bağlayıcı sürümü yayımlandıktan sonra kullanıcılar nasıl haberdar olur?

- Yayın: `python3 tools/build_connector.py` (`--check` geçmeli), README'deki `pixel-connector.pyz SHA-256` satırı,
  commit + push. Vercel yeni `web/connector/pixel-connector.pyz` ve `SHA256SUMS` dosyalarını yayımlar; `SHA256SUMS`
  kanca dosyalarının (`../hook/*.py`) özetlerini de içerir.
- Kontrol: `curl -s https://www.pixeloffice.com.tr/connector/SHA256SUMS` yeni `pixel-connector.pyz` özetini göstermeli.
- **Haberdar olma (kendiliğinden güncelleme yok):** eşleşmiş tarayıcı bağlı bilgisayarın çalıştırdığı `.pyz` özetini
  (`device.info.pyz`) sunucudaki `SHA256SUMS` ile karşılaştırır. Farklıysa ofisin üstünde sarı şerit çıkar
  ("Bilgisayarınızdaki bağlayıcı eski."), cihaz kartında da aynı not görünür; bilgisayarda `pixel-connector doctor`
  "Güncelleme var" der. Şerit kapatılırsa o sürüm için bir daha çıkmaz, sonraki yayında yeniden çıkar.
- **Kullanıcının yapacağı:** bağlayıcı 3.5.0 ve sonrası → o bilgisayarda `pixel-connector update`
  (indirir, doğrular, değiştirir, servisi yeniden başlatır). 3.5.0'dan eski → bir kez Cihazlar → Cihaz ekle'deki
  kurulum komutu (şeritteki "Kurulum komutunu göster" düğmesi oraya götürür).
- Site ele geçirilse bile hiçbir bilgisayar kendiliğinden kod kurmaz: güncelleme yalnızca kullanıcı komutu
  çalıştırınca olur (tarayıcıdan tetiklenen bir güncelleme mesajı yok, `docs/V3.md` §5.4).
