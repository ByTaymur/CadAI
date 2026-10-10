# CadAI — Fusion 360 adaptörü

Bu adaptör Fusion'ın açık belgesini aynı CadAI MCP araçları ve VS Code görüntüleyicisiyle bağlar. FreeCAD desteği korunur.

**Durum (0.18.0, 10 Ekim 2026):** Kök bileşendeki parçalar için bütün düzenleme araçları gerçek Fusion'da (Windows,
Fusion `736d2b4c…` kurulumu) `fusion/tests/live_smoke.py` ile uçtan uca doğrulandı. Bu doğrulama iki gerçek hatayı
buldu ve düzeltti: `add_box`/`add_cylinder` yardımcı düzlemi gizlerken salt okunur `isVisible` özelliğine yazıyordu,
`add_cylinder` yarıçap ölçüsünün yazısını daire merkezine koyuyordu; ikisi de 0.17.x'te gerçek Fusion'da hiç
çalışmıyordu. Alt bileşenli montajların doğrulaması (aşağıdaki Fusion içi betik) henüz gerçek Fusion'da çalıştırılmadı.

## Kurulum

**0.18.2'den itibaren kurulum kendiliğinden güncellenir:** VS Code'daki CadAI eklentisi her açılışta Fusion eklentisini
kendi sürümüne getirir; daha yeni bir kurulumun üzerine asla eski dosya yazmaz. Fusion'a bağlanırken Fusion eski bir
CadAI çalıştırıyorsa kod yerinde yenilenir (0.18+), yoksa Stop/Run istenir. VS Code eklentisi güncellendiğinde bir kez
**Developer: Reload Window** yapın; aksi hâlde pencere eski sürümü çalıştırmaya devam eder.

VS Code paketinde **CadAI → Fusion 360 eklentisini kur / güncelle** komutunu çalıştırın. Fusion → Scripts and Add-Ins
içinde `CadAI` klasörünü ekleyin ve eklentiyi çalıştırın (eklenti sonraki açılışlarda kendiliğinden başlar). Ardından
VS Code'da **CadAI → Fusion'a bağlan** ya da **CAD oturumunu seç** komutunu kullanın.

Kurulum klasörü Windows'ta `%APPDATA%/Autodesk/Autodesk Fusion/API/AddIns/CadAI`, macOS'ta
`~/Library/Application Support/Autodesk/Autodesk Fusion/API/AddIns/CadAI` olur. Bu konum Autodesk'in güncel
[eklenti oluşturma belgesine](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/WritingDebugging_UM.htm)
uygundur. 0.17.0 farklı `Autodesk Fusion 360` klasörüne kopyalıyordu; Fusion'da önceki CadAI kaydını Stop yapın, yeni
klasörü Add-Ins listesine ekleyip Run yapın.

Kurulu dosyaları güncelledikten sonra Fusion'ı kapatmanız gerekmez: VS Code'da **CadAI → Geliştirici → Eklentiyi
yeniden yükle** (ya da `python fusion/tests/reload_live.py`) adaptör kodunu aynı köprü, oturum ve belge kimlikleriyle
yeniden yükler. `CadAI.py` giriş noktası değiştiyse Fusion'da Stop/Run gerekir.

**Fusion'a bağlan** düğmesi yalnızca Fusion oturumlarını denetler; tek oturum varsa onu seçer. Hata veren oturumlar
listeden gizlenmez. Panelde **Fusion 360 bağlı** yazmalı. Sadece **Parametreler** düğümü varsa ve görünür gövde yoksa
gösterilecek geometri yoktur. Bağlanırken Fusion'ın Scripts and Add-Ins ve diğer açık diyaloglarını kapatın; açık
diyaloglar API olaylarının işlenmesini geciktirebilir.

Eklenti Run sırasında hata verirse Fusion hata metnini gösterir ve `%APPDATA%/CadAI/fusion/startup-error.log`
(macOS: `~/Library/Application Support/CadAI/fusion/startup-error.log`) dosyasına yazar. Oturum bulunamazsa **Fusion'a
bağlan → CadAI çıktısını aç** ile bu kayıt görülebilir.

