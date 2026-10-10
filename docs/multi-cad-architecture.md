# CadAI — Çoklu CAD mimarisi

Karar tarihi: 8 Ekim 2026. Durum: v0.17.0 ortak köprü ve FreeCAD adaptörü uygulandı. v0.18.0 (10 Ekim 2026): Fusion adaptörünün kök parça düzenleme araçları (aşama 3) gerçek Fusion'da `fusion/tests/live_smoke.py` ile doğrulandı; alt bileşen düzenleme ve montaj betiğinin gerçek çalıştırması bekleniyor. SolidWorks ileriki hedef.

Uygulanan çekirdek `freecad/CadAI/cadai_core` altında, CAD API'sinden bağımsızdır. FreeCAD'in kurulum yapısını korumak için bu konum kullanılır; aynı kaynak Fusion ve VS Code paketlerine de eklenir. Mevcut FreeCAD araç kayıt/normalleştirme mantığı korunur; yeni adaptörler bağımsız `cadai_core.registry` sözleşmesini kullanır. [Fusion kurulum ve kapsamı](../fusion/README.md).

## Hedef

CadAI, ortak AI araçları, model üzerinde işaretler ve VS Code görüntüleyicisi sunan açık kaynak bir platform olacak. Mevcut FreeCAD desteği korunacak; Fusion 360 ikinci adaptör, SolidWorks ileriki hedef olacak. Yeni bir CAD programı için tüm platformu yeniden yazmak yerine adaptör eklenebilecek.

CadAI kaynak kodunun açık olması, bağlandığı ticari CAD programını açık kaynak ya da ücretsiz yapmaz. Kullanıcı o programı kendi kurulumu ve kullanım haklarıyla çalıştırır.

## Katmanlar

```text
VS Code görüntüleyicisi + işaretler       AI ajanları
                 |                          |
                 +-------- CadAI -----------+
                    ortak araç sözleşmesi
                    oturum yönlendirme
                    ölçüm ve doğrulama
                              |
              +---------------+---------------+
              |               |               |
       FreeCAD adaptörü  Fusion adaptörü  SolidWorks adaptörü
        (doğrulanmış)  (kök parça doğr.)     (ileride)
              |               |               |
           FreeCAD          Fusion          SolidWorks
```

Yerel CAD belgesi modelin asıl kaynağıdır. Ortak katman bir CAD çekirdeği veya bütün programların parametrik geçmişini temsil eden yeni bir dosya biçimi olmayacak. Geometri aktarımı ile program içinde parametrik düzenleme ayrı iş akışlarıdır; STEP aktarımı özellik geçmişini taşıyormuş gibi sunulmaz.

## Mevcut koddan geçiş

- `freecad/CadAI/cadai/bridge.py`: yerel HTTP, kimlik doğrulama ve araç manifesti yaklaşımı yeniden kullanılabilir. FreeCAD ayar yolu ve GUI dispatch işlemi adaptör tarafında kalır.
- `freecad/CadAI/mcp_server/cadai_mcp.py`: MCP taşıma ve araç sunma davranışı ortaklaştırılabilir. FreeCAD keşif yolları, istemler ve program kimliği ayrılmalıdır.
- `freecad/CadAI/cadai/tools/`: kayıt, argüman normalleştirme ve sonuç sözleşmeleri incelenerek ortak çekirdeğe alınır. FreeCAD nesnelerine dokunan araç uygulamaları FreeCAD adaptöründe kalır.
- `freecad/CadAI/cadai/ui_actions.py`: sahne, ağaç, seçim ve işaret sözleşmeleri ortaklaştırılır; üçgenleme, nesne gözlemcileri ve belge erişimi adaptöre aittir.
- `vscode/cadai-vscode/`: görüntüleyici ve işaret arayüzü korunur. Program başlatma, köprü keşfi, komutlar ve kullanıcı metinleri seçilen oturuma göre çalışır.

