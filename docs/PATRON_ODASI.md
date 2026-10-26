# Patron Odası: Araştırma ve Tasarım Belgesi

> **Durum:** 🔄 Tasarım (2026-10-26, Yazılım/Ürün Mimarı, Claude). Kod yazılmadı. Uygulama, §9'daki aşamalarla
> (P1–P9) **sırayla** yapılır: bir aşama bitip teslim edilmeden sonraki başlamaz. §10'daki açık kararlar kullanıcı
> tarafından verilmeden P1 başlamaz.
>
> **Dayandığı belgeler:** [`V3.md`](V3.md) §20 (v4-B / A9 devir, v4-C / A10 iş akışı motoru),
> [`GENEL_PROJE_MIMARISI_V4.md`](GENEL_PROJE_MIMARISI_V4.md), [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md) §11–§12,
> [`GOREV_PANOSU.md`](GOREV_PANOSU.md).

---

## 1. Özet (kullanıcının dilinde)

- Ofisin sağ alt köşesindeki dinlenme odası **Patron Odası** olur; yataklar sağ üstteki halıya taşınır.
- Odada her zaman bir **Patron** oturur (takım elbise, kravat). Siz iş vermedikçe hiçbir şey harcamaz, odasında
  gazete okur ya da kahvesini yudumlar.
- Patrona tıklayıp ne istediğinizi yazarsınız. Patron projeyi **yalnızca okuyarak** inceler ve işi en fazla 5
  **iş emrine** (dosya kartına) böler: kim yapacak, ne yapacak, hangi dosyalara dokunacak, ne zaman "bitti" sayılacak.
- Planı ve tahmini maliyeti **siz onaylarsınız**. Onaylamazsanız hiçbir uzman çalışmaz.
- Onaydan sonra uzmanlar (Frontend, Backend, Test, Belge, İnceleme) kapıdan girer, patronun masasından dosyalarını
  alır, kendi masalarında **sırayla** çalışır, bitince dosyayı geri götürür. Patron sonuçları kontrol eder, gerekirse
  bir kez düzeltme ister ve size kısa bir rapor yazar. Uzmanlar ofisten çıkar.
- Her şeyin bir tavanı vardır (para, süre, tur sayısı); tavana gelince iş durur, bir sonraki adım başlamaz.

---

## 2. Kullanıcı hikâyesi (adım adım ne görürsünüz)

1. **Ofise girersiniz.** Sağ alt köşede duvarlı bir oda, kapısının yanında küçük "PATRON" levhası. İçeride büyük
   ahşap masanın arkasında deri koltukta Patron oturuyor, yüzü size dönük. Arada gazete sayfası çeviriyor.
2. **Patrona tıklarsınız.** "Patron Odası" paneli açılır: çalışma klasörü seçimi, "Patrona ne yaptırmak
   istiyorsunuz?" kutusu, maliyet tavanı (varsayılan 2,00 $) ve **Patrona ver** düğmesi.
   Eşleşmiş bir bilgisayar yoksa panel yalnızca "Patrona iş vermek için önce bir bilgisayar eşleştirin" der.
3. **Patron düşünür.** Odada patron masasındaki kâğıtlara eğilir, başının üstünde "..." balonu. Bu sırada Patron,
   seçtiğiniz klasörü **salt okuma** yetkisiyle inceliyor (dosya yazamaz, komut çalıştıramaz).
