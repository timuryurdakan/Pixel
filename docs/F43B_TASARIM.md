# F4-3b — İş akışı şablonları tarayıcıda: Tasarım (taslak)

Durum: **onay bekliyor**. Kaynak: `docs/F43_TASARIM.md` B8 ("şablonlar ayrı iş olarak, Patron'dan sonra").

## 1. Amaç

Bilgisayarına hiçbir şey kurmayan biri, "Tarayıcı ajanı"ndaki kendi anahtarıyla bir iş akışı şablonunu çalıştırır.
Örneğin hazır "Statik site taslağı" ya da Şablon Oluşturucu'da kendi yaptığı bir şablon. Akış şöyle ilerler: adım
adım ajanlar çalışır, araya onay adımları girer, ilerleme Görev Tahtası'nda ve ofiste görünür. Bugün şablonlar
yalnızca bağlayıcıda çalışıyor.

## 2. Ana fikir: F4-3a ile aynı yol

- Tarayıcı cihazı bağlayıcıyla **aynı `workflow.*` mesajlarını** cevaplar: `workflow.list`, `workflow.start`,
  `workflow.answer`, `workflow.cancel`. Olay da aynıdır: `workflow.state`. Cihaz bilgisi `features` listesine
  `workflows` ekler.
- Böylece iş akışı penceresi, çalışma kartı, Görev Tahtası ve ofisteki yürüme animasyonu (`runChanges`) olduğu gibi
  çalışır. Değişen tek şey, aşağıdaki C2'deki model seçimi.
- Yürütücü, yeni `web/js/browser_workflows.js` dosyasında yaşar. Adımlar, Patron'un kullandığı geçici ajan yolundan
  (`_runBossTask`) çalışır: anahtar `wf-<adım>`, ajan kaydedilmez.

## 3. Bağlayıcıyla birebir aynı kalması gerekenler

| Kural | Nasıl aynı tutulur |
|---|---|
| Şablon doğrulama (`validate_template`) | Tarayıcıda zaten var (`template_builder.js` `validateTemplate`). Yeni vektör dosyası `tests/workflow_vectors.json`, hem Python hem JS testinden geçer. Üreticisi `_boss_shared.py`'deki gibi. |
| `workflow.start` alanları (`parse_start`): izin listesi, istek en fazla 4 KB, `steps` yalnız şablondaki adımlar | Aynı vektör dosyası. Tarayıcıya özgü fark: `maxCostUsd` yerine `model` (C2, C4). |
| Adım istemi: talimat, `useBrief` ile "Kullanıcının isteği:" eki, `usePrevious` ile önceki adımın çıktısının rastgele çitle **veri olarak** eklenmesi, boyut sınırı | İstem metinleri ve çit biçimi `boss_rules.js` gibi üretilen `workflow_rules.js`'te. Aynı girdiye aynı istemi üreten parite testi var (nonce sabitlenerek). |
| Durum makinesi (adım ve çalışma geçişleri), `run.wire()` biçimi | Geçiş tabloları ortak dosyada. Pencerenin `validRun` kontrolü iki tarafın çıktısında da test edilir. |

## 4. Tarayıcıya özgü kararlar (onay gereken)