Bağımsız paket için depo kökünde `python fusion/build_addin.py` çalıştırın (sürüm `CadAI.manifest`'ten okunur).
`fusion/dist/CadAI-fusion-<sürüm>.zip` içindeki `CadAI` klasörünü çıkarıp Scripts and Add-Ins ekranından ekleyin.
Bir kaynak checkout'unda `fusion/CadAI` klasörü de doğrudan eklenebilir; ortak çekirdek `freecad/CadAI/cadai_core`
altındadır. Fusion programı ayrıca kurulmuş olmalıdır.

## Araçlar

- Belge özeti, yerel parametreler, seçim, yüz ve kenar inceleme, ölçüm.
- `set_property`: `object="Parameters"`, `property` yerel parametre adı. Sayısal uzunluk mm, açı derece; ifade
  verilecekse birimi yazın (`"25 mm"`).
- `import_mesh`: STL/OBJ/3MF dosyasını mesh gövdesi olarak ekler (birim varsayılan mm).
- `add_box`, `add_cylinder` (`radius` ya da `diameter`): yerel eskiz ölçüleri, ofset düzlemi ve ekstrüzyon özellikleri.
- `make_hole`: ölçülen düz yüz ve yüz içindeki noktadan, yalnızca belirtilen gövdede yerel delik özelliği. Sonuç
  `diameter_parameter` döndürür; mevcut deliğin çapı bu parametreyle değiştirilir.
- `fillet_edges` (`radius`), `chamfer_edges` (`size`, eşit mesafeli pah): `list_edges` kenar kimlikleri ya da tek
  sözcük `all` / `top` / `bottom` / `vertical` / `horizontal` / `circular` (montaj koordinatında ölçülür).
- `boolean`: `cut` / `fuse` / `common`, yerel Combine özelliği. `tool` gövdesi tüketilir; sonuç `base` gövdesinin
  kimliğini korur ve `volume_change_mm3` döndürür.
- `move_object`: yerel Move özelliği. `position` gövdenin sınır kutusu en küçük köşesinin yeni yeri, `offset`
  kaydırma, `rotation_deg` + `rotation_axis` (varsayılan Z) gövde merkezinden döndürme.
- `export_model`: kök bileşenin tamamı STEP veya F3D. Seçili gövde dışa aktarımı ve STL henüz desteklenmez.
- Arayüz: geri al / yinele (Fusion'ın kendi komutları; yanıt komut gerçekten uygulandıktan sonra döner), sahne
  farkları, seçim, belge içinde saklanan imzalı işaretler, görünürlük.

Kök ve iç içe bileşen gövdeleri montaj konumlarında görüntülenir; tekrarlanan parçalar ayrı seçilir ve ölçülür. Bileşen
hareketi sahne önbelleğini ve eski işaretleri geçersiz kılar. Geometri değişince eski işaretler `stale=true` olur;
yeniden işaretleme gerekir. Bir bileşen gövdesinin görünürlüğü değiştirildiğinde o bileşen örneğinin tamamının ışığı
değişir; aynı parçanın diğer örnekleri korunur.

## Sınırlar

- Geometri düzenleme kök bileşendeki parçalarla sınırlıdır. Alt bileşenli belgeler görüntülenebilir, seçilebilir,
  ölçülebilir, işaretlenebilir ve dışa aktarılabilir; bu belgelerde düzenleme uygulamadan önce reddedilir.
- Tek bir CadAI aracı Fusion'da birden çok geri alma adımı oluşturabilir (ör. `add_box`: düzlem, eskiz, ekstrüzyon,
  ad). Geri al, Fusion'ın son adımını geri alır; aracın tamamını değil. Başarısız bir işlemde atomik geri dönüş yoktur:
  modeli inceleyin, aynı işlemi körlemesine tekrarlamayın.
- Montaj bağlantıları/hareket analizi, FEM, DFM, teknik resim, CAM ve render desteklenmez; bu araçlar manifestte ve
  panelde görünmez.
- Mesh (üçgen ağ, içe aktarılmış STL/OBJ/3MF) gövdeleri 0.18.1'den beri görüntülenir, seçilir ve ölçülür (hacim
  üçgenlerden; yalnızca kapalı ağda geçerli). B-rep yüzü/kenarı olmadığından düzenlenemez; Fusion'da Mesh → Dönüştür
  ile katıya çevirin.
- Hesaplanamayan hacim `null` ve hata metniyle döner; değer uydurulmaz. Eksik yüz uyarısı olan sahne tam geometri
  doğrulaması sayılmaz.
- Yeni belgeyi ilk kez kaydetmek için Fusion'ın kendi proje klasörü seçimini kullanın.

## Oturum ve MCP

Köprü yalnızca `127.0.0.1` üzerinde, bearer token ile çalışır. Her süreç ayrı oturum kimliği yayımlar:

- Windows: `%APPDATA%/CadAI/sessions/<session_id>/bridge.json`
- macOS: `~/Library/Application Support/CadAI/sessions/<session_id>/bridge.json`