İlk adım büyük bir klasör taşıması değil, mevcut davranışın sınırlarını belgelemek ve FreeCAD'i aynı sözleşmenin ilk uygulaması yapmaktır. Çalışan MCP bağlantısı geçiş boyunca korunur; 0.19'dan beri adı yalnızca `cadai` (FreeCAD ve Fusion'ı kendisi bulur).

## Adaptör sözleşmesi

### Açık oturum ve belge seçimi

Her bağlantı `backend_id`, `session_id`, `document_id` ve `revision` bilgisi taşır. Kullanıcı işlem yapılacak CAD oturumunu seçer. Aynı anda açık FreeCAD ve Fusion oturumlarından biri dosya tarihine veya son keşfedilen köprüye bakılarak sessizce tercih edilmez.

Değişiklik isteği hedef oturuma ve belgeye bağlıdır. Belge kapanmışsa veya kullanıcı başka belgeye geçmişse işlem yanlış modele uygulanmaz. Eski revizyondan gelen seçim, işaret ve geometri referansları yeniden çözülür; güvenilir eşleşme yoksa işlem açıklayıcı hata döndürür.

### Yetenek bildirimi

Adaptör, program ve adaptör sürümünü, desteklediği araçları ve sınırlamalarını bildirir. Desteklenmeyen işlem başarı gibi gösterilmez; AI'a sunulan manifest seçilen oturumun gerçek yeteneklerinden üretilir.

İlk ortak kapsam: belge özeti, model ağacı, sahne, seçim, yüz/kenar inceleme, ölçme, parametre okuma/değiştirme, temel katı oluşturma, delik, yuvarlatma/pah, taşıma ve dışa aktarma. Her araç ayrı yetenek olarak bildirilir; bu listenin tamamı bütün adaptörlerde var sayılmaz.

FEM, DFM, teknik resim, render, CAM ve tasarım geçmişi ayrı yetenek gruplarıdır. Mevcut FreeCAD uygulamasının sunduğu bu özellikler diğer programlara kendiliğinden taşınmış sayılmaz.

### Birimler ve referanslar

Ortak katmanda uzunluk mm, kuvvet N, gerilme MPa, yoğunluk kg/m³ ve açı derece olarak tanımlanır. Alan mm², hacim mm³, kütle kg kullanır; ölçüm sonuçları birimini açıkça taşır. Adaptör, CAD API'sinin yerel birimleriyle dönüşümü girişte ve çıkışta yapar.

Nesne, yüz ve kenar kimlikleri adaptöre ait referanslardır; `Face1` bütün programlarda aynı anlama gelmez. Ortak referans oturum, belge ve revizyon bağlamını içerir. Geometrik özelliklerle yeniden eşleştirme yapılırsa belirsizlik saklanmaz; birden fazla adaydan biri tahmin edilerek düzenlenmez.

Parametre araçları düzenlenebilir ölçüleri, ifadeleri ve yerel kısıtları bildirir. FreeCAD'deki herhangi bir nesne özelliğinin Fusion veya SolidWorks'te birebir karşılığı olduğu varsayılmaz.

### Değişiklik ve doğrulama

Model inceleme → değişiklik → gerçek geometri üzerinde ölçüm akışı bütün adaptörlerde korunur. Başarısız doğrulama, yapılmış değişikliği geri alınmış gibi raporlamaz. Sonuç işlem durumu ile doğrulama durumunu ayrı gösterir.

Adaptör, işlemleri CAD programının gerektirdiği iş parçacığında yürütür. Ağ ve uzun harici işler CAD arayüzünü bloke etmeden yönetilir. Geri alma, atomiklik ve iptal desteği açıkça bildirilir; bütün programlar için aynı garanti verilmez.

Serbest kod çalıştırma program özelinde kalır. FreeCAD tarifleri Fusion'da çalıştırılmaz. Çözülmüş araç adı ve normalleştirilmiş argümanlar onay mekanizmalarına verilir. Dış dosyalardan gelen güvenilmeyen işaret notları için mevcut güvenlik kuralı bütün adaptörlerde korunur.

### Sahne ve işaretler

VS Code'a ortak sahne biçimiyle geometri, nesne ağacı, görünürlük ve seçim eşlemesi gönderilir. Değişmeyen nesneler tekrar üçgenlenmez/gönderilmez; adaptör güvenilir değişiklik tespiti yapamıyorsa bunu bildirir ve güvenli tam yenileme kullanır.

İşaretlerin koordinatları, bağlı referansları ve güven durumu korunur. Saklama yeri programın desteklediği belge verisi veya açıkça tanımlanmış eşlikçi dosya olabilir. Model değişince kopan işaretler kullanıcıya gösterilir. Bir programdan diğerine aktarılan geometrinin yüz kimlikleri veya işaret bağlantıları aynı kalmış gibi sunulmaz.

## Aşamalar ve kabul ölçütleri

1. **FreeCAD sözleşmesi:** Mevcut araç/sahne davranışını belgeleyip adaptör sınırını kur. Mevcut MCP istemcileri ve VS Code iş akışları çalışmaya devam etmeli. Projedeki FreeCAD, MCP, VS Code ve görüntüleyici kontrolleri geçmeli.
2. **Fusion bağlantısı:** Ayrı eklenti ile oturum/yetenek keşfi, belge özeti, seçim ve ölçüm sağla. İki program aynı anda açıkken her istek açıkça seçilmiş belgeye gitmeli; birim dönüşümleri gerçek modelden doğrulanmalı.
3. **Fusion düzenleme:** Parametre değiştirme, temel geometri, delik, yuvarlatma/pah ve dışa aktarmayı sırayla ekle. Yeniden açma, model değişikliğinden sonra referans çözme ve desteklenen geri alma davranışı doğrulanmalı.
4. **Ortak görüntüleyici ve işaretler:** Fusion sahnesini mevcut arayüze bağla. Seçim, işaret saklama ve değişiklik sonrası ölçüm uçtan uca doğrulanmalı; FreeCAD iş akışı da yeniden kontrol edilmeli.
5. **SolidWorks ve diğer adaptörler:** Aynı sözleşmeyi uygula. Yeni adaptörün eklenmesi ortak arayüzü ve diğer adaptörleri değiştirmeyi gerektiriyorsa sözleşme yeniden değerlendirilmeli.

Her adaptör için destek matrisi ve gerçek CAD ortamında doğrulama sonuçları yayımlanır. Sahte adaptör testleri gerçek model üzerinde ölçümün yerine geçmez. Performans, aynı örnek parçalarda işlem, geometri aktarımı ve görüntü güncelleme süreleriyle karşılaştırılır; ölçümden önce hız üstünlüğü iddia edilmez.

## İlk kapsamın sınırı

Öncelik, mevcut model üzerinde seçime ve işaretlere dayalı güvenilir düzenlemedir. Programlar arasında eksiksiz parametrik geçmiş aktarımı, bütün analiz/üretim özelliklerinde eşitlik ve tüm CAD programlarını ilk sürümde desteklemek bu başlangıç kapsamına dahil değildir.
