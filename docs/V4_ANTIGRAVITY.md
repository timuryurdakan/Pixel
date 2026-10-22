# v4 Görevleri: Antigravity İçin Görev Dosyası

**Hazırlayan:** Claude · **Tarih:** 2026-10-21 · **Onaylayan:** kullanıcı ("yeni işleri Antigravity'ye verelim")
**Okuma sırası:** önce bu dosya, sonra [`AGENTS.md`](../AGENTS.md), [`docs/GOREV_PANOSU.md`](GOREV_PANOSU.md),
[`PROJECT.md`](../PROJECT.md) (§5 tablo ve Aşama 18–20), [`v2.md`](../v2.md) (genel inceleme raporu).

Bu dosya, v4 işlerini (görev tahtası, masadan masaya devir, iş akışı motoru) Antigravity'ye devreder. Ayrıca Claude'un
token'ı biterse yarım kalan işlerin nasıl devralınacağını ve **push'un nasıl yapılacağını** anlatır.

---

## 0. Ne zaman başlanır?

| Durum | Ne yapılır |
|---|---|
| Panoda **C3** satırı ⬜ ya da 🔄 (Claude hâlâ Aşama 20 paketleri üzerinde) | Yalnızca **A6**, **A11** ve **A12** yapılabilir (kendi dalında, push yok). Diğer görevlere başlanmaz. |
| Panoda **C3 ✅** (Claude paketleri bitirdi) | A8 → A9 → A10 sırayla. Her görev bitince §2'deki yolla push. |
| Kullanıcı "Claude'un token'ı bitti, devral" derse | Önce **A7** (Claude'un kalan paketleri), sonra A8 → A10. |

Görevler **sırayla** yapılır: biri bitmeden (testler geçmeden, push yapılmadan) diğerine başlanmaz.

**Şu anki durum (2026-10-22): C3 ✅** — Aşama 20 bitti (Paket 1–8; A11 ve A12 birleştirildi). **A8 şimdi başlayabilir.**
Bundan sonra Antigravity kendi işini §2'deki yolla kendisi push'lar (Claude birleştirmesi beklenmez). Başlamadan önce
dalına güncel `main`'i al (`git fetch origin && git merge origin/main`; kapanış commit'ini kullanıcı push'lar); A12'den sonra `main`'e Paket 8a ve Aşama 20
kapanışı (bağlayıcı 3.2.3) geldi.

---

## 1. Değişmeyen kurallar (kısa özet, ayrıntı AGENTS.md ve panoda)

- **Klasör:** her zaman `~/Desktop/Pixel-antigravity` (dal `antigravity/gorevler`). `~/Desktop/Pixel` klasörüne
  (Claude'un klasörü) **hiçbir şekilde dokunulmaz**, orada merge/checkout/commit yapılmaz.
- **Bağımlılık yok:** Python yalnızca standart kütüphane; web tarafı derleme adımı olmayan düz JS modülleri;
  CDN, dış font, dış görsel yok. CSP: satır içi `<script>` ve `on…=` yasak. Dinamik metin yalnızca `textContent`.
- **Sırlar:** token, anahtar, `#key=` bağlantısı hiçbir dosyaya, commit'e, kayda yazılmaz. `~/.pixel-office`,
  `~/.claude` okunmaz, değiştirilmez.
- **Güvenlik çekirdeğine dokunma:** `pixel_connector/crypto.py`, `session.py`, `web/js/e2e.js`, Claude izin kuralları
  (`engines/claude.py` içindeki `BUILTIN_DENY`, `child_env`). Bunlar Aşama 20 / Paket 1'de sertleştirildi
  (`docs/SECURITY_AUDIT.md` §8). Bir v4 adımı bunlara dokunmayı gerektiriyorsa önce kullanıcıya sor.
- **Testler:** her değişiklikte `python3 -m unittest discover -s tests` hatasız geçer (1 atlanan test normal: `pwsh`).
  Test silinmez, atlanmaz, zayıflatılmaz. Yeni davranış için yeni test yazılır.
- **Sürümler:**
  - Web dosyası değiştiyse `web/sw.js` içindeki `VERSION` ile `web/js/cloud.js` içindeki `APP_VERSION` **ikisi birlikte**
    bir artırılır (`po-v3.0.X` → `po-v3.0.X+1`; v4 işleriyle `po-v4.0.0`'a geçilebilir). Test bunu denetler.
  - `pixel_connector/` ya da `web/hook/pixel_agent.py` değiştiyse bağlayıcı yeniden derlenir:
    `python3 tools/build_connector.py` → `web/connector/SHA256SUMS` ve README'deki SHA satırı güncellenir →
    `python3 tools/build_connector.py --check` geçmeli. Bağlayıcı sürümü (`pixel_connector/__init__.py`) artırılır.
- **Kayıt:** her görev sonunda `PROJECT.md` §6'nın en altına tarihli Türkçe kayıt ve panoya teslim notu.
  **Yalnızca gerçekten yapılanı yaz.** (A5 kaydında teslim edilmeyen portre/filtre/zoom "yapıldı" diye yazılmıştı; tekrarlanmasın.)
- **İndirme:** paket kurulumu (`pip`, `npm`, `apt`) ya da dış servis kullanımı gerekiyorsa önce kullanıcıya sor.

---

## 2. Push nasıl yapılır? (yeni kural, yalnızca C3 ✅ olduktan sonra ya da A7'de)

Claude çalışırken push'u kullanıcı yapıyordu. Bu dosyadaki işlerde **Antigravity kendi işini kendisi push'lar**.
GitHub deposu: `https://github.com/timuryurdakan/Pixel` (dal `main`). Vercel, `main`'e gelen her push'u ~1-2 dakikada
yayına alır (`www.pixeloffice.com.tr`), Cloudflare röle de `relay/` değiştiyse kendini yeniden derler.

Her görev bitince, **kendi klasöründe** şu sırayla:

```bash
cd ~/Desktop/Pixel-antigravity
git status                                  # 1. temiz mi? yarım dosya, debug çıktısı, sır yok mu?
git fetch origin                            # 2. GitHub'daki son hâli al
git merge origin/main                       # 3. başkasının (Claude/kullanıcı) yeni işini dalına al; çakışma varsa çöz
python3 -m unittest discover -s tests       # 4. hepsi geçmeli
python3 tools/build_connector.py --check    # 5. "up to date" demeli
git push origin HEAD:main                   # 6. dalını GitHub'daki main'e gönder
```

- **6. adım reddedilirse** ("rejected … fetch first" / "non-fast-forward"): bu arada başka biri push'lamıştır.
  2'den itibaren tekrarla. **Asla `--force` kullanma**, asla `git reset --hard` yapma.
- **Kullanıcı adı / parola isterse:** dur ve kullanıcıya sor ("GitHub girişi gerekiyor"). Token'ı asla bir dosyaya,
  remote adresine (`https://token@github.com/...`) ya da komut geçmişine yazma. Antigravity'de GitHub hesabıyla oturum
  açılmışsa entegre terminal girişi kendisi halleder.
- **Yayın kontrolü (push'tan 2 dk sonra):**
  ```bash
  curl -s https://www.pixeloffice.com.tr/sw.js | grep VERSION        # yeni sürüm görünmeli
  curl -s https://www.pixeloffice.com.tr/api/health                  # {"ok":true,...}
  ```
  Bağlayıcı değiştiyse: `curl -s https://www.pixeloffice.com.tr/connector/SHA256SUMS` yeni SHA'yı göstermeli.
- **Claude geri dönerse:** `~/Desktop/Pixel` klasöründe `git pull --ff-only` ile senin push'larını alır. Bu yüzden
  `main`'in geçmişini değiştiren hiçbir şey (rebase sonrası force push, amend + push) yapılmaz.
- Push'tan sonra panoya teslim notu: commit kısa SHA'sı, test sayısı, canlıdaki `VERSION`.

---

## 3. Görevler

### A6: v4 mimari belgesini düzelt ve projeye al (şimdi yapılabilir)
Dosya: `docs/GENEL_PROJE_MIMARISI_V4.md` (senin klasöründe duruyor, henüz commit'lenmedi).
Claude'un inceleme notları, düzeltilecekler:
1. Başlıktaki durum: "Onaylanmış Ana Mimari Plan" ve "anayasa" ifadeleri yerine **"Taslak — kullanıcı onayı bekliyor"**.
   Kullanıcı yalnızca bu dosyadaki A8–A10 sırasını onayladı.
2. Yanlış iddialar:
   - v1 `~/.gemini` izlemiyordu (Antigravity izleyicisi Aşama 18'de, 2026-10-21'de eklendi: `web/hook/pixel_antigravity.py`).
   - "%95 test kapsamı" ölçülmüyor; "her değişiklik tam test takımından geçer" yaz.
   - "Push bildirim" yok; Aşama 19'daki onay kartları sayfa açıkken çalışır (Web Push ayrıca planlanabilir).
3. Kullanıcının durumu: **sürekli açık bilgisayar yok.** Gece Nöbetçisi ve GitHub webhook tetikleyicileri bu yüzden
   "ileride" bölümüne alınır; ilk şablon elle başlatılan ve bilgisayar açıkken biten bir iş olmalı.
4. `gh pr create` gibi adımlar GitHub token'ı ister; Paket 1'den beri ajan ortamına token geçmiyor (bilerek).
   Bu ihtiyaç "kullanıcının açık onayıyla, adım bazında verilen kısıtlı yetki" olarak tasarlanmalı (henüz yok).
5. Riskler bölümüne ekle: `edge-tts` resmî olmayan Microsoft servisi; Claude aboneliğiyle otomasyon ve ücretli plan
   satışı Anthropic kullanım şartları açısından incelenmeli (A4 araştırmasındaki notlar).
6. Mermaid bloğu GitHub'da çizilir ama başka yerde düz metin görünür; sorun değil, kalsın.
**Teslim:** `[A6] v4 mimari belgesi (taslak, düzeltilmiş)` commit'i, kendi dalında. Push yok (C3 ✅ değilse).

### A7: Claude'un kalan Aşama 20 paketlerini devral (yalnızca kullanıcı isterse)
`PROJECT.md` "Aşama 20" listesinde işaretlenmemiş paketler ve `v2.md`'de ✅ almamış maddeler. Her paket için `v2.md`'de
dosya:satır ve önerilen düzeltme yazıyor. Sıra: Paket 5 → 6 → 7 → 8. Kurallar §1; push §2. Her maddeyi bitirince
`v2.md`'de başlığın sonuna ` ✅ (Paket N, Antigravity)` ekle. Yarım kalmış, commit'lenmemiş bir Claude işi bulursan
(`~/Desktop/Pixel` içinde `git status` kirli) **dokunma**, kullanıcıya söyle.

### A8: v4-A — Ofis duvarında Görev Tahtası
**Amaç:** Ofisin duvarına piksel bir Kanban tahtası. Sütunlar: **Sırada · Çalışıyor · Onay bekliyor · Bitti**.
- Veri kaynağı:
  - Eşleşmiş cihazda (v3 kumanda açık): bağlayıcının görev listesi (`web/js/control.js` hub'ındaki görev/ajan durumları;
    yeni protokol mesajı gerekmiyorsa ekleme). Aşama 19'un onay istekleri "Onay bekliyor" sütununa düşer.
  - Eşleşmemiş/izleyici tarayıcıda: yalnızca herkese açık ajan durumlarından sayılar (çalışan / bekleyen / uyuyan).
    Görev metni asla gösterilmez (gizlilik: görev metinleri yalnızca E2E kanalda).
- Görünüm: `web/js/office.js` haritasında duvarda 3-4 karelik bir tahta mobilyası (yürünmez), `sprites.js`'e yeni çizim
  (mevcut çizimler değişmez, `validateSprites()` boş dönmeli). Tahtada her sütunda en fazla 3 küçük kart + sayı.
- Tıklayınca: tahtanın büyük hâli bir panelde açılır (DOM, `textContent`), karta tıklayınca ilgili ajan seçilir.
  Mini pencerede (`?view=mini`) yalnızca küçük tahta, panel yok.
- Erişilebilirlik: panel klavyeyle açılıp kapanır (Enter/Escape), `aria-live` ile sayı değişimi duyurulur.
- Dokunulabilecek dosyalar: `web/js/office.js`, `renderer.js`, `sprites.js` (yalnızca ekleme), yeni `web/js/board.js`,
  `ui.js`/`main.js` (yalnızca tahta bağlantısı), `web/index.html`, `web/css/style.css`, `web/sw.js` + `cloud.js`
  `APP_VERSION` (sürüm), testler (`tests/js/board.test.js`, `tests/test_v3_browser.py`'ye bir kontrol).
- Kabul: 360/390/1280 px'te taşma yok, konsol hatası yok; ajanlar tahtanın içinden yürümüyor (yol bulma testi);
  izleyici modunda görev metni yok (test); tüm testler geçer.

### A9: v4-B — Masadan masaya devir (iki adımlı elle başlatılan akış)
**Amaç:** "Ajan A bitirince çıktısını Ajan B'ye verir" akışının ilk, küçük sürümü ve animasyonu.
- Kumanda panelinde yeni bir seçenek: görev verirken "Bitince şu ajana devret: [ajan seç] + ek talimat".
- Bağlayıcı: A'nın görevi başarıyla bitince B için yeni görev oluşturulur; girdi olarak A'nın **son yanıt metni**
  (boyut sınırlı, ör. 16 KB) verilir. Zincir en fazla 1 devir (A→B), döngü yok. A başarısızsa devir yapılmaz.
  Aşama 19 açıksa devirden önce "Devredilsin mi?" onayı istenebilir (ayar).
- Ofiste: A masasından kalkıp B'nin masasına yürür, ikisinin üstünde "evrak" balonu çıkar, A yerine döner, B yazmaya başlar.
- Güvenlik: devir yalnızca aynı bağlayıcıdaki iki ajan arasında; B'nin izin profili ve çalışma klasörü kendi ayarıdır
  (A'nın yetkisi B'ye geçmez); bütçe sınırları her iki görev için ayrı uygulanır; denetim kaydına `task.handoff`.
- Dokunulabilecek dosyalar: `pixel_connector/tasks.py`, `app.py` (yeni mesaj alanı), `docs/V3.md` (protokol eki),
  `web/js/prompt.js`, `control.js`, `agents.js`/`renderer.js` (animasyon), testler. Bağlayıcı yeniden derlenir, sürümü artar.
- Kabul: sahte motorlarla uçtan uca test (A biter → B başlar; A hata → B başlamaz; döngü reddedilir); tüm testler geçer.

### A10: v4-C — İş akışı motoru ve ilk şablon
**Amaç:** `workflow.json` ile tanımlanan çok adımlı akış (A9'un genelleştirilmiş hâli) ve tek bir hazır şablon.
- `pixel_connector/workflows.py`: adımlar, durum makinesi (BEKLİYOR → ÇALIŞIYOR → ONAY BEKLİYOR → BİTTİ / HATA / İPTAL),
  koruma sınırları (toplam maliyet tavanı, toplam tur, süre), insan onay adımı (Aşama 19 mekanizması).
- Şablonlar yalnızca bağlayıcının izinli çalışma klasörlerinde çalışır; şablon dosyaları bilgisayarda durur, buluttan
  kod/şablon indirilmez.
- **İlk şablon (bilgisayar açıkken biter, sürekli açık makine gerektirmez):** "Mimar (Ollama) → Geliştirici (Claude, edit)
  → İnceleme (Claude, readonly) → Onay" ile `~/PixelOfficeIsler` içinde küçük bir statik site taslağı.
- Arayüz: tahtada (A8) akışın adımları; "Şablonlar" listesinden başlatma; her adımda ilerleme.
- Kabul: sahte motorlarla tüm durum geçişleri, maliyet tavanı aşımı, onay reddi, iptal testleri; gerçek bir küçük denemenin
  sonucu teslim notuna yazılır (kullanıcı onayıyla, bütçe sınırı düşük).
- Gece Nöbetçisi, GitHub tetikleyicileri, video şablonu: **bu görevde yok** (sürekli açık bilgisayar ve dış servis gerekir).

### A11: Aşama 20 / Paket 6 — Bulut temizliği (şimdi yapılabilir, C3'ü beklemez)
Claude aynı anda Paket 5 (bağlayıcı, kanca, arayüz), 7 ve 8 üzerinde çalışıyor. Bu görev yalnızca bulut tarafına
dokunduğu için onlarla çakışmaz. **Push yok:** kendi dalında `[A11]` commit'i, Claude birleştirir.
Önce `git merge main` (A6 sonrası ana dal).

Maddeler (`v2.md`'de dosya:satır, kanıt ve önerilen düzeltme yazıyor):
- **F1** Kaldırılmış "sabit ajan" (resident) özelliğini sil: `api/residents.py`, `pixel_cloud/residents.py`, `derive.py` ve
  `app.py` içindeki resident dalları, `tests/test_residents.py` (özellik silindiği için bu test dosyası da silinir; bu
  kuraldaki "test silinmez" yasağının tek istisnası, teslim notunda belirt), `tests/test_release.py`'deki ilgili satırlar,
  `docs/CLOUD.md`/`V3.md`/README'deki anmalar. Redis'te kalmış `_res` alanı varsa okuma yolu onu sessizce yok saysın.
  Bu silme H18 ve H23'ü de kapatır (v2.md'de ikisine de ` ✅ (Paket 6, F1 ile)` yaz).
- **H12** `pixel_office/reported.py:82-106` hayalet ajan çakışması (yerel mod). Çakışmayı yakalayan test yaz.
- **H17** `pixel_cloud/app.py` `room_full` 429'unda gruptaki güncellemelerin yarım yazılması.
- **H19** Sahip adı (`no:`) alanlarının hiç temizlenmemesi.
- **H20** Upstash çağrıları için toplam süre sınırı (Vercel 10 sn sınırının altında kalsın, 503 dönsün).
- **H21** Röle ayarı yarım/geçersizken bir kez uyarı kaydı (değeri yazmadan).
- **H22** İsimlerde görünmez karakter süzme (Unicode `Cf` kategorisi + U+3164, U+115F, U+1160, U+FFA0, U+E0000–E007F).
- **D5 (yalnızca bulut)** `pixel_cloud/__init__.py` sürümü `3.0.0` (veya 3.1.0) yap, `tests/test_cloud_app.py`'deki iki
  beklentiyi bilinçli sürüm değişikliği olarak güncelle; `pixel_office` (yerel sunucu) sürüm etiketi.
  **`web/hook/pixel_hook.py` sürümüne dokunma** (Claude Paket 5'te o dosyada çalışıyor).
- **D9** `app.py:565` `ids` gölgelemesi (F1 ile kod tamamen gidiyorsa ayrıca gerekmez).
- **F3 (yalnızca bulut)** `Storage.count` (kullanılmıyorsa), `reset_storage`, `Settings.extra`, `_update_to_dict` fazla dalları,
  `derive_room_name`, `ids.bearer()` çift başlık, `pixel_cloud/relay.py` ikinci `ROOM_RE`.
- Belge: `docs/CLOUD.md` §12.2'deki HLEN/HKEYS düzeltmesi; `POST /api/residents` anmaları kalkar.

**Dokunulabilecek dosyalar:** `api/`, `pixel_cloud/`, `pixel_office/`, `server.py`, `dev_server.py` (gerekirse),
`tests/test_cloud_*.py`, `tests/test_residents.py`, `tests/test_release.py` (yalnızca resident satırları),
`tests/test_store.py`, `tests/test_local_status.py`, yeni test dosyaları, `docs/CLOUD.md`, `docs/V3.md` (yalnızca resident
anmaları), `README.md` (yalnızca resident anmaları), `v2.md` (yalnızca bu maddelerin ✅ işaretleri), panoya teslim notu,
`PROJECT.md` §6 en alta kayıt (Paket 6 kutusunu Claude işaretler).
**Dokunulmayacaklar:** `pixel_connector/`, `web/` (tamamı), `web/hook/`, `vercel.json`.
**Teslim:** tüm testler geçer; `[A11] Paket 6: bulut temizliği` commit'i; panoya teslim notu (silinen dosyalar, test sayısı).

### A12: Aşama 20 / Paket 8b — Belgeler (şimdi yapılabilir)
Önce `git merge main`. **Yalnızca belge dosyaları:** `README.md`, `PROJECT.md`, `docs/*.md`, `v2.md` (✅ işaretleri).
Kod, test, `web/` dosyalarına dokunma. Push yok: `[A12] Paket 8b: belgeler` commit'i, Claude birleştirir.
Maddeler (`v2.md` §3–§5'te ayrıntı):
- **D1** Tek doğru `.pyz` SHA: yalnızca README ve `web/connector/SHA256SUMS` güncel değeri taşır (şu anki değer
  `web/connector/SHA256SUMS` içinde). PROJECT.md, GOREV_PANOSU, SECURITY_AUDIT'teki eski SHA'ların yanına "(geçmiş)" yaz.
- **D2** README'deki sabit test sayılarını kaldır ("tüm testler geçer" yeterli).
- **D3** PROJECT.md: en üstteki durum satırını güncelle (v3 yayında, alan adı `www.pixeloffice.com.tr`, Aşama 18–19
  bitti, Aşama 20 sürüyor). §6 ilerleme günlüğünü `docs/gunluk/2026-10.md` dosyasına **aynen** taşı, PROJECT.md'de
  günlüğün yerine bu dosyaya bağlantı ve yalnızca son 5 kaydı bırak. README ve PROJECT.md'de tekrar eden "yayına alma
  kontrol listesi"ni tek yerde (`docs/YAYIN.md`) topla, diğerlerinde bağlantı kalsın.
- **D4** GOREV_PANOSU kural 3 ile görev izinleri arasındaki çelişkiyi gider ("görevin dosya listesi kural 3'ün önüne
  geçer" diye tek cümle yeterli).
- **F5** Eski alan adları: `pixel-rouge-phi.vercel.app`, `pixel-ofis.vercel.app` belge anmaları → `www.pixeloffice.com.tr`
  (yalnızca belgeler; `web/rehber.html`'e dokunma, onu Claude yapar).
- **F6** PROJECT.md'deki A5 kaydında teslim edilmeyen portre/filtre/zoom ifadelerini düzelt (üstü çizili ya da
  "teslim edilmedi" notu).
- **E7** README ve `docs/ARCHITECTURE.md`'ye A5 efektleri (ses düğmesi, varsayılan kapalı, kedi, ışık, hareketi azalt),
  "Yalnızca izleme" rozeti, Aşama 19 uzaktan onay ve Antigravity izleyicisi için kısa bölümler (her biri birkaç cümle).
- Röle alan adı taşınıyor (`relay.pixeloffice.com.tr`, internet sağlayıcıları `*.workers.dev`'i engelliyor). Belgelerde
  röle adresini şimdilik değiştirme; README'nin sorun giderme bölümüne bir not ekle: "Röleye bağlanılamıyorsa ve
  `workers.dev` engelliyse kendi alan adınızı Workers'a bağlayın" (kısa). *(2026-10-22: taşıma tamamlandı; belgeleri
  Claude Aşama 20 kapanışında güncelledi: `docs/YAYIN.md`, README sorun giderme, `docs/V3.md` §13.5/§16.1.)*
Kabul: `python3 -m unittest discover -s tests` geçer (belgeler testlerde de denetleniyor, örn. README SHA satırı).

---

## 4. Takılırsan

- Bir kural bu dosya ile çelişirse sıra: kullanıcının açık talimatı → `docs/GOREV_PANOSU.md` → bu dosya → `AGENTS.md`.
- Testler senin değişikliğin dışında bir yerde kırılıyorsa düzeltmeye çalışma; panoya not yaz, kullanıcıya söyle.
- Emin olmadığın güvenlik kararlarında (yeni yetki, yeni ağ çağrısı, yeni dosya yazma yeri) önce kullanıcıya sor.
