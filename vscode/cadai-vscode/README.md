# CadAI — CAD + Yapay Zekâ (VS Code eklentisi)

**v0.17.0:** FreeCAD doğrulanmış desteği korur; Fusion 360 adaptörü deneysel olarak eklenmiştir. **CadAI → Fusion 360 eklentisini kur / güncelle (deneysel)** komutuyla eklentiyi hazırlayın, Fusion'ın Scripts and Add-Ins ekranında çalıştırın ve **CAD oturumunu seç** komutundan Fusion'ı seçin. İlk kapsam inceleme, parametre düzenleme, kutu/silindir, delik, yuvarlatma, 3B görünüm/işaretler ve STEP/F3D'dir. Montajlar, FEM, teknik resim ve diğer gelişmiş FreeCAD özellikleri henüz Fusion'da desteklenmez. Gerçek Fusion geometri doğrulaması bekleniyor; paket içindeki `fusion-addon/CadAI/README.md` kapsamı ve doğrulama betiğini açıklar.

**v0.17.2:** Fusion'da alt bileşenlerdeki B-rep gövdeleri de 3B görünümde açılır. Montaj örnekleri konumlarıyla ayrı seçilir ve ölçülür; bileşen hareketleri sahneye yansır. Alt bileşenli belgelerde geometri düzenleme henüz desteklenmez; Fusion desteği gerçek Fusion doğrulaması bekleyen deneysel durumdadır.

Var olan bir parçayı VS Code içinde açar ve üzerinde **göstererek tarif etmenizi** sağlar. Değişiklikleri yapay zekâ yapar, FreeCAD arka planda motor olarak çalışır.

## İşaretle ve tarif et
3B görünümün üst çubuğunda üç mod var:

| Mod | Ne yapar |
|---|---|
| **Seç** (S / Esc) | Yüze tıklayınca yüz seçilir. Ctrl ile birden fazla yüz seçilir. |
| **📍 İşaretle** (M) | Parçada bir noktaya tıklayın. Fare köşeye, sonra kenara, sonra yüze yapışır (renkler: kırmızı köşe, turuncu kenar, yeşil yüz). Ardından orada ne istediğinizi yazın, ör. "bu kenarı 5 mm kısalt", "buraya Ø8 delik". |
| **📏 Ölçü** (D) | İki noktaya tıklayın; aradaki mesafe ve ΔX/ΔY/ΔZ gösterilir. Hedefi yazın, ör. "40 mm olsun". |
| **╱ Çizgi** (L) | Noktalara sırayla tıklayın. Enter ya da çift tık ile bitirin; Backspace son noktayı siler. Ör. "buradan kes", "bu hat boyunca 2 mm kanal". |
| **◯ Daire** (C) | Önce merkeze, sonra yarıçap kadar uzağa tıklayın. Daire tıklanan yüzün düzleminde çizilir. Ör. "buraya bu çapta delik". |
| **✎ Kalem** (P) | Yüzeyin üzerinde fareyi basılı tutup serbest çizin; iz yüzeyi takip eder. Ör. "bu bölgeyi 2 mm incelt". Boşlukta sürüklerseniz görünüm döner. |