Fusion ya da FreeCAD kapanırken köprüyü durduramazsa (çökme, görev yöneticisi) kayıt klasörü kalır. 0.18.0'dan itibaren
yeni bir köprü başlarken süreci olmayan kayıtları siler; MCP keşfi de Windows'ta artık ölü süreçleri sayar ve onları
yok sayar (önceden birden çok eski FreeCAD kaydı `cadai-freecad`'i "Birden çok CAD oturumu var" hatasıyla durduruyordu).

**Otomatik bağlantı (0.19.0):** Ajanlar tek bir `cadai` MCP sunucusu kullanır. Sunucu her istekte çalışan FreeCAD ve
Fusion oturumlarını bulur: yalnızca biri açıksa ona bağlanır; ikisi de açıksa VS Code'da seçili olana
(`sessions/active-session.json`) gider; seçim yoksa tahmin etmez, ajana oturum listesini verir ve ajan kullanıcıya
sorup `cad_select_session` çağırır. Program açılınca, kapanınca ya da yeniden başlayınca araç listesi kendiliğinden
güncellenir (`notifications/tools/list_changed`); Fusion bağlıyken yalnızca Fusion'ın araçları görünür.
`cad_bridge_status` hangi programa ve belgeye bağlı olunduğunu söyler. VS Code görünümü de aynı kuralla FreeCAD'e ya da
Fusion'a kendiliğinden bağlanır ve kapanan programın yerine çalışana geçer.

**Yapay zekâ ajanlarına bağla** artık tek `cadai` kaydı yazar ve eski `cadai-freecad` / oturuma bağlı `cadai-fusion`
kayıtlarını kaldırır. Ortam değişkeni verilmemiş eski `cadai-freecad` kayıtları da varsayılan olarak otomatik modda
çalışır. Tek programa sabitlemek için `CADAI_BACKEND=freecad` ya da `fusion` (+ `CADAI_SESSION_ID`) verilebilir.

Manuel MCP için Python 3.9+ ile `freecad/CadAI/mcp_server/cadai_mcp.py` çalıştırın; ortam değişkeni gerekmez.
Token'ı ayara veya depoya kopyalamayın. Belge/revizyon değişince `cad_refresh_context` ile hedefi açıkça yenileyip
modeli, yüzleri ve işaretleri yeniden inceleyin.

## Doğrulama

- `python -m unittest freecad/CadAI/tests/test_adapters.py`: ortak köprü güvenliği, birim dönüşümleri, imzalar, ana iş
  parçacığına dispatch, kenar seçicileri, geri alma beklemesi, yeniden yükleme ve ölü oturum temizliği. Fusion geometri
  çekirdeğini kullanmaz.
- `python fusion/tests/live_smoke.py`: **çalışan Fusion'da**, köprü üzerinden, Fusion içinde betik çalıştırmadan. Kendi
  kaydedilmemiş belgesini açar; 100 × 50 × 5 mm kutu, Ø6 delik → çap parametresi Ø10, dört dikey kenarda 1 mm pah,
  üst kenarda R1 yuvarlatma, Ø8 pim (taşıma + Boolean kesme), 90° döndürme, geri al/yinele, sahne farkı, imzalı işaret
  ve STEP; her adımda hacim ve boyutu beklenen değerle karşılaştırır. Sonunda belgeyi kaydetmeden kapatır, kullanıcının
  belgelerine dokunmaz. Birden çok Fusion oturumu varsa `CADAI_SESSION_ID` verin.
- `fusion/tests/smoke` (Fusion içi Script): montaj bileşenleri — döndürülmüş, taşınmış, tekrarlanmış ve iç içe
  bileşenlerde ayrı kimlikler, montaj koordinatındaki ağırlık merkezleri ve bileşen hareketindeki sahne farkı. Scripts
  and Add-Ins ekranına **Script** olarak ekleyip çalıştırın; PASS/FAIL sonucunu gösterir. Gerçek Fusion'da henüz
  çalıştırılmadı.

API uygulaması Autodesk'in [iş parçacığı kuralları](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/Threading_UM.htm),
[birimler](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/Units_UM.htm),
[BRep gövde referansları](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_BRepBody.htm) ve
[yerel delik konumlandırma](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/HoleFeatureInput_setPositionByPoint.htm)
belgelerine dayanır. Bileşen görünümü [Occurrence gövde vekilleri](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Occurrence.htm),
[tüm bileşen örnekleri](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Component_allOccurrences.htm) ve
[transform2](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Occurrence_transform2.htm) sözleşmesini
kullanır. Yerel yüz ağı bir kez montaj koordinatına dönüştürülür; vekil geometrisine ikinci kez dönüşüm uygulanmaz.
