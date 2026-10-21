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
| Panoda **C3** satırı ⬜ ya da 🔄 (Claude hâlâ Aşama 20 paketleri üzerinde) | Yalnızca **A6** yapılabilir (kendi belgen, kendi dalında). Diğer görevlere başlanmaz, push yapılmaz. |
| Panoda **C3 ✅** (Claude paketleri bitirdi) | A8 → A9 → A10 sırayla. Her görev bitince §2'deki yolla push. |
| Kullanıcı "Claude'un token'ı bitti, devral" derse | Önce **A7** (Claude'un kalan paketleri), sonra A8 → A10. |

Görevler **sırayla** yapılır: biri bitmeden (testler geçmeden, push yapılmadan) diğerine başlanmaz.

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

---

## 4. Takılırsan

- Bir kural bu dosya ile çelişirse sıra: kullanıcının açık talimatı → `docs/GOREV_PANOSU.md` → bu dosya → `AGENTS.md`.
- Testler senin değişikliğin dışında bir yerde kırılıyorsa düzeltmeye çalışma; panoya not yaz, kullanıcıya söyle.
- Emin olmadığın güvenlik kararlarında (yeni yetki, yeni ağ çağrısı, yeni dosya yazma yeri) önce kullanıcıya sor.