- İşaretler numaralanır (#1, #2…), görünümde ve **Kontrol → İşaretler** listesinde görünür. Listeden notları düzenleyebilir ya da işareti silebilirsiniz.
- İşaretler **FreeCAD dosyasının içinde saklanır**; dosyayı kapatıp açınca kaybolmaz.
- **Uygulat** düğmesi işaret listesini seçtiğiniz yapay zekâ ajanına gönderir.
- Ajan `get_markers` aracıyla her işaretin tam koordinatını, altındaki yüz/kenar/köşeyi ve o elemanın şimdiki geometrisini okur.

## Tasarım şartları

Ajana “kalınlık 8 mm ve kapakla boşluk en az 2 mm olsun; bu şartları koru” diyebilirsiniz.
`set_design_requirements` istenen boyut, hacim, katı sayısı, minimum mesafe ve ölçü ilişkilerini `.FCStd` içinde saklar.
Ajan/MCP araçlarıyla yapılan değişiklikler ölçülen değer, hedef ve tolerans içeren `design_validation` raporu döndürür.
`check_design_requirements` denetimi tekrar çalıştırır; uygun olmayan veya ölçülemeyen sonuç başarılı sayılmaz.
Kontrol panelindeki **Tasarım şartları** bölümünden şart ekleyin/düzenleyin, parçasını seçin veya JSON raporu kaydedin.
Panel, belge değişince denetimi yeniler. Delik sayısı/çapı, delik merkezi–sınır uzaklığı ve çakışma hacmi de denetlenir.
**Ölçüleri bağla**, iki ölçüyü FreeCAD bağıntısıyla ilişkilendirir. Ajanın `add_mounting_plate` aracı dört köşe delikli
parametrik plaka oluşturur; boyut değişince deliklerin kenar uzaklıkları ve boydan boya kesme derinliği korunur.
FreeCAD'in kendi ajanı son doğrulamayı geçemediğinde iki düzeltme turu dener; sonuç hâlâ uygun değilse bunu bildirir.
Delik tanıma tam silindirik yüzlerle, kenar uzaklığı dünya eksenlerindeki sınırlayıcı kutuyla sınırlıdır.

## Hangi yapay zekâ?
Hangi pencereden çalışıyorsanız onu kullanın. Hepsi aynı `cadai` MCP araçlarına (açık FreeCAD ya da Fusion kendiliğinden bulunur) ve aynı kurallara (`AGENTS.md`) bağlıdır. Seçim için **Kontrol → Yapay zekâ → Ajan** listesini ya da "CadAI: Yapay zekâ ajanını seç" komutunu kullanın; listede yalnızca kurulu olanlar görünür.

| Ajan | İşaretler nasıl gider |
|---|---|
| Claude Code (terminal), Codex (terminal) | Terminalde ajan açılır, mesaj doğrudan gönderilir |
| GitHub Copilot Chat | Mesaj sohbet kutusuna yazılır, siz gönderirsiniz |
| Claude Code, Codex, Cline, Kilo Code, Roo Code (paneller) | Mesaj panoya kopyalanır ve panel açılır; Ctrl+V ile yapıştırırsınız |
| Sadece panoya kopyala | İstediğiniz yere yapıştırırsınız |

MCP sunucusunun tanımlı olduğu yerler:
- VS Code / Copilot: eklenti sunucuyu **VS Code'un yerel MCP API'siyle** kendisi tanıtır (`mcp.json` gerekmez)
- Claude Code: kullanıcı ayarı ve projedeki `.mcp.json`
- Codex: `~/.codex/config.toml`
- Cline ve Kilo: kendi MCP ayar dosyaları

Claude Code'da hazır komutlar da gelir: `/mcp__cadai__apply_markers`, `/mcp__cadai__fem_check`,
`/mcp__cadai__inspect_model`.

## Analiz ve üretim
- **FEM renk haritası:** Statik analiz bitince 3B görünüm, şekil değiştirmiş parçayı von Mises gerilmesi ya da yer
  değiştirmeye göre boyar. Lejant, %99'da kırpma (mesnet tekillikleri), şekil değiştirme ölçeği ve imlecin altındaki
  değer gösterilir. Sonuçta **kuvvet dengesi** (mesnet tepkileri = uygulanan yük) de raporlanır.
- **Üretilebilirlik (DFM):** FDM 3B baskı, 3 eksen CNC, plastik enjeksiyon ya da sac metal seçin; seçili parça ölçülür,
  bulgular (ince duvar, sarkma, keskin iç köşe, derin delik, alttan kesme, koniklik…) listelenir ve modelde boyanır.
- **Teknik resim (A3 PDF):** Kontrol → Dışa aktar → **Teknik resim (PDF)**. Seçili (ya da ilk görünür) parçanın Türkçe
  antetli A3 resmi: ön, sol ya da Kesit A-A, üst ve izometrik görünüş; ölçek, toplam boyutlar, delik çapları, merkez
  çizgileri ve malzemeden hesaplanan ağırlık. Başlık ve malzeme sorulur; PDF ve PNG .FCStd'nin yanına yazılır. Ek ölçü
  ve notları yapay zekâya söyleyin ("Ø28 yuvaya 0/-0,01 tolerans yaz"): ölçüleri modelden alır, uydurmaz.
- **Render:** Kontrol → **Render**. Taslak / son kalite (Blender Cycles, ekran kartında), dönen animasyon (GIF) ya da
  Blender'sız hızlı render; kamera açısı sorulur, seçili parçalar (yoksa görünenlerin hepsi) çizilir. PNG .FCStd'nin
  yanına yazılır ve VS Code'da açılır. Malzemeyi yapay zekâya söyleyin ("kapak turuncu plastik, gövde eloksal mavi").
- **Diğer programlar:** Kontrol → "Render ve diğer programlar" → **Dosyadan aktar**: OpenSCAD (.scad), build123d /
  CadQuery (.py) ya da KiCad kartı (.kicad_pcb) modele katı olarak gelir. **Kurulum…** hangi programın kurulu olduğunu
  gösterir; eksik olanı komutunu gösterip onayınızla kurar (Windows'ta winget). Yollar ayarlardan da verilebilir
  (`cadai.external.*`, makine düzeyi).
- **Standart parçalar (step.parts):** 16 000+ açık STEP parça; arayın, **Ekle** ile modele koyun (seçili yüz varsa
  oraya). Dosyalar SHA-256 ile doğrulanır.
- **Kesit (X):** Görünümü X → Y → Z düzleminde keser; kaydırıcıyla konumu ayarlayın.

## Tasarım geçmişi (isteğe bağlı)
CadAI kenar çubuğundaki **Tasarım geçmişi** görünümünde "Geçmişi aç" tuşu (ya da Kontrol → "● Geçmişi aç"). Açıkken
modeldeki her değişiklik (yapay zekâ, VS Code ya da FreeCAD'de elle) ayrı bir klasörde git commit'i olur:
`Beam.Length 100 mm → 120 mm` gibi okunur bir mesaj, farkı görülebilen `model/model.json`, FCStd kopyası ve
**Obsidian** ile açılabilen notlar (günlük, parça notları, `[[bağlantılar]]`). Geçmiş ağacı günlere ayrılır; her kayıt
açılınca neyin değiştiği görünür (tıklanan parça modelde seçilir). Kaydın yanındaki tuşlar: eski sürümü yeni belge
olarak aç (asıl dosyanız değişmez) ve notu aç. Başlıktaki ⏸ tuşu kaydı durdurur; var olan geçmiş silinmez.
Geliştiriciler `cadai.history.useWorkspace` ile geçmişi projenin deposuna (yalnızca `cadai-history/` klasörü)
commit'leyebilir. Hiçbir şey uzağa gönderilmez.

## Performans ve ekran kartı
"GPU / performans" (Kontrol → Geliştirici) FreeCAD'in ve 3B görünümün hangi GPU'da çalıştığını gösterir; her marka
(AMD, NVIDIA, Intel, Apple, Qualcomm) için çözüm önerir. Monitör bağlantısı, uygulamanın kullandığı GPU'yu tek başına
belirlemez; tanılama ikisini birlikte değerlendirir. Gerektiğinde kablo bağlantısını kontrol edin ya da uygulamaya
"Yüksek performans" GPU'su atayın.

3B görünüm, çalışan bir **WebGL 2** sürücüsü olan ekran kartlarında markadan bağımsız olarak kendini ayarlar:
- Sürücü bazı ayarları reddederse (kenar yumuşatma, güçlü GPU isteği) bir sonraki ayarla açılır. WebGL 2 hiç yoksa
  boş bir pencere yerine nedenini ve "GPU tanılaması" tuşunu gösterir. Son denemede güç tercihi tamamen kaldırılır.
- Büyük/HiDPI pencerelerde çizim çözünürlüğü GPU'nun renderbuffer, texture ve viewport sınırlarını aşmaz; pencere
  yeniden boyutlanınca sınırlar yeniden uygulanır. Tarayıcı GPU adını gizlese de çizim ve seçim çalışır.
- Sürücü sıfırlanırsa (Windows TDR, uyku, sürücü güncellemesi, dizüstünde GPU değişimi) görünüm kendiliğinden geri
  gelir ve modeli yeniden yükler.
- Zayıf, eski ya da yazılımla çizen GPU'larda görünüm döndürülürken kare süresi ölçülür. Akıcılık 20 kare/sn'nin
  altına düşerse çözünürlük kademeli olarak azaltılır, akıcı kalınca geri verilir.
- Tanılama sürücüsü kurulmamış kartı ("Microsoft Basic Display Adapter"), sunucu ekran yongalarını (ASPEED, Matrox)
  ve uzak masaüstü/yayın ekran bağdaştırıcılarını tanır. FreeCAD'e VBO'yu yalnızca OpenGL 3+ destekleyen gerçek bir
  GPU'da önerir.

## Diğer özellikler
- **Model ağacı:** Ölçüyü düzenle (kalem), göster/gizle, sil.
- **Kontrol paneli:**
  - Yeni / Aç (FCStd, STEP, STL) / Kaydet
  - Geri al / Yinele
  - Seçimi ölç
  - FEM analizi (statik ve modal)
  - STEP/STL dışa aktarım (yapay zekâ ayrıca IGES, BREP, GLB, 3MF yazabilir), teknik resim (PDF + PNG)
- **Geliştirici:** Testleri çalıştır, FreeCAD eklentisini yeniden yükle, hata ayıklayıcıyı FreeCAD'e bağla.

## Başlarken
1. Sol çubuktaki **CadAI** simgesine tıklayın, sonra **FreeCAD'i başlat** düğmesine basın.
2. **Aç** ile parçanızı açın (FCStd ya da STEP).
3. 3B görünümde **📍 İşaretle** modunu seçin, değişmesini istediğiniz yerlere tıklayıp tarif edin, sonra **Yapay zekâya uygulat**.

Gerekenler: FreeCAD 1.0 veya üstü ve FreeCAD'e kurulu CadAI eklentisi (`freecad/CadAI`).