| # | Karar | Önerim | Neden |
|---|---|---|---|
| C1 | Hangi şablonlar? | Hazır şablonlar ve **bu tarayıcıdaki Şablon Oluşturucu kitaplığı**. Tarayıcı cihazı `workflowSave` ilan etmez, "Cihaza kaydet" düğmesi orada çıkmaz: şablon zaten bu tarayıcıda. | Ayrı bir kayıt yeri açmamak için. Bağlayıcıdaki "bilgisayarda onay" adımı burada anlamsız. |
| C2 | Şablonda "Claude/Ollama" yazan adımlar ne olacak? | Tarayıcıda motor seçimi yok. **Tek sağlayıcı ve tek model**, bütün adımlar için ortak (B5'in aynısı). Penceredeki adım başına motor/model kutuları yerine üstte tek bir "Sağlayıcı / Model" seçici çıkar. Şablondaki motor ve model alanları yok sayılır. | En basit hâli. Adım başına model seçimi ileride eklenebilir. |
| C3 | İzin profilleri | `readonly` ve `edit` çalışır. **`dev` ya da `auto` adımı olan şablon listede görünür ama "Bu tarayıcıda çalışamaz: komut çalıştırma gerekiyor" yazısıyla seçilemez.** | B1 ile aynı: tarayıcıda komut yok. Sessizce `edit`'e düşürmek, şablonun güvencesini değiştirirdi. |
| C4 | Tavanlar | Para tavanı yok. Bunun yerine çalışma başına **en fazla 60.000 çıkış jetonu** (Patron'la aynı). Süre: şablonun `timeoutS` değeri, en fazla 30 dakika. Tur: şablonun `maxTurns` değeri. Adım başına D7 tavanları geçerli. Her adım saatlik 30 görev sayacına girer. | B3 ile aynı gerekçe. |
| C5 | Onay adımları | Bu tarayıcıdaki kullanıcı, çalışma kartındaki "Onayla / Reddet" ile karar verir. Süre şablondaki `timeoutS`; süre dolarsa reddedilmiş sayılır. | Bağlayıcıdaki davranışın aynısı. |
| C6 | Patron ile aynı anda | **Hayır.** Tarayıcıda aynı anda tek iş: ya bir Patron işi ya da bir iş akışı. | Bağlayıcıdaki `busy` kuralı. Ücretsiz katman sınırları da bunu gerektiriyor. |
| C7 | Alt klasör | **İlk sürümde yok.** Bütün çalışmalar aynı sanal klasöre yazar. Pencere bunu söyler. | Sanal klasöre alt kök desteği eklemek ayrı bir iş. Her şablonun bir `folder` alanı var, ileride kullanılabilir. |
| C8 | Yarım kalan çalışma | Sayfa kapanırsa çalışma biter. Yeniden açılınca "Sayfa kapandığı için durdu" (`stopped`) olarak görünür, kaldığı yerden devam etmez. | Bağlayıcının kapanınca davranışıyla aynı. Devam ettirmek, yarım kalmış bir ajan turunu tekrar çalıştırmak demek. |

## 5. Güvenlik

- **Model çıktısı güvenilmezdir.** Bir adımın çıktısı, sonraki adıma yalnızca çitli veri olarak girer (bağlayıcıdaki
  `handoff_prompt`). Arayüzde gösterilmez, `workflow.state`'e girmez. Olaylarda yalnızca kimlikler, durumlar ve
  sayılar vardır.
- **Şablon güvenilmezdir.** Kullanıcının kitaplığından gelse bile çalıştırmadan önce doğrulayıcıdan geçer. Bozuk bir
  şablon listede "geçersiz" olarak sayılır, çalışmaz.
- **Araçlar:** E4 sanal klasör araçlarıyla sınırlı (`list_dir`, `read_file`, `write_file`). Komut ve ağ yok.
- **Anahtar:** Yalnızca `keystore.js`'ten alınır. Hiçbir `workflow.*` mesajına, olaya ya da hata metnine girmez.
  Kanarya testleri `workflow.*` mesajlarını da kapsayacak şekilde genişletilir.
- **Oda sınırı (D6):** Çalışma yalnızca bu tarayıcıda görünür, odaya gönderilmez.

## 6. İş bölümü ve büyüklük

| Parça | Dosyalar | Büyüklük | Kim yazar |
|---|---|---|---|
| Ortak kurallar, vektörler, parite testleri | `tests/_workflow_shared.py`, `tests/workflow_vectors.json`, üretilen `web/js/workflow_rules.js` | M | Claude |
| Yürütücü: istem, durum makinesi, onay, tavanlar | yeni `web/js/browser_workflows.js` | M | Claude |
| Cihaza bağlama (`workflow.*` yönlendirme, `features`, C6 kilidi) | `browser_device.js` | S | Claude |
| Pencerede tek sağlayıcı/model seçici, C3 yazısı | `workflows.js` | S | Qwen taslağı, Claude inceler |
| İngilizce metinler, JS test iskeletleri | `i18n_en.js`, `tests/js/` | S | Qwen taslağı, Claude inceler |

Toplam yaklaşık M–L. Güvenlik içeren parçaları (doğrulama, istem çiti, durum makinesi) ben yazarım. Mekanik
parçaların ilk taslağını Qwen yazar, ben okuyup düzeltirim. Antigravity döndüğünde bağımsız inceleme yapar.