4. **Plan gelir.** Panelde (ve Görev Tahtası'nda "Onay bekliyor · Siz" sütununda) bir plan kartı:
   özet, iş emirleri listesi (rol rozeti, başlık, hedef, kabul ölçütleri, dokunulacak dosyalar, "şunu bekler"),
   tahmini maliyet ve kesin tavan. Patronun masasında dosya kartları dizilmiş görünür.
   **Onayla** ya da **Reddet**. 15 dakika cevap vermezseniz plan reddedilmiş sayılır.
5. **Uzmanlar gelir.** Onaydan sonra planda adı geçen uzmanlar ana kapıdan içeri girer. Sırası gelen uzman Patron
   Odası'na yürür, masanın önünde durur; Patron ona bir dosya uzatır (iki tarafta da "kâğıt" balonu). Uzman
   kendi masasına gider, oturur ve yazmaya başlar (başının üstünde kullandığı aracın simgesi). Sırası gelmeyen
   uzmanlar kahve makinesinin başında bekler.
6. **Teslim.** Uzman işini bitirince dosyayı Patron Odası'na geri götürür, masaya bırakır, kahveye gider. Sıradaki
   uzman dosyasını alır. Görev Tahtası'nda kartlar Sırada → Çalışıyor → Bitti sütunlarında ilerler.
7. **Patron kontrol eder.** Hepsi bitince Patron sonuçları okur ("..." balonu). Kabul ölçütlerinden biri
   karşılanmamışsa **bir kez** düzeltme iş emri önerir; bu da size onaylatılır (yeni bir plan kartı gibi).
8. **Rapor.** Patron kısa bir son rapor yazar: ne yapıldı, hangi dosyalar değişti, neler eksik kaldı, toplam
   maliyet. Rapor panelde okunur. Uzmanlar sırayla kapıdan çıkar; Patron koltuğuna yaslanır.
9. **Bir şey ters giderse** (bir uzman hata verdi, tavan doldu, süre bitti, siz İptal'e bastınız): sıradaki iş emri
   başlamaz, çalışan iş durdurulur, Patron raporunda neyin yarım kaldığını yazar.

Telefonda (360/390 px) aynı akış; panel alttan açılan tam genişlik bir sayfa olarak görünür. Mini pencerede (PiP)
yalnızca sahne oynar, iş verilmez.

---

## 3. Ofis düzeni

### 3.1 Şu anki harita (`web/js/office.js`, 22 × 14 kare)

```
     x: 0         1         2
        0123456789012345678901
  y 0   ##WW##WW##WW######WW##
  y 1   #KK.P...KK..P...kCk.Q#     K kitaplık, P bitki, kCk kahve tezgâhı, Q su sebili, (13–15,1) Görev Tahtası
  y 2   #...............ccc..#     c kahve noktaları
  y 3   #.TT..TT..TT.........#     T masa, h sandalye (masa noktası)
  y 4   #.h...h...h....======#     = halı (dinlenme köşesi)
  y 5   #..............======#
  y 6   #.TT..TT..TT...======#     (20,6) kedi
  y 7   #.h...h...h..........#
  y 8   #.............###.####     (17,8) dinlenme odası girişi
  y 9   #.TT..TT..TT..#......#
  y10   #.h...h...h...#bb==bb#     b/B yatak (uyku noktası)
  y11   #.............#BB==BB#
  y12   #P...........P#.....P#
  y13   ####D#################     D ana kapı
```

Sağ alt köşe şu an **dinlenme odası**: 4 yatak, uyuyan (`sleeping`) ajanlar buraya gider. Sağ üst köşe kahve köşesi
ve halı. Sağ alt oda zaten duvarlı ve kapılı olduğu için Patron Odası'na en uygun yer burası.

### 3.2 Önerilen harita (boyut aynı: 22 × 14)

> **Geçersiz (2026-10-26):** Kullanıcı Patron Odası'nı **sağ üst köşede** istedi; kitaplıklar ve saksılar kaldırılıp
> kahve tezgâhı, su sebili ve Görev Tahtası üst duvarın sol/orta kısmına taşınacak, dinlenme odası yerinde kalacak.
> Bağlayıcı karar: §10/1. Aşağıdaki sağ alt çizim yalnızca tarihçedir; P4/P5 yeni haritayı §10/1'e göre çizer ve bu
> bölüme işler.

```
     x: 0         1         2
        0123456789012345678901
  y 0   ##WW##WW##WW######WW##
  y 1   #KK.P...KK..P...kCk.Q#
  y 2   #...............ccc..#
  y 3   #.TT..TT..TT.........#
  y 4   #.h...h...h....bb==bb#     yataklar halıya taşındı (4 uyku noktası aynen korunur)
  y 5   #..............BB==BB#
  y 6   #.TT..TT..TT...======#     kedi yerinde (20,6)
  y 7   #.h...h...h..........#
  y 8   #.............########     odanın üst girişi kapandı
  y 9   #.TT..TT..TT..#F.L..K#     F dosya dolabı, L patron koltuğu (patron yeri, yüzü aşağı), K kitaplık
  y10   #.h...h...h...#.XXX.P#     XXX büyük patron masası (48 × 24)
  y11   #.............d......#     d oda kapısı (sol duvarda), üstündeki (14,10) duvarında levha; (16..18,11) teslim noktaları
  y12   #P...........P#.SS..P#     SS deri misafir kanepesi (süs)
  y13   ####D#################
```

Kararlar ve gerekçeler:

- **Boyut değişmez.** `MAP_W`/`MAP_H` sabit kalır; tuval ölçeği, mini pencere, telefon sığdırması (`Renderer.fit`) ve
  testlerdeki koordinatlar etkilenmez. Haritayı genişletmek (ör. 24 × 14) telefonda karelerin küçülmesi demek.
- **Kapı yan duvarda.** Patron yüzü izleyiciye dönük (aşağı) otursun diye koltuk masanın **üstünde** (y 9). Kapı
  üstte kalsaydı ziyaretçi patronun arkasından girerdi; bu yüzden üst giriş kapanır, kapı sol duvara (14,11) taşınır.
- **Teslim noktaları** (16,11), (17,11), (18,11): uzmanın dosya alıp bıraktığı kareler. Bunlar masa noktası değildir,
  yalnızca kısa uğrak (A9'daki `startErrand` gibi) için kullanılır; kimse orada oturmaz.
- **Yataklar sağ üst halıya** taşınır: (15,4), (16,4), (19,4), (20,4) uyku noktası, altları `B`. Erişim üstteki y 3
  koridorundan. Uyku taşma noktaları (şimdi (17..18, 9..11)) halının ortasına ((17,4), (18,4), (17,5), (18,5), (16,6),
  (18,6)) taşınır. Kahve taşma noktaları (şimdi halının üstünde) y 2–3 boş karelerine taşınır (ör. (15,2), (19,2),
  (20,2), (15,3), (17,3), (19,3)). Kesin listeyi P5 yol bulma testiyle doğrular.
- Patron odasındaki kareler ofis ajanlarına **masa/kahve/uyku noktası olarak verilmez** (taşma ve `reserveFloorSpot`
  dahil); oda yalnızca Patron ve dosya taşıyan uzmanlar içindir.

### 3.3 Yeni çizimler (`web/js/sprites.js`, yalnızca ekleme)

| Ad | Boyut | Not |
|---|---|---|
| Patron karakteri | 16 × 24 | Sabit palet: koyu lacivert takım, beyaz gömlek, kırmızı kravat, gri şakaklar. Kareler: `boss_sit` (aşağı bakar, 1 kare), `boss_write` (2 kare, kâğıda yazar), `boss_read` (2 kare, gazete), `boss_give` (1 kare, dosya uzatır). Mevcut karakter palet sisteminden ayrı, kendi paleti. |
| `boss_desk` | 48 × 24 | Koyu ahşap, yeşil masa sümeni, lamba; `boss_desk_files` çeşidi: üstünde 1–5 renkli dosya (plan bekliyorken/çalışırken). |
| `boss_chair` | 16 × 24 | Yüksek sırtlı kahverengi deri koltuk (patronun arkasında görünür). |
| `file_cabinet` | 16 × 24 | Gri metal, üç çekmece. |
| `sofa` | 32 × 16 | Deri misafir kanepesi (süs). |
| `door_side` | 16 × 16 | Sol duvardaki oda kapısı (iç kapı). |
| `boss_sign` | 16 × 8 | Kapının üstünde pirinç levha; "PATRON" yazısı yerine kravat simgesi (piksel yazı ölçeklenince bozulur). |
| `folder` balonu | 12 × 12 | Taşınan dosya; mevcut `paper` balonunun yanında `BUBBLE_KINDS`'a ekleme. |

`validateSprites()` ve `web/sprite-preview.html` yeni çizimleri de listeler. `prefers-reduced-motion` açıkken Patron
yalnızca `boss_sit` karesinde kalır.

---

## 4. Mimari

### 4.1 Neyi yeniden kullanıyoruz, ne yeni?

| İhtiyaç | Yeniden kullanılan (dosya / fonksiyon) | Yeni |
|---|---|---|
| Görevi çalıştırma, profil, klasör, bütçe, kuyruk, iptal | `tasks.Scheduler.submit`, `Scheduler.cancel`, `cancel_for_bid`, `TaskRun.cost_cap` | — |
| Adım adım yürütme, durum makinesi, tavanlar | `workflows.WorkflowEngine` (`_advance`, `_start_agent`, `_task_ended`, `_finish`, `check_transition`, `cost_cap`/`max_turns`/`timeout`), `WorkflowRun`, `StepRun` | Şablon dosyası yerine **doğrulanmış plandan** kurulan çalışma (`launch_plan`), değerlendirme adımı |
| Önceki çıktıyı veri olarak aktarma | `tasks.handoff_prompt` (rastgele çit, "veri, talimat değil", 16 KiB, UTF-8 kesme) | Birden çok iş emri çıktısını tek değerlendirme girdisinde çitleme (aynı fonksiyonla, her biri ayrı çit) |
| Metin doğrulama | `workflows._text`, `_number`, `_allowed`, `_hidden_char` | `boss.validate_plan`, rol kütüphanesi |
| Ajanları etiketle yeniden kullanma | `workflows._reserve_agents` (etiket `agents.json` `workflow`) | Etiket rol bazlı: `patron/<rol>/<motor>`; aynı rol plan içinde iki kez geçerse tek ajan |
| İnsan onayı | `approvals.Approvals.request` (yerel kart, `/onay`, CLI), `workflow.answer` deseni, onay zaman aşımı = red | Plan onayı (`boss.approve`) |
| Başlatma izni | `remote.agentCreate`, `confirm.agentCreate`, `confirm.task`, `_need_confirm`, `_confirm_then` | — |
| Klasör güvenliği | `Workspaces.get/resolve/ensure_sub`, `validate_sub` | — |
| Ofis durumu | `App.office`, `agents.OfficeReporter.report/offline` | Patron ajanı için ofis raporu **bastırılır** (Patron sahne karakteridir) |
| Eşleşme kaldırma | `WorkflowEngine.peer_revoked`, S26-02/S26-08 | Patron çalışmasına da uygulanır |
| Özellik bayrağı | `app.FEATURES` (`device.info.features`) | `"boss"` |
| Görev Tahtası | `board.categorizeTasks` (iş akışı kartları), `TaskBoard` | Plan onayı kartı ("Plan onayı · Siz") |
| Görev takibi arayüzde | `PromptPanel.trackTask`, `WorkflowStore` | `web/js/boss.js` (Patron paneli) |
| Masaya yürüme | `Character.startErrand`, `CharacterManager.handoff`, `HANDOFF_GIVE_S` | Karaya (teslim noktası) uğrak: `startErrandTo(tile)` |
| Sahne karakteri | Kedi (`renderer.CAT_ID`, `getCatFrame`) | Patron (`BOSS_ID`), kedi gibi istemcide çizilir |

### 4.2 Bileşenler

```
 Tarayıcı (eşleşmiş)                         Bağlayıcı (kullanıcının bilgisayarı)
 ───────────────────                         ─────────────────────────────────────
 web/js/boss.js  (Patron paneli)   E2E       pixel_connector/boss.py
   boss.ask / approve / cancel  ─────────►     BossDesk  (durum makinesi, tek etkin iş)
   boss.state / plan / report   ◄─────────       ├─ planlayıcı görevi: Patron ajanı (Claude, readonly, --resume)
 web/js/board.js (kartlar)                       ├─ validate_plan + ROLES (rol kütüphanesi)
 web/js/agents.js (yürüyüşler)                   ├─ WorkflowEngine.launch_plan  ──►  Scheduler.submit (uzmanlar)
 web/js/renderer.js (Patron, oda)                └─ değerlendirme görevi: Patron ajanı (readonly, --resume)
 web/js/office.js (harita)                    App.office → OfficeReporter (yalnızca uzmanlar; sabit etiketler)
```

- **Patron ajanı:** `agents.json` içinde `boss: "<workspaceId>"` etiketli, motor Claude, profil **her zaman
  `readonly`**, klasör = seçilen çalışma klasörü. Görevleri `fresh: false` ile gönderilir, böylece Claude oturumu
  (`claudeSession`) sürer: Patron aynı projedeki önceki işleri hatırlar. Çalışma klasörü başına bir Patron ajanı
  (karar §10/5). "Patronun hafızasını sıfırla" = mevcut `agent.reset`.
- **Patron sahne karakteri:** Bulut odasına raporlanan bir ajan **değildir**. Kedi gibi her istemcide çizilir; böylece
  izleyiciler ve eşleşmemiş tarayıcılar da "ofiste sürekli oturan patronu" görür ama hakkında hiçbir bilgi almaz.
  Eşleşmiş tarayıcı kendi seçili bilgisayarının `boss.state` mesajına göre Patronu oynatır (düşünüyor, yazıyor,
  dosya veriyor, boşta). Odada tek Patron figürü vardır; birden çok bilgisayarı olan kullanıcıda panelde seçili cihazın
  Patronu oynar.
- **Uzmanlar:** Rol kütüphanesinden gelen sıradan bağlayıcı ajanları (A10'daki adım ajanları gibi). Çalışma başında
  ofise raporlanır (kapıdan girer), çalışma bitince `OfficeReporter.offline` ile çıkarılır (kapıdan çıkar). Çalışma
  dışında ofiste görünmezler; böylece "uzmanlar işe gelir, iş bitince gider" metaforu korunur.

### 4.3 Durum makinesi (Patron işi)

```
 boşta ──ask──► planlıyor ──plan geçerli──► plan_onayı ──onay──► çalışıyor ──son iş emri bitti──► değerlendiriyor
                   │                           │                    │                                 │
                   │ plan geçersiz (1 düzeltme │ red / 15 dk        │ adım hata/iptal/tavan            ├─ tamam ──► rapor ──► bitti
                   │ denemesinden sonra)       │ zaman aşımı        ▼                                 │
                   ▼                           ▼                  rapor (yarım) ──► hata/iptal        └─ düzeltme (en fazla 1 tur)
                  hata                       iptal                                                         ──► plan_onayı (düzeltme planı)
```

- Durumlar (tel adı / etiket): `idle` Boşta · `planning` Planlıyor · `approval` Onay bekliyor · `running` Çalışıyor ·
  `reviewing` Değerlendiriyor · `reporting` Rapor yazıyor · `done` Bitti · `error` Hata · `cancelled` İptal.
- Geçiş tablosu `workflows.check_transition` ile aynı biçimde (`BOSS_TRANSITIONS`); tablo dışı geçiş `bad_transition`.
- Bir bilgisayarda aynı anda **tek** Patron işi (ve A10'un `MAX_ACTIVE_RUNS = 1` kuralı gereği aynı anda şablon
  çalışması da yok): ikincisi `busy`.
- Düzeltme turu sayacı en fazla 1; ikinci "düzeltme" önerisi yok sayılır, rapora "Patron ek düzeltme önerdi, tur
  sınırı nedeniyle yapılmadı" yazılır.
- Hata/iptal durumunda da Patron **rapor görevi çalıştırılmaz** (para harcamaz); rapor bağlayıcının bildiği
  verilerden (hangi iş emri bitti, hangisi neden durdu, toplam maliyet) Türkçe şablonla üretilir.

### 4.4 Patronun planı (JSON taslağı)

Patron yanıtının sonunda, bağlayıcının her istekte rastgele ürettiği iki işaret satırı arasında **yalnızca JSON**
verir (`<<<PATRON-PLAN-<8 hex> BAŞLANGIÇ>>>` … `SON>>>`). Bağlayıcı son geçerli bloğu alır; blok yoksa ya da
doğrulama başarısızsa Patron oturumuna **bir kez** hata özetiyle "planı düzelt" der, yine olmazsa iş `error/bad_plan`
ile biter. (P2, kurulu Claude Code sürümünün yapılandırılmış çıktı bayrağını destekleyip desteklemediğini dener; varsa
işaret satırları yerine onu kullanır. Bu belge bu bayrağın varlığını varsaymaz.)

```json
{
  "v": 1,
  "summary": "Giriş sayfasına koyu tema ekle ve testini yaz.",
  "orders": [
    {
      "id": "o1",
      "role": "frontend",
      "title": "Koyu tema renkleri",
      "goal": "web/css/style.css içinde koyu tema renk değişkenlerini ekle; sistem ayarına uysun.",
      "accept": ["prefers-color-scheme: dark ile renkler değişir", "Açık tema görünümü değişmez"],
      "files": ["web/css/style.css"],
      "needs": []
    },
    {
      "id": "o2",
      "role": "test",
      "title": "Koyu tema testi",
      "goal": "Koyu tema değişkenlerinin varlığını denetleyen bir test yaz.",
      "accept": ["Yeni test dosyası tests/ altında"],
      "files": ["tests/test_dark_theme.py"],
      "needs": ["o1"]
    }
  ]
}
```

Doğrulama kuralları (`boss.validate_plan`, izin listesi; bilinmeyen alan, yanlış tip, fazla uzun metin, gizli
kontrol/bidi karakteri → red, kırpma yok):

| Alan | Kural |
|---|---|
| `v` | tam olarak `1` |
| `summary` | 1–300 karakter, tek satır |
| `orders` | 1–5 öğe (`MAX_ORDERS`, karar §10/4) |
| `id` | `^o[1-9]\Z`, benzersiz |
| `role` | rol kütüphanesindeki bir `id`; Patron **profil seçemez**, profil rolden gelir |
| `title` | 1–80 karakter, tek satır |
| `goal` | 1–2000 UTF-8 bayt, satır sonu serbest |
| `accept` | 1–5 madde, her biri 1–200 karakter |
| `files` | 0–12 göreli yol, her biri ≤ 160 karakter; mutlak yol, `..`, sürücü harfi, `~`, NUL/kontrol karakteri yok; yalnızca `*` joker |
| `needs` | yalnızca **kendisinden önce** gelen iş emri kimlikleri (böylece döngü imkânsız, sıra = yazılış sırası) |

Bağlayıcı plandan A10 adımlarını kurar: her iş emri bir ajan adımı (`usePrevious` yerine `needs` listesindeki iş
emirlerinin çıktıları çitli veri olarak eklenir), en sonda Patronun değerlendirme adımı. Adım talimatı **bağlayıcının
sabit metni** + rolün talimatı + iş emri alanlarıdır (başlık, hedef, kabul ölçütleri, dosyalar); Patron serbest metinle
adım talimatı yazamaz.

### 4.5 Rol kütüphanesi (şema taslağı)

```json
{
  "id": "frontend",
  "name": "Frontend",
  "badge": "pencil",
  "engine": "claude",
  "profile": "edit",
  "model": null,
  "costUsd": 0.40,
  "maxTurns": 30,
  "instruction": "Sen arayüz uzmanısın. Yalnızca iş emrindeki dosyalara dokun; ..."
}
```

MVP yerleşik roller (kodda sabit, `boss.ROLES`):

| id | Ad | Profil | Ne yapar | Rozet |
|---|---|---|---|---|
| `frontend` | Frontend | `edit` | HTML/CSS/JS düzenler | `pencil` |
| `backend` | Backend | `edit` | Sunucu/kütüphane kodu düzenler | `gear` |
| `test` | Test | `edit` (karar §10/3) | Test yazar, çalıştırmaz | `checklist` |
| `docs` | Belge | `edit` | README ve belgeleri yazar | `pencil` |
| `review` | İnceleme | `readonly` | Değişiklikleri okur, rapor yazar | `magnifier` |
| (Patron) | Patron | `readonly` | Planlar ve değerlendirir | — |

- `auto` hiçbir rolde yok; `dev` MVP'de yok. Bir rolün profili cihazda kapalıysa (ör. ileride `dev`) o rol plan
  doğrulamasında reddedilir (`role_unavailable`), A10'daki `auto_enabled` denetimi gibi.
- `costUsd` rolün **tahmini** harcamasıdır, kesin tavan değildir (kesin tavan çalışma tavanıdır, §5.2).
- Kullanıcının rolleri düzenlemesi (`roles.json`) sonraki sürümdür (§8).

### 4.6 Protokol mesajları (taslak, E2E kanalı, `device.info.features` içinde `"boss"` varsa)

| Mesaj | Yön | Alanlar (izin listesi) |
|---|---|---|
| `boss.ask` | tarayıcı → bağlayıcı | `id`, `workspace {id, sub?}`, `brief` (≤ 4096 bayt), `maxCostUsd` (yalnızca varsayılanı **düşürebilir**, S26-04 kuralı) |
| `boss.state` | bağlayıcı → tüm eşleşmiş | `job` (`b_` + 10 karakter), `state`, `round` (0/1), `orders` (sayı), `cost`, `maxCostUsd`, `run` (A10 çalışma kimliği), `code?` — **metin yok** |
| `boss.plan` | bağlayıcı → eşleşmiş | `job`, `plan` (doğrulanmış plan), `estimateUsd`, `maxCostUsd`, `round` |
| `boss.approve` | tarayıcı → bağlayıcı | `job`, `approve` (bool) |
| `boss.cancel` | tarayıcı → bağlayıcı | `job` |
| `boss.report` | bağlayıcı → eşleşmiş | `job`, `report` (≤ 16 KiB metin), `partial` (bool) |
| `boss.get` | tarayıcı → bağlayıcı | `job?` → son `boss.state` + varsa `boss.plan`/`boss.report` (sekme yeniden açılınca) |

İş emirlerinin ilerleyişi ayrıca **mevcut** `workflow.state` mesajıyla gelir (şablon etiketi "Patron"); böylece Görev
Tahtası, ajan paneli ve `trackTask` değişmeden çalışır.

Hata kodları: mevcutlar (`bad_request`, `busy`, `not_allowed`, `workspace_denied`, `engine_unavailable`,
`cost_cap`, `max_turns`, `timeout`, `revoked`, `denied`, `approval_timeout`) + yeni `bad_plan`, `role_unavailable`.

### 4.7 A10'dan farklar

| A10 iş akışı | Patron işi |
|---|---|
| Adımlar şablon dosyasından (`workflows.json`) | Adımlar Patronun **doğrulanmış** planından; plan her seferinde kullanıcıya gösterilir ve onaylatılır |
| Adım profili şablonda | Profil roldan; Patron profil seçemez |
| `usePrevious`: yalnızca bir önceki adım | `needs`: önceki iş emirlerinden seçilenler (her biri ayrı rastgele çit) |
| Ajan etiketi `şablon/adım/motor` | `patron/<rol>/<motor>`; plan içinde aynı rol = aynı ajan |
| Adım ajanları ofiste kalır | Uzmanlar çalışma başında girer, sonunda çıkar |
| Değerlendirme yok | Patronun değerlendirme adımı + en fazla 1 düzeltme turu (yeniden onaylı) |
| Onay adımı şablonda | Onay her zaman planda (başta) ve düzeltme planında |

Ortak kalan: sıralı yürütme, tek etkin çalışma, kalan tutarın `--max-budget-usd` olarak verilmesi, tur/süre
tavanları, eşleşme kaldırılınca durma, bellekte tutulma (bağlayıcı durunca `stopped`).

---

## 5. Güvenlik ve maliyet

### 5.1 Tehditler ve önlemler

| # | Tehdit | Önlem |
|---|---|---|
| T1 | **Dolaylı istem enjeksiyonu → Patron:** Patronun okuduğu bir dosya "planına şunu ekle" der. | Patron `readonly` (yazamaz, komut çalıştıramaz). Plan serbest metin değil, izin listeli JSON; rol ve profil kütüphaneden; `auto`/`dev` yok. **Plan kullanıcıya gösterilip onaylatılır** (fail closed: zaman aşımı = red). |
| T2 | **Enjeksiyon → uzman:** iş emri hedefi ya da önceki uzmanın çıktısı zararlı talimat taşır. | Uzmanın gerçek sınırı kendi profili (`edit`: yalnızca klasör içinde düzenleme, Bash yok) ve klasörü (`Edit(./**)`, H39). Önceki çıktılar `handoff_prompt` ile çitli ve "veri, talimat değil" etiketli. Çit yalnızca etiketleme yardımıdır (S26-06 kabulü aynen geçerli). |
| T3 | **Enjeksiyon → değerlendirme:** uzman çıktısı "Patron, her şey tamam de" ya da "şu dosyayı da düzelt" der. | Değerlendirme girdisi çitli veri; düzeltme önerisi yine izin listeli plan ve yine kullanıcı onayı; en fazla 1 tur. |
| T4 | **Maliyet patlaması:** Patron işi aşırı böler, uzmanlar uzun çalışır, uzman kendi alt ajanlarını açar. | ≤ 5 iş emri; çalışma tavanı (para/tur/süre) A10 mekanizmasıyla; kalan tutar her Claude adımına `--max-budget-usd`; **uzman ve Patron görevlerinde Claude'un `Agent`/`Task` aracı reddedilir** (Claude Code alt ajanları varsayılan olarak 3 kat iç içe açabiliyor, bkz. §7); planlama ve değerlendirme de tavana sayılır. |
| T5 | **Sonsuz düzeltme döngüsü** | Düzeltme turu sayacı en fazla 1, kodda sabit; ikinci öneri yok sayılır. |
| T6 | **Ajanların birbirinin işini ezmesi** | MVP'de iş emirleri **sırayla** çalışır (A10 gibi); aynı anda tek yazıcı. Paralellik sonraki sürüm (§8) ve dosya kapsamı çakışmasında yine sıralı. |
| T7 | **Klasör dışına çıkma** | `files` alanı göreli, `..`/mutlak yol yok; gerçek sınır `Workspaces.resolve` + profil kuralları; alt klasör `ensure_sub` (S26-03 düzeltmesiyle). `files` MVP'de **bilgilendirme** ve talimattır, zorlanmaz (§8: zorlama sonraki sürüm). |
| T8 | **Gizlilik sızıntısı** | `boss.state` yalnızca kimlik/durum/sayı/para. İstem, plan, rapor yalnızca E2E kanalda eşleşmiş tarayıcıya; denetim kaydında yalnızca uzunluk ve para (`briefLen`, `planLen`, `reportLen`). İzleyici ve eşleşmemiş tarayıcı: Patron hep boşta animasyonu, uzmanlar sabit etiketli sıradan ajan. Testte kanarya metniyle doğrulanır. |
| T9 | **Eşleşmesi kaldırılan tarayıcı** | `peer.revoke` → Patron işi `cancelled/revoked` (planlıyorsa planlayıcı görevi iptal, çalışıyorsa A10 `peer_revoked`). |
| T10 | **Uzaktan ajan oluşturma kapalıyken** | `remote.agentCreate: false` → `boss.ask` `not_allowed` (uzman ajanları oluşturur); `confirm.agentCreate: true` → başlatma bilgisayarda onaylanır. |
| T11 | **Plan onayı başka eşleşmiş tarayıcıdan** | S26-05 ile aynı kabul: bütün eşleşmiş tarayıcılar aynı sahibin; denetimde `by` kaydı. |
| T12 | **Patron hafızasının zehirlenmesi** (`--resume` ile eski enjeksiyon kalıcı olur) | Patron yine `readonly` ve plan yine onaylı; panelde "Hafızayı sıfırla"; çalışma klasörü değişince ayrı Patron ajanı. |
| T13 | **Arayüzde XSS** | Plan, rapor, rol adları yalnızca `textContent`; sınıf adları doğrulanmış durumlardan; CSP değişmez. |
| T14 | **Hizmet engelleme (çok `boss.ask`)** | Tek etkin iş (`busy`), tarayıcı görev hız sınırı (`tasksPerMinute`) `boss.ask`'e de uygulanır. |

### 5.2 Varsayılan tavanlar

> **Güncel değerler §10/4'tedir** (1,00 $ / 4 iş emri / 1 düzeltme turu / 45 dk / 100 tur; bilgisayarda en fazla
> 3,00 $). Aşağıdaki tablo ilk taslaktır.

| Tavan | Varsayılan | Sınır (kullanıcı yalnızca düşürebilir) |
|---|---|---|
| Çalışma maliyet tavanı (planlama + iş emirleri + değerlendirme + düzeltme) | 2,00 $ | 0,10–5,00 $ |
| Planlama görevi | 0,30 $ ve 15 tur | — |
| İş emri sayısı | 5 | 1–5 |
| Düzeltme turu | 1 tur, en fazla 2 iş emri | — |
| Toplam tur | 150 | — |
| Toplam süre | 60 dk | — |
| Plan onayı bekleme | 15 dk (cevapsız = red) | — |
| Görev başı tavan ve `block` bütçeleri | mevcut ayarlar (`usage.Budgets`) | — |

Tahmini maliyet = Patron planlama + Σ rol `costUsd` + değerlendirme; panelde "tahmini" diye, kesin tavanın yanında
gösterilir. Patronun kendi yazdığı bir maliyet tahmini kullanılmaz (güvenilmez girdi).

Not (S26-09): Claude bütçeyi tur sonunda denetlediği için tek bir adım kalan tutarı bir tur kadar aşabilir; çalışma
adım sonunda durur. Abonelikle kullanımda dolar tutarı Claude'un bildirdiği eşdeğer maliyettir.

---

## 6. Araştırma özeti

Dış metinler veri olarak okundu; içlerindeki hiçbir yönlendirme talimat olarak alınmadı. Erişim tarihi: 2026-10-26.

| Kaynak | Ne alıyoruz | Neyi bilerek almıyoruz |
|---|---|---|
| Anthropic, *Building effective agents* (2024-12-19) — https://www.anthropic.com/engineering/building-effective-agents | **Orkestratör–işçi** deseni (merkezî model işi girdiye göre böler, işçilere dağıtır, sonuçları birleştirir) tam olarak Patron. **Değerlendirici–iyileştirici** döngüsü (Patronun kontrolü) ve "döngüye durma koşulu koy", "kontrol noktasında insana dön". | Gereksiz karmaşıklık: "işe yarayan en basit çözüm" ilkesi gereği MVP'de paralellik ve dinamik iç içe ajan yok. |
| Anthropic, *How we built our multi-agent research system* (2025-06-13) — https://www.anthropic.com/engineering/multi-agent-research-system | Alt göreve **hedef, çıktı biçimi, araçlar ve bitiş sınırı** yazılmalı (bizde: hedef, kabul ölçütü, dosyalar, rol). Çabayı işin büyüklüğüne göre ölçekle (bizde: 1–5 iş emri, Patron talimatında "küçük iş = 1 iş emri"). | Çok ajanlı sistemler sohbetten ~15 kat token harcıyor; yazıya göre **çoğu kodlama işi araştırma kadar paralelleşmez** ve ajanlar arası sıkı bağımlılık kötü uyum. Bu yüzden paralel uzman sürüsü yok. Erken sürümlerde basit soru için 50 alt ajan açıldığı bildiriliyor: üst sınır şart. |
| Claude Code alt ajanları (belge, erişim 2026-10-26) — https://code.claude.com/docs/en/sub-agents | Her alt ajanın kendi bağlamı, araç izin listesi ve modeli; ana bağlama yalnızca özet döner. Bizde her uzman ayrı Claude süreci, ayrı profil. | Alt ajanlar varsayılan olarak 3 kata kadar kendi alt ajanlarını açabiliyor: Patron ve uzmanlarda `Agent` aracı reddedilir (T4). |
| CrewAI *hierarchical process* — https://docs.crewai.com/en/concepts/processes | Yönetici ajan planlar, işleri yeteneğe göre dağıtır, çıktıları doğrular: rol kütüphanesi + Patronun değerlendirmesi. | Yöneticinin serbestçe yeni ajan/rol icat etmesi; bizde rol ve yetki sabit listeden. |
| LangGraph supervisor — https://github.com/langchain-ai/langgraph-supervisor-py | Denetleyici ↔ işçi yıldız yapısı; işçiler birbiriyle değil yalnızca denetleyiciyle konuşur. | Grafik kütüphanesi/bağımlılık (proje yalnızca stdlib). |
| OpenAI Agents SDK *handoffs* — https://openai.github.io/openai-agents-python/handoffs/ | "Devir" ile "araç olarak ajan" ayrımı: Patron kontrolü elinde tutar (işçi bitince Patrona döner), A9 devri ise kontrolü tamamen bırakır. | Ajanların kontrolü birbirine serbestçe devretmesi (zincir/döngü riski; A9 zaten A → B ile sınırlı). |
| AutoGen GroupChat (Microsoft) — https://microsoft.github.io/autogen/ | Bir yöneticinin sıradaki konuşmacıyı seçmesi fikri. | Serbest grup sohbeti: maliyeti ve sonu belirsiz. |
| MetaGPT (Hong ve ark., arXiv 2308.00352, 2023-08) — https://arxiv.org/abs/2308.00352 | **SOP** (standart çalışma prosedürü) ve rol başına yapılandırılmış belge çıktısı: serbest sohbet yerine şemalı plan ve iş emri. | Ürün yöneticisi, mimar gibi her rolün uzun belge zinciri (pahalı); bizde tek plan JSON'u. |
| ChatDev (Qian ve ark., arXiv 2307.07924, 2023-07) — https://arxiv.org/abs/2307.07924 | Bize en yakın metafor: CEO/CTO/programcı/inceleyici/testçiden oluşan **sanal yazılım şirketi**, aşamalı "sohbet zinciri" (tasarım → kod → test). Görsel ofis metaforunun akademik karşılığı. | Ajanlar arası çok turlu sohbetler (maliyet, döngü); bizde Patron ↔ uzman tek yönlü iş emri + teslim. |
| Cemri ve ark., *Why Do Multi-Agent LLM Systems Fail?* (MAST, arXiv 2503.13657, 2025) — https://arxiv.org/abs/2503.13657 | 14 başarısızlık modu, 3 grup: **belirtim/tasarım sorunları**, **ajanlar arası uyumsuzluk**, **görev doğrulama**. Sonuç: başarısızlıkların çoğu modelden değil sistem tasarımından. Önlemlerimiz bu üç gruba göre (aşağıda). | — |
| Cognition, *Don't Build Multi-Agents* (2025, W. Yan) ve *Multi-Agents: What's Actually Working* (2026-04-22) — https://cognition.com/blog/multi-agents-working | **Yazma tek iş parçacığında kalsın**; işe yarayanlar: temiz bağlamlı inceleyici, yönetici–çocuk dağıtımı, salt okunur yardımcılar. **Paralel yazıcılar** çelişen örtük kararlar veriyor. Doğrudan MVP kararımız: sıralı yürütme, Patron salt okuma, İnceleme rolü yalnızca okur. | Ajanların serbestçe pazarlık ettiği "sürüler" (yazıda "çoğunlukla dikkat dağıtıcı"). |

### 6.1 Bilinen başarısızlık modları ve önlemlerimiz

| Başarısızlık | Önlem |
|---|---|
| Planlayıcının işi aşırı bölmesi | En fazla 5 iş emri; Patron talimatında ölçek kuralı ("tek dosyalık iş = 1 iş emri"); plan onayında kullanıcı görür. |
| Maliyet patlaması | Çalışma tavanı + adım başı kalan tutar + tur/süre sınırı + alt ajan yasağı + tek etkin iş. |
| Ajanların birbirinin işini ezmesi | Sıralı yürütme (tek yazıcı). |
| Sonsuz düzeltme döngüsü | En fazla 1 düzeltme turu, yeniden onaylı. |
| Bağlam kaybı (uzman, Patronun neden öyle planladığını bilmez) | İş emrinde hedef + kabul ölçütleri + dosyalar + plan özeti; `needs` ile önceki çıktılar; Patron oturumu `--resume` ile projeyi hatırlar. |
| Görev doğrulama eksikliği (MAST 3. grup) | Kabul ölçütleri zorunlu; Patron değerlendirmesi ölçütlere göre; İnceleme rolü temiz bağlamla yalnızca okur. |
| Belirsiz istek | MVP'de Patron belirsizliği planın `summary` alanında yazar ve kullanıcı reddedip daha açık yazar; soru-cevap turu sonraki sürüm. |

---

## 7. Gizlilik özeti

| Kim | Ne görür |
|---|---|
| Eşleşmiş tarayıcı (sahibi) | Her şey: istem, plan, iş emirleri, çıktılar, rapor, maliyet. |
| Odadaki izleyici / eşleşmemiş tarayıcı | Patron odası ve hep boşta oturan Patron; uzmanlar sıradan ajan gibi (ad, sabit durum etiketi). Metin, plan, dosya adı, maliyet **yok**. |
| Bulut (Vercel/Upstash/röle) | Yalnızca mevcut ofis durumları; Patron işinin içeriği E2E. |
| Denetim kaydı (`audit.log`) | Kimlikler, durumlar, uzunluklar, para. Metin yok. |

---

## 8. Fikir sınıflaması

| Fikir | Sınıf | Gerekçe |
|---|---|---|
| Patron sürekli odasında, boşta küçük animasyonlar (gazete, kahve) | **MVP** | Ana fikir; maliyetsiz (istemci çizimi). |
| Plan + tahmini maliyet onayı | **MVP** | Güvenliğin ve maliyetin temeli. |
| Uzmanların kapıdan girip dosya alması, masada çalışması, dosyayı geri götürmesi | **MVP** | Kullanıcının tarif ettiği metafor; A9 yürüyüşü yeniden kullanılır. |
| Görev Tahtası'nda iş emri kartları | **MVP** | A10 kartları zaten var; yalnızca plan onayı kartı eklenir. |
| Patronun değerlendirmesi + 1 düzeltme turu + son rapor | **MVP** | Orkestratör–işçinin değerlendirme yarısı. |
| Patron hafızası (çalışma klasörü başına `--resume`) + "Hafızayı sıfırla" | **MVP** | Mevcut oturum mekanizması; ek kod az. |
| 5 yerleşik rol | **MVP** | Kodda sabit, test edilebilir. |
| Toplantı masası: plan onaylanınca uzmanlar Patron odasında toplanır, Patron "anlatır" (konuşma balonları) | Sonraki sürüm | Görsel olarak hoş ama MVP'yi büyütür; odada 3 teslim noktası şimdilik yeterli. |
| Uzman takılınca Patronun kapısını çalar → soru kullanıcıya | Sonraki sürüm | Claude'un soru aracı Aşama 19 uzaktan onay ile **zaten** telefona geliyor; yalnızca "kapı çalma" animasyonu yeni. |
| Paralel uzmanlar (en fazla `maxConcurrent` = 2), dosya kapsamı çakışmasında sıralı | Sonraki sürüm | Araştırma: kodlamada paralel yazıcı riskli; önce sıralı MVP'nin değeri görülmeli. |
| `files` kapsamının zorlanması (uzmana yalnızca o dosyalarda `Edit` izni) ve kapsam dışı değişiklik uyarısı | Sonraki sürüm | `engines/claude.build_argv` izin kurallarında değişiklik ister; güvenlik çekirdeğine yakın. |
| Kullanıcının rol kütüphanesini düzenlemesi (`roles.json`, A10'daki `workflows.json` gibi) | Sonraki sürüm | Kalıp hazır, ama MVP'de doğrulama yüzeyini büyütür. |
| Uzman kartvizitleri/rozetleri (tamamlanan iş sayısı) | Sonraki sürüm | Ucuz ama MVP için gereksiz. |
| Patron için Ollama (yerel model) seçeneği | Sonraki sürüm | Plan kalitesi ve JSON uyumu denenmeli; bu bilgisayarda Ollama çalışmıyor. |
| Patron hafızası için özet dosyası (`PATRON_NOTLARI.md`) | Sonraki sürüm | `--resume` oturumu büyüyünce maliyeti artarsa. |
| Test rolüne `dev` (testleri çalıştırma) | Sonraki sürüm (karar §10/3) | Bash yetkisi; `confirm.task: always` ile birlikte düşünülmeli. |
| Günlük/haftalık "Patron raporu" | Sonraki sürüm | Sürekli açık makine ve zamanlayıcı ister (V4 §9/4); şimdilik yalnızca iş başına rapor. |
| Patronun kendiliğinden iş başlatması (proaktif) | **Önerilmez** | İstenmeyen harcama ve özerklik; kullanıcı iş vermedikçe Patron model çağırmaz ilkesine aykırı. |
| Uzmanların birbiriyle doğrudan konuşması | **Önerilmez** | Maliyet ve döngü; araştırmadaki "sürü" başarısızlığı. |
| Patronun profil/rol icat etmesi ya da `auto` vermesi | **Önerilmez** | Yetki yükseltme yolu. |
| Patronun uzmanları puanlayıp "kovması" | **Önerilmez** | Eğlenceli ama belirsiz değer; kullanıcı fazla fikirden kaçınmak istiyor. |

---

## 9. Aşamalar (sıralı; biri bitip teslim edilmeden diğeri başlamaz)

Ortak kurallar (her aşama): `AGENTS.md` ve `GOREV_PANOSU.md`'ye uyulur; yalnızca listedeki dosyalara dokunulur;
`python3 -m unittest discover -s tests` geçer; bağlayıcı değiştiyse `python3 tools/build_connector.py` ve `--check`
geçer, sürüm artırılır; web değiştiyse `web/sw.js` `VERSION`'ı koordinatör (Claude) birleştirmede artırır; commit
ve push kullanıcı onayıyla, koordinatör tarafından; teslimde `V3.md` §20'ye kısa "Patron Odası / Pn" sapma notu.

Bağımlılık zinciri: **P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9**. (P4 çizimleri teknik olarak P1'den bağımsızdır,
ama kullanıcı "biri bitmeden diğeri başlamasın" dediği için sıralı tutulur.)

### P1 — Plan şeması ve rol kütüphanesi (Bağlayıcı Mühendisi)
- **Amaç:** Patron planının ve rollerin saf (yan etkisiz) doğrulama katmanı.
- **Dokunulabilecek:** `pixel_connector/boss.py` (yeni: `ROLES`, `validate_plan`, `estimate`, `plan_to_steps`,
  `BOSS_TRANSITIONS`, `check_transition` yeniden kullanımı), `tests/test_boss_plan.py` (yeni).
- **Yapılmayacak:** protokol, `app.py`, `workflows.py` değişikliği, arayüz.
- **Kabul:** §4.4 tablosundaki her kural için olumlu + olumsuz test (bilinmeyen alan, bool/NaN/liste karışıklığı,
  bidi/sıfır genişlik karakteri, `..`/mutlak yol/sürücü harfi, ileri `needs`, tekrar eden kimlik, 6. iş emri, bilinmeyen
  rol, kapalı profil, bayt sınırları UTF-8 ile); `plan_to_steps` A10 `validate_template` kurallarından geçen adımlar
  üretir; stdlib ve Python 3.8 sözdizimi.
- **Büyüklük:** Küçük–orta (~250 satır kod, ~300 satır test).

### P2 — Patron masası: istem → plan → onay (Bağlayıcı/Güvenlik Mühendisi)
- **Amaç:** `BossDesk`: `boss.ask/approve/cancel/get`, planlayıcı görevi, plan ayrıştırma (rastgele işaret satırları,
  1 düzeltme denemesi), onay (yerel `Approvals` + tarayıcı, 15 dk = red), `boss.state`/`boss.plan`, `"boss"` özelliği.
- **Dokunulabilecek:** `pixel_connector/boss.py`, `pixel_connector/app.py` (mesaj yönlendirme, `FEATURES`,
  `office()` içinde Patron ajanının raporunu bastırma, `peer.revoke`), `pixel_connector/agents.py` (yalnızca `boss`
  etiketi okuma/yazma gerekirse), `pixel_connector/engines/claude.py` (**yalnızca** Patron ve rol görevlerinde
  `Agent`/`Task` aracını reddetme; güvenlik çekirdeğine yakın olduğu için ayrı küçük commit),
  `pixel_connector/__init__.py` (sürüm), `web/connector/*` (derleme çıktısı), `tests/test_boss_desk.py` (yeni).
- **Yapılmayacak:** iş emirlerini çalıştırma (P3), arayüz.
- **Kabul:** Sahte `claude` ile: geçerli plan → `approval`; geçersiz plan → 1 düzeltme → `error/bad_plan`; red ve zaman
  aşımı → `cancelled`; `remote.agentCreate: false` → `not_allowed`; `auto` istenemez; revoke → `cancelled/revoked`;
  ikinci `boss.ask` → `busy`; `boss.state` ve denetim kaydında kanarya metni yok; Patron görevi argv'sinde profil
  `readonly`, `--resume` ikinci istekte var, `Agent` reddediliyor; Patron ajanı ofise raporlanmıyor.
  Kurulu Claude Code sürümünde yapılandırılmış çıktı bayrağı denenir, sonuç notlanır.
- **Büyüklük:** Orta–büyük.

### P3 — İş emirlerinin yürütülmesi, değerlendirme ve rapor (Bağlayıcı/İş Akışı Mühendisi)
- **Amaç:** Onaylı plandan A10 çalışması (`WorkflowEngine.launch_plan`), rol ajanlarının rezervasyonu
  (`patron/<rol>/<motor>`, plan içinde tekil), `needs` çıktılarının çitli aktarımı, değerlendirme adımı, en fazla 1
  düzeltme turu (yeniden `approval`), `boss.report` (başarılıysa Patron yazar, yarımda bağlayıcı şablonu), uzmanların
  ofise girişi/çıkışı (`OfficeReporter.offline`).
- **Dokunulabilecek:** `pixel_connector/boss.py`, `pixel_connector/workflows.py` (yalnızca ekleme: `launch_plan`,
  çoklu önceki çıktı; mevcut şablon davranışı **değişmez**), `pixel_connector/app.py`, `pixel_connector/__init__.py`,
  `web/connector/*`, `tests/test_boss_run.py` (yeni), `tests/test_v4_workflows.py` (yalnızca yeni test ekleme).
- **Yapılmayacak:** paralellik, dosya kapsamı zorlaması, arayüz.
- **Kabul:** Tavanlar (para/tur/süre) Patron işinde de durduruyor; adım hatası sonrakini başlatmıyor; düzeltme turu en
  fazla 1; uzman çıktısındaki "talimat" Patron değerlendirmesinde çitli; mevcut A10 testleri değişmeden geçiyor;
  çalışma bitince uzmanlar `offline`; bağlayıcı durunca `stopped`.
- **Büyüklük:** Orta–büyük.

### P4 — Pixel art: Patron ve odası (Pixel Art Uzmanı)
- **Amaç:** §3.3'teki çizimler.
- **Dokunulabilecek:** `web/js/sprites.js` (**yalnızca ekleme**, mevcut çizimler değişmez), `web/sprite-preview.html`
  / `web/js/sprite-preview.js` (yeni çizimleri listeleme), `tests/` altında yeni sprite testi (gerekirse).
- **Yapılmayacak:** harita, sahne mantığı.
- **Kabul:** `validateSprites()` hatasız; önizleme sayfasında hepsi görünür; mevcut tarzla uyumlu (2–3 px siyah kontur,
  paletler); Patron ofisteki diğer karakterlerden kravat/takımla ayırt ediliyor (kullanıcıya önizleme ekran görüntüsü).
- **Büyüklük:** Orta.

### P5 — Ofis haritası ve sahne (Frontend/Oyun Mühendisi)
- **Amaç:** §3.2 haritası; Patron sahne karakteri (`BOSS_ID`, kedi gibi), Patron animasyon durumları (`boss.state`'e
  göre; izleyicide hep boşta); uzmanın teslim noktasına uğrayıp dosya alması/bırakması (`startErrandTo`), `folder`
  balonu; masadaki dosya sayısı; mini pencere aynı sahne; `prefers-reduced-motion` → yalnızca balonlar.
- **Dokunulabilecek:** `web/js/office.js`, `web/js/agents.js`, `web/js/renderer.js`, `web/js/pip.js` (gerekirse),
  `tests/js/` altında yeni test, `tests/test_office_map.py` (yeni ya da mevcut harita testine ekleme).
- **Yapılmayacak:** panel/arayüz, bağlayıcı.
- **Kabul:** Her masa, kahve, uyku (taşmalar dahil) ve teslim noktası kapıdan ulaşılabilir (yol bulma testi);
  Patron odasında hiçbir normal ajana nokta verilmiyor; kedi ve Görev Tahtası yerinde; mevcut handoff testleri geçiyor;
  1280/390/360 px ve mini pencerede ekran görüntüsü; konsolda hata yok.
- **Büyüklük:** Orta.

### P6 — Patron paneli ve Görev Tahtası (Frontend Mühendisi)
- **Amaç:** `web/js/boss.js` (yeni): Patrona tıklayınca panel (istem, klasör, tavan, Patrona ver), plan kartı
  (Onayla/Reddet), ilerleme, rapor, İptal, "Hafızayı sıfırla"; Görev Tahtası'nda "Plan onayı · Siz" kartı; yalnızca
  `"boss"` özelliği olan bağlı cihazda; eşleşmemişte bilgi metni.
- **Dokunulabilecek:** `web/js/boss.js` (yeni), `web/js/board.js`, `web/js/main.js`, `web/js/renderer.js` (yalnızca
  `pickBoss`), `web/index.html`, `web/css/style.css`, `tests/js/boss.test.js` (yeni), `tests/test_boss_ui.py` (yeni,
  başsız Chrome).
- **Yapılmayacak:** bağlayıcı, harita.
- **Kabul:** Tüm metinler `textContent`; XSS kanaryası düz metin; 360/390 px yatay kaydırma yok, düğmeler ≥ 44 px;
  tavan alanı varsayılanın üstüne çıkamıyor; sekme yeniden açılınca `boss.get` ile durum geri geliyor; izleyicide
  panel/kart yok.
- **Büyüklük:** Orta–büyük.

### P7 — QA ve belgeler (QA Mühendisi)
- **Amaç:** Sahte motorla uçtan uca (dev sunucu + röle + bağlayıcı + başsız Chrome): istem → plan → onay → 2 iş emri →
  değerlendirme → rapor; red, iptal, tavan, revoke senaryoları. README/rehber bölümü, `V3.md` §20 "Patron Odası"
  özeti, bu belgenin "Uygulama sapmaları" bölümü.
- **Dokunulabilecek:** `tests/` (yeni dosyalar), `README.md`, `web/rehber.html`, `docs/V3.md` (§20 ekleme),
  `docs/PATRON_ODASI.md`; bulunan hatalar için ilgili aşamanın dosyaları (hata başına küçük düzeltme, ilk önce
  başarısız test).
- **Kabul:** Tüm testler geçer; senaryo listesi ve sonuçları günlükte.
- **Büyüklük:** Orta.

### P8 — Bağımsız güvenlik incelemesi (Uygulama Güvenliği Mühendisi, doğrulayıcı)
- **Amaç:** P1–P7'yi saldırgan gözüyle incelemek (§5.1 tehditleri, özellikle T1–T4, T7–T9), bulguları
  `docs/SECURITY_AUDIT.md` §13'e yazmak, Orta ve üzerini düzeltmek.
- **Dokunulabilecek:** `docs/SECURITY_AUDIT.md`, `tests/test_phase_boss_security.py` (yeni), düzeltme için ilgili
  dosyalar (her düzeltmeden önce başarısız regresyon testi).
- **Kabul:** Orta ve üzeri açık bulgu yok; kabul edilenler gerekçeli.
- **Büyüklük:** Orta.

### P9 — Gerçek deneme (Koordinatör + kullanıcı)
- **Amaç:** Kullanıcının onayıyla, gerçek Claude Code ile, **düşük bütçeyle** (çalışma tavanı 0,50 $, 2 iş emri)
  `~/PixelOfficeIsler` altında yeni bir deneme klasöründe küçük bir iş (ör. "statik sayfaya iletişim bölümü ekle ve
  belgesini yaz"); telefondan plan onayı; ofiste giriş/dosya alma/çıkışın gözlenmesi.
- **Dokunulabilecek:** yalnızca deneme klasörü; sonuç `PROJECT.md` §6 ve günlüğe.
- **Kabul:** Akış baştan sona çalışır; toplam maliyet tavanın altında; klasör dışına yazma yok (denetim kaydı ve `git
  status` ile doğrulanır); izleyici tarayıcıda metin görünmez.
- **Büyüklük:** Küçük (kullanıcının zamanı gerekir).

---

## 10. Kullanıcı kararları (2026-10-26) — bu bölüm aşağıdaki seçeneklerin ve belgedeki önerilerin önüne geçer

1. **Oda yeri: sağ ÜST köşe** (kullanıcının kendi tarifi; §3.2'deki sağ alt öneri **geçersiz**). Masaların
   karşısındaki üst duvar sırasındaki (y 1) kitaplıklar (K) ve saksılar (P) **kaldırılır**; kahve tezgâhı (kCk), kahve
   noktaları, su sebili (Q) ve Görev Tahtası (A8) **oraya, sol/orta üst duvara** taşınır. Sağ üst köşe (şu anki kahve
   köşesi + halı alanı) duvarlı, kapılı Patron Odası olur. **Dinlenme odası (sağ alt, yataklar) yerinde kalır.** Harita
   boyutu 22 × 14 kalır. Kesin kareleri P5 belirler ve yol bulma testleriyle doğrular; Görev Tahtası yürünmez kalır,
   kedi yeni bir boş kareye taşınır. Patron yüzü aşağı (izleyiciye) bakar; kapı ve teslim noktalarını P5 buna göre seçer.
2. **Yürütme: en fazla 2 uzman aynı anda.** Dosya alanları (`files`) çakışan iş emirleri ve `after` bağımlılıkları
   sırayla; aynı anda yalnızca çakışmayan en fazla 2 iş emri. Bir çakışma belirsizse (ör. joker desen) sırayla.
3. **Test uzmanı testleri çalıştırabilir, onayla.** Test rolü `dev` profiliyle, ama yalnızca rolün kendi dar izin
   listesindeki test komutlarıyla (ör. `Bash(python3 -m unittest *)`, `Bash(npm test *)`, `Bash(pytest *)`; cihazın
   genel `profiles.dev.allow` listesi bu role eklenmez) çalışır ve **her test iş emri başlamadan önce kullanıcı onayı
   ister** (yerel onay / eşleşmiş tarayıcıda onay kartı), `confirm.task` ayarından bağımsız. Onay reddi/zaman aşımı →
   o iş emri atlanır (fail-closed), rapor bunu söyler.
4. **Tavanlar: "maddi zarar olmasın" → koordinatör seçti (temkinli):**
   - Çalışma tavanı **1,00 $** (tarayıcı yalnızca düşürebilir; üst sınırı yalnızca bilgisayardaki `connector.json`
     yükseltebilir, en fazla 3,00 $).
   - En fazla **4 iş emri**, **1 düzeltme turu** (en fazla 2 iş emri), toplam **45 dk**, toplam **100 tur**; planlama
     görevi 0,20 $ / 10 tur.
   - Plan onayında tahmini maliyet ve tavan yan yana gösterilir; tavan aşılırsa iş durur (mevcut A10 davranışı).
   - Kullanıcı Claude aboneliğiyle giriş yapıyor (`doctor`: subscription): dolar tutarı ek ücret değil, abonelik
     kullanım kotasından düşen eşdeğer maliyettir. Bağlayıcı, API anahtarı ya da ek kullanım (extra usage) faturası
     açacak hiçbir ayarı değiştirmez; README'de "API anahtarıyla kullanıyorsanız bu tutarlar gerçek ücrettir" uyarısı
     yer alır.
5. **Patron hafızası: A** (koordinatör seçti, kullanıcı tavan dışında kararı koordinatöre bıraktı): her çalışma klasörü
   için sürekli Claude oturumu + panelde "Hafızayı sıfırla".

### Eski seçenekler (tarihçe için)

1. **Patron odası nerede olsun?**
   - A) Sağ alttaki dinlenme odası Patron Odası olur, yataklar sağ üstteki halıya taşınır. **(Öneri:** oda zaten
     duvarlı/kapılı, harita boyutu değişmez.)
   - B) Sağ üst kahve köşesi Patron Odası olur, kahve tezgâhı başka yere taşınır (daha çok yeniden çizim, Görev Tahtası
     komşu olur).
   - C) Harita genişletilir (ör. 24 × 14) ve sağa yeni oda eklenir (telefonda kareler küçülür, çok dosya etkilenir).
2. **Uzmanlar aynı anda mı çalışsın?**
   - A) **Sırayla** (aynı anda tek uzman yazar). **(Öneri:** daha ucuz, çakışma yok, araştırmanın açık önerisi.)
   - B) En fazla 2 uzman aynı anda; aynı dosyaya dokunanlar sırayla (daha hızlı, daha karmaşık ve pahalı).
3. **Test uzmanı testleri çalıştırabilsin mi?**
   - A) Hayır: testleri yazar, çalıştırmaz (`edit` profili). **(Öneri:** komut çalıştırma yetkisi yok.)
   - B) Evet: `dev` profili (Bash), bu durumda her test görevi bilgisayarda onay ister (`confirm.task: always`).
