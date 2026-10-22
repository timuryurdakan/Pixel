# Yayına Alma Kontrol Listesi (v3)

Bu belge, Pixel Office v3 "Ajan Kontrol Merkezi" yayına alma adımlarının tek ve güncel kaynağıdır (README ve `PROJECT.md` buraya başvurur).

Sırayla yapın. Node.js kurmanız **gerekmez**. Ekrandaki düğme adları zamanla değişebilir; değerler değişmez.

---

## 1. Röle ve Alan Adı Hazırlığı

1. **Röle adresini sabitleyin** (Cloudflare'de seçtiğiniz `workers.dev` alt alan adıyla; hesabınız yoksa önce 3. adımı yapın):
   ```bash
   python3 tools/set_relay_host.py <alt-alan>
   ```
   Çıktıdaki `wss://pixel-relay.<alt-alan>.workers.dev` adresini not edin. *(Özel alan adı kullanılıyorsa örn. `wss://relay.pixeloffice.com.tr`).*
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
     | Build command | `npm install --no-audit --no-fund && npm test` |
     | Deploy command | `npx wrangler deploy` |
     | Build watch paths → Include | `relay/*` |

   - **Save and Deploy**'a tıklayın. Derleme günlüğünde `fail 0` görünmelidir.
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
   - `PIXEL_RELAY_URL` = `wss://pixel-relay.<alt-alan>.workers.dev` (veya özel alan adı `wss://relay.pixeloffice.com.tr`; sonunda `/` olmamalıdır)
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
   curl https://pixel-relay.<alt-alan>.workers.dev/health
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
   RELAY_SECRET='<secret>' python3 tests/relay_conformance.py --url wss://pixel-relay.<alt-alan>.workers.dev
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