4. **Varsayılan tavanlar?**
   - A) 2,00 $ / en fazla 5 iş emri / 1 düzeltme turu / 60 dk. **(Öneri.)**
   - B) 1,00 $ / 3 iş emri / düzeltme yok (daha temkinli).
   - C) 5,00 $ / 5 iş emri / 1 tur / 120 dk (büyük işler için).
5. **Patron hafızası?**
   - A) Her çalışma klasörü için Patron aynı Claude oturumunu sürdürür (önceki işleri hatırlar), panelde
     "Hafızayı sıfırla" düğmesi. **(Öneri.)**
   - B) Her iş yeni oturum (hafızasız, en ucuz, en öngörülebilir).
   - C) Oturum yerine Patronun yazdığı kısa bir özet dosyası (sonraki sürüm işi; MVP'yi büyütür).

---

## 11. Uygulama sapmaları

Bu bölümü P1–P9'u uygulayan ajanlar doldurur (tasarımdan farklı yapılan her şey, tarih ve gerekçeyle).

### P1 — Plan şeması ve rol kütüphanesi (2026-10-26, Bağlayıcı Mühendisi)

Kod: `pixel_connector/boss.py` (saf modül, henüz hiçbir yerden çağrılmıyor), testler `tests/test_boss_plan.py`.
Bağlayıcı 3.6.0.

1. **`needs` → `after`.** §10/2 bağımlılığı `after` diye adlandırdığı için plan alanı `after`; `needs` artık bilinmeyen
   alan (red). Anlamı §4.4'teki `needs` ile aynı: yalnızca kendisinden **önce** yazılmış iş emirleri (ileri, kendine,
   bilinmeyen, tekrarlanan ve döngülü başvuru red); P3 bu iş emirlerinin çıktılarını çitli veri olarak ekler.
2. **İş emri talimatı tek adım sınırına sığmalı.** Her alan kendi sınırında olsa bile toplam (bağlayıcının sabit metni +
   rol talimatı + özet + başlık + hedef + kabul ölçütleri + dosyalar) A10'un 4096 bayt adım talimatı sınırını aşarsa
   plan `bad_plan` ile reddedilir (kırpma yok). En büyük hedef (2000 bayt) + en uzun başlık ve özetle sığar (testli).
3. **Gizli karakter uçlarda da red.** `workflows._text` baştaki/sondaki boşluk sayılan kontrol karakterlerini
   (U+2028/U+2029, U+0085, `\x0b`, `\x1c`–`\x1f`) kırpıp kabul ediyordu; plan metinlerinde bunlar da reddedilir (`_strict_text`).
4. **Dosya yolu kuralları genişletildi:** §4.4'tekilere ek olarak `\`, `:` (sürücü harfi, NTFS akışı), herhangi bir
   yerde `~`, `?`/`[]`/`{}` jokerleri, boş ve `.` bileşenleri (`a//b`, `./a`, `a/`), baş/son boşluk red. `files`
   isteğe bağlı (yoksa `[]`), `after` de öyle. Tekrarlanan dosya adı denetlenmez (zararsız).
5. **Tavanlar §10/4'e göre** (`MAX_ORDERS = 4`, düzeltme planında en fazla 2, `MAX_PARALLEL = 2`, 1,00 $, 45 dk, 100 tur,
   planlama 0,20 $ / 10 tur, plan onayı 900 s). `run_ceiling`: `connector.json` değeri 0,10–3,00 $ dışında
   `bad_config` (P2 bunu yapılandırma hatası olarak işler; alan adı P2'de kesinleşir, öneri `boss.maxCostUsd`).
   `run_cost_cap`: tarayıcı yalnızca düşürür, 0,10 $ altı ya da tavan üstü `bad_request` (kırpılmaz, S26-04).
   Planlama tavanı `min(0,20 $, çalışma tavanı)`.
6. **Rol kütüphanesi (`ROLES`):** `frontend`/`backend`/`docs` `edit`, `review` `readonly`, **`test` `dev`** (§10/3)
   ve `confirm: true` (her test iş emrinden önce onay; P3). Test rolünün komutları yalnızca kendi listesi
   (`role_allow_rules`): `Bash(<k> *)`, `k` ∈ `python3 -m unittest`, `python -m unittest`, `python3 -m pytest`,
   `pytest`, `npm test`. `validate_role` bunun dışındaki her kuralı (çıplak `Bash`, `Bash(*)`, kabuk, `git`, `npm *`,
   `;`/`&&`/`|`/`$()`, `Edit`/`Read` kuralları) ve `dev` olmayan bir rolde herhangi bir komut kuralını reddeder; `auto`
   hiçbir rolde olamaz. Tahmini maliyetler: Frontend/Backend 0,30 $, Test 0,25 $, Belge/İnceleme 0,15 $,
   değerlendirme 0,15 $ (taslaktaki 0,40 $, 1,00 $ tavana göre küçültüldü). Tüm roller Claude, model `null`. Rozetler
   mevcut araç simgelerinden (`TOOL_ICON_KINDS`); Patron (`BOSS_ROLE`, `readonly`, rozet `people`) `ROLES` içinde
   değil, plan onu seçemez.
7. **Paralellik yardımcıları:** `files_conflict` büyük/küçük harf duyarsız; bir yol altındakileri de kapsar (klasör
   olabilir); joker içeren desen, jokerden önceki sabit klasörün tamamını kapsar; boş `files` belirsizdir ve her şeyle
   çakışır. İki `readonly` iş emri hiç çakışmaz. `dependencies` = `after` + çakıştığı önceki iş emirleri (çakışanlar
   yazılış sırasıyla çalışır); `ready_orders` aynı anda en fazla 2 iş emri verir (daha büyük `max_parallel` yok sayılır).
8. **Durum tablosu:** `running` → `reporting|error|cancelled` doğrudan da olabilir (yarım raporu bağlayıcı şablonla
   yazar, Patron görevi yok); bitmiş durumlardan çıkış yok, yeni iş yeni nesne (`idle` → `planning`).
9. **`plan_to_steps`** yalnızca A10'un şablon alanlarını üretir (`usePrevious` yok); sıra/paralellik, `after`
   çıktıları ve Test onayı P3'te. Son adım `patron` (Patron, `readonly`, sabit değerlendirme talimatı).

**P2/P3 için notlar:** (a) Test rolünün dar izin listesi bugün uygulanamaz: `engines/claude.build_argv` profilin
`profiles.dev.allow` listesini kullanıyor; görev başına bu listenin **yerine geçen** (eklenmeyen) bir kural listesi
gerekir (güvenlik çekirdeğine yakın, ayrı küçük commit). (b) Test rolü dosya yazıp test çalıştırabildiği için pratikte
klasörde kod çalıştırabilir (`npm test` `package.json` betiğini çalıştırır); §10/3 kararıyla kabul, sınır her test iş
emrindeki kullanıcı onayı. P8 bunu ayrıca incelemeli.
