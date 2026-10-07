# CadAI — Açık Kaynak, Yapay Zekâ Destekli Mühendislik Tasarım Ortamı

> **Durum:** Plan + ilk uygulama. FreeCAD eklentisi v0.1 yazıldı (Bölüm 12).
> **Tarih:** 3–5 Ekim 2026 · **Sürüm:** 0.10
> Netleşmesi gereken konular Bölüm 11'de.

---

## 1. Amaç

Mühendislikte yaygın iş akışı üç ticari araca dayanıyor: **SolidWorks** ile çizim, **MATLAB** ile animasyon, **ANSYS** ile analiz. CadAI bu işleri **açık kaynak araçlarla**, **tek bir VS Code ortamında** ve **yapay zekâ desteğiyle** yapmayı hedefliyor.

- **Hibrit çalışma:** Katı modeller hem doğrudan müdahaleyle (tıklama, ölçü değiştirme) hem de doğal dilde yazılan komutlarla oluşturulup düzenlenir.
- **Modelden bağımsız yapay zekâ:** Bulut modelleri (Claude, GPT, Gemini) de yerel modeller (Ollama, LM Studio) de bağlanabilir.
- **Önce hazırı kullan:** Mevcut açık kaynak projeler sıfırdan yeniden yazılmaz; birleştirilir ve geliştirilir.
- **Ticari araçları kullanmak hedef değil.** Yine de mimari, lisansı olan kullanıcıların bu araçları ileride bağlamasına izin verir.

## 2. Özet ve fizibilite

- **Yapılabilir.** Çizim, animasyon ve analizin her biri için olgun açık kaynak motorlar var. Çoğu için yapay zekâ ajanlarının kullanabileceği MCP sunucuları da yazılmış durumda.
- **Eksik olan:** Bunları tek ortamda, tek görüntüleyiciyle ve ortak bir veri akışıyla birleştiren bir proje bulunamadı. CadAI'nin özgün katkısı bu birleştirici katman (Bölüm 5).
- **İlk adım:** Hiç kod yazmadan, hazır araçlarla 1–2 haftalık bir deney (Faz 0, Bölüm 10).

> **MCP (Model Context Protocol):** Yapay zekâ ajanlarının dış araçları (CAD, çözücü, dosya sistemi vb.) standart bir arayüzle çağırmasını sağlayan açık protokol. Cline, Claude Code, Cursor ve GitHub Copilot gibi ajanlar MCP sunucularına bağlanabilir.
>
> **Cline:** VS Code içinde çalışan açık kaynaklı (Apache-2.0) yapay zekâ ajanı. Dosya düzenleyebilir, komut çalıştırabilir ve MCP araçlarını kullanabilir; yerel modeller dahil çok sayıda model sağlayıcısını destekler.

## 3. Ticari araçların açık kaynak karşılıkları

| Görev | Bugün | Açık kaynak motor | Hazır AI köprüsü (MCP) |
|---|---|---|---|
| Çizim | SolidWorks | **build123d**: OCCT çekirdeği, gerçek katı model (B-rep), STEP çıktısı | build123d-mcp, freecad-mcp |
| Animasyon (hareketi göstermek) | MATLAB | build123d mafsalları (joints) + **OCP CAD Viewer** animasyonu | Görüntüleyicinin kendi özelliği |
| Dinamik simülasyon | MATLAB / Simscape | **Project Chrono**; sistem modeli için OpenModelica | mcp-chrono, mcp-modelica (çok erken) |
| Hesap ve grafik | MATLAB | Python (NumPy, SciPy, Matplotlib) + Jupyter | Gerekmez; ajan Python yazıp çalıştırır |
| Yapısal analiz | ANSYS Mechanical | **Gmsh** (mesh) + **CalculiX**; alternatifler: Elmer, Code_Aster, FEniCSx | mcp-calculix, CAE-Agent-Hub, openPASO |
| Akış analizi (CFD) | ANSYS Fluent | **OpenFOAM** | Foam-Agent |
| Sonuç görselleştirme | ANSYS sonuç ekranı | ParaView, PyVista | CAE-Agent-Hub görüntüleyicisi |
| Yapay zekâ ajanı | — | **Cline**; yerel model için Ollama, LM Studio | — |

## 4. Mevcut projelerin değerlendirmesi

| Proje | Lisans | Durum | Ne yapıyor | Karar |
|---|---|---|---|---|
| **build123d-mcp** | Apache-2.0 | Aktif, ölçümle kanıtlı | build123d oturumu, görüntü alma, ölçüm, delik bulma ve düzenleme, STEP/STL çıktısı | **Olduğu gibi kullan** |
| **Casys-AI paketi** | MIT | Çok erken | mcp-build123d, mcp-calculix, mcp-chrono, mcp-modelica, mcp-dfm: geometri → fizik → ölçülmüş kontrol → kanıt | **Fork'lanıp geliştirilmeye en uygun aday** |
| **CAE-Agent-Hub** | MIT | ~1000 yıldız | Ticari CAE köprüleri + CalculiX + FreeCAD → Elmer → ParaView akışı + tarayıcıda sonuç görüntüleyici | **Açık kaynak parçalarını al** |
| **openPASO** (eski adı OASiS) | Doğrulanmadı | Araştırma projesi, aktif | Tek MCP'den 9 sonlu eleman / çoklu fizik kodu; her simülasyondan önce denetçi ajan kontrolü | **Doğrulama yöntemini örnek al** |
| **Foam-Agent** | MIT | NeurIPS 2025 çalıştayı | OpenFOAM v10'u MCP + Docker ile ajana açıyor | **CFD gerekirse kullan** |
| **FreeCAD + freecad-mcp** | FreeCAD: LGPL; MCP: doğrulanmadı | ~2600 yıldız | Çizim, montaj ve FEM tek arayüzde | **Ana yol değil** (önce arayüz yaklaşımı, kod + AI fikrinden uzak) |
| **OCP CAD Viewer** | Apache-2.0 | 4.x, aktif | VS Code içinde 3D görüntüleme, GPU tabanlı seçim, ölçüm, animasyon | **Görüntüleyicinin temeli** |
| **Cline / @cline/sdk** | Apache-2.0 | Aktif; SDK Mayıs 2026'da çıktı | Ajan döngüsü, MCP, onay, geri alma noktaları; SDK ile başka uygulamaya gömülebiliyor | **Ajan katmanı** |

**Notlar**
- **build123d-mcp:** Hugging Face'in CADGenBench testinde aynı modelin puanını 0.360'tan 0.457'ye, geçerli geometri oranını %88'den %100'e çıkarmış. Eksiği: canlı görüntüleyicisi yalnızca izleme yapıyor, kullanıcının seçimi ajana ulaşmıyor.
- **Casys-AI:** Vizyonu CadAI'ye en yakın proje. Zayıf yanları: yıldız sayısı tek haneli, yapı Docker/Deno ağırlıklı, Windows desteği belirsiz. mcp-calculix (v0.8.6) lineer statik, modal, lineer burkulma, sünme ve ısıl-mekanik bağlı analizleri destekliyor. Yük ve mesnet uygulanacak yüzler koordinat kutusuyla (bounding box) tarif ediliyor.
- **CAE-Agent-Hub:** Ağırlığı Abaqus, ANSYS ve HyperWorks köprülerinde. Çözücü lisanslarını içermiyor.

### Ticari araçlar için hazır köprüler (isteğe bağlı)
- **MATLAB:** MathWorks'ün resmi MATLAB MCP Core Server'ı (~1600 yıldız; MATLAB R2021a veya üstü gerekir).
- **ANSYS:** Resmi PyAnsys MCP sunucuları: PyMAPDL-MCP, PyMechanical-MCP, PyFluent-MCP (lisanslı ANSYS kurulumu gerekir).
- **SolidWorks:** Topluluk MCP'leri, ör. hjbaard/SolidWorks-MCP (MIT, 60 araç, 0.x sürüm, COM üzerinden çalışıyor).

## 5. CadAI'nin katkısı: eksik halka

1. **Tek görüntüleyici:** Model, animasyon ve gerilme/deplasman renk haritası VS Code içinde aynı 3D pencerede görünür.
2. **Seçim köprüsü:** Kullanıcı bir yüze tıklayıp "burası sabit" ya da "bu yüze 500 N" der. Seçim hem çizim koduna hem çözücünün yüz grubuna çevrilir. Mevcut araçlarda yüzler ya koordinatla tarif ediliyor ya da hiç seçilemiyor.
3. **Ortak proje akışı:** Her adımın girdisi ve çıktısı dosya olarak saklanır; akış tekrar üretilebilir ve git ile izlenir.
4. **Tasarım döngüsü:** "Gerilme 150 MPa'yı geçmesin, ağırlık en az olsun" gibi hedeflerle AI ölçüleri değiştirir, modeli yeniden çizer ve yeniden analiz eder.
5. **Mühendislik kontrolleri:** Birim tutarlılığı, kuvvet dengesi (mesnet tepkileri = uygulanan yük), mesh yakınsaması, el hesabıyla karşılaştırma.

## 6. Mimari

```
VS Code
├─ Yapay zekâ ajanı: Cline + CadAI kuralları (.clinerules)
│   ├─ Modeller: Ollama · LM Studio (yerel) | Claude · GPT · Gemini (bulut)
│   └─ MCP sunucuları
│       ├─ cad     → build123d-mcp              çizim, ölçüm, STEP
│       ├─ motion  → build123d mafsal + Chrono  kinematik / dinamik
│       ├─ fea     → Gmsh + CalculiX MCP        statik, modal, burkulma, ısıl
│       ├─ cfd     → Foam-Agent                 isteğe bağlı (Docker / WSL2)
│       └─ cadai   → CadAI: seçim köprüsü, proje akışı, kontroller, rapor
└─ CadAI VS Code eklentisi: tek 3D görüntüleyici (three-cad-viewer tabanlı)
    model + animasyon + sonuç renk haritası + yüz seçimi → ajan bağlamı
```

### Proje akışı

```
parametreler → model.py → STEP → mesh → çözüm → sonuçlar → kontroller → rapor
```

Kontrol sonuçlarına göre AI ya da kullanıcı parametreleri günceller ve akış baştan çalışır.

### Temel tasarım kararları

| Karar | Gerekçe |
|---|---|
| Çizim motoru **build123d** (CadQuery ile aynı OCCT altyapısı) | Gerçek katı model: köşe yuvarlatma, pah, STEP, kalıcı yüz/kenar seçimi. AI-CAD araçları build123d etrafında toplanmış. PythonSCAD (OpenSCAD türevi) yüzeyleri üçgenlerle yaklaşık olarak modelliyor ve GPLv2 lisanslı; bu yüzden ana motor olarak uygun değil. |
| **Tek gerçek kaynak Python kodu** | Arayüz de AI da aynı dosyaya yazar. Sürümler git ile tutulur, her değişiklik okunabilir ve geri alınabilir. Zoo Design Studio da aynı ilkeyi kendi KCL diliyle uyguluyor. |
| **Cline fork edilmez** | Faz 0–2'de Cline olduğu gibi kullanılır, araçlar MCP ile eklenir. Bağımsız uygulama gerekirse @cline/sdk gömülür (özel araçlar, özel sistem istemi, onay mekanizması, oturum kaydı). |
| **Çözücüler ayrı süreçte çalışır** | GPL'li çözücülerle (CalculiX, OpenFOAM, Gmsh) lisans ayrımı korunur; çökme ve zaman aşımı yönetilir; AI'ın yazdığı kod ana uygulamadan yalıtılır. |
| **Mühendislik kontrolleri temel özelliktir** | AI'ın hatalı sınır koşulu ya da birimle "ikna edici ama yanlış" sonuç üretmesine karşı. |

## 7. Hibrit düzenleme: tıklama + komut

Rastgele yazılmış Python kodunu arayüzden geri düzenlemek genel olarak mümkün değil (döngüler, fonksiyonlar vb.). Çözüm, bir **kod yazım sözleşmesi** (parametreler dosyanın başında, özellikler sıralı ve isimli) ve kademeli ilerlemek. AI'a bu sözleşme Cline kurallarıyla (.clinerules) uygulatılır.

| Kademe | Kullanıcı ne yapar | Sistem ne yapar | Zorluk |
|---|---|---|---|
| 1. Parametre | Kaydırıcıdan değer değiştirir ya da ölçü okunu sürükler | Koddaki sayıyı biçimi bozmadan değiştirir (libcst) ve modeli yeniden üretir | Kolay |
| 2. Seçim + komut | Yüz/kenar seçip "bu deliği 2 mm büyüt" der | Seçimi AI bağlamına ekler: geometrik özellikler, o yüzü üreten kod satırı, sağlam seçici önerisi | Orta (ürünün kalbi) |
| 3. Doğrudan işlem | Kenar seçip "2 mm yuvarlat" der, yüzü iter/çeker | AI kullanmadan, kurala dayalı kod satırı ekler | Orta-zor |
| 4. Eskiz | Kısıtlı 2B çizim yapar | Kısıt çözücü sonucunu build123d eskiz koduna çevirir | Zor |

İki teknik anahtar:
- **Köken izleme (provenance):** build123d işlemleri sarmalanır ve her yüz onu üreten kod satırıyla etiketlenir. Tıklanan yüzden koddaki satıra gidilir.
- **Topolojik isimlendirme problemi:** Seçim koda sırayla (`faces()[7]`) değil anlamlı bir sorguyla yazılır ("en üstteki düz yüz", "yarıçapı 3 mm olan silindirik yüz"). Model değişince seçim bozulursa düzeltme işi AI'a verilir.

Analiz tarafında aynı seçim, çözücünün yüz grubuna (sabit mesnet, kuvvet, basınç) çevrilir.

## 8. Yapay zekâ ve yerel model stratejisi

- **Bağlantı:** Ollama (`localhost:11434`), LM Studio (`localhost:1234`) ya da OpenAI uyumlu herhangi bir uç nokta. Cline bunları doğrudan destekliyor.
- **Yerel model ayarları:** Cline'da "Use Compact Prompt" açılmalı. Bağlam penceresi yeterince büyük tutulmalı; Ollama'da küçük bırakılırsa uzun sistem istemi sessizce kesilebilir.
- **Donanım** (Cline dokümanına göre): 16–32 GB RAM küçük modeller için, 32–64 GB orta boy modeller için, 64 GB ve üstü büyük modeller için.
- **İş bölümü:**
  - Yerel model: ölçü değişikliği, seçime dayalı küçük düzenlemeler, hata düzeltme, sonuç yorumu.
  - Bulut model: sıfırdan karmaşık parça, çok adımlı analiz kurulumu.
- **Yerel modeli güçlendirmek için:**
  - Serbest kod yerine üst düzey araçlar vermek (ör. `add_hole(seçim, çap, derinlik)`).
  - build123d dokümanı ve örneklerinden bilgi getirme (RAG).
  - Görsel kontrol için görüntü okuyabilen bir model kullanmak (Qwen-VL, Gemma vb.).
- **AI'a "göz ve cetvel" vermek:** Her adımda birkaç açıdan görüntü ve ölçüm (hacim, sınır kutusu, yüz/kenar sayısı) alarak kendi çıktısını doğrulaması.

## 9. Gerçekçi beklentiler ve riskler

| Risk | Etki | Önlem |
|---|---|---|
| AI sıfırdan çizimde zayıf | CADGenBench özetine göre en iyi sistemler düzenleme görevlerinin ~%82–91'inde kullanılabilir geometri üretiyor, sıfırdan çizimde ise ~%2–10'da kalıyor | AI'ı düzenleyici olarak konumlamak; hazır şablonlar; görüntü + ölçüm ile doğrulama |
| Açık modeller geride | Qwen3-VL-235B ≈ 0.24, liste başı ≈ 0.68 (CADGenBench) | İş bölümü; yerel modele sınırlı rol |
| Açık kaynak FEA, ANSYS ayarında değil | Statik, modal, ısıl ve burkulmada güçlü; karmaşık temas, çarpışma ve ileri CFD'de zayıf ya da zor | Kapsamı günlük mühendislik işleriyle sınırlamak |
| AI'ın simülasyon kurulumunda hata yapması | Yanlış sınır koşulu ya da birim, ikna edici ama yanlış sonuç verir | Zorunlu kontroller: birim, kuvvet dengesi, mesh yakınsaması, el hesabı |
| AI'ın yazdığı kodun çalıştırılması | Güvenlik | Ayrı süreç, zaman aşımı, kullanıcı onayı; ileride sandbox/container |
| OCCT'nin kararlılığı ve hızı | Köşe yuvarlatma ve boolean hataları, yavaş yeniden üretim | Önbellek, kısmi yeniden üretim, hata mesajını AI'a geri vermek |
| Windows | OpenFOAM ve Code_Aster Linux odaklı | WSL2 ya da Docker |
| Lisans | GPL'li çözücüler | Ayrı süreç olarak çağırmak; CadAI kodu MIT/Apache kalır |

## 10. Yol haritası

### Faz 0 — Hazır araçlarla deney (1–2 hafta, kod yok)

**Kurulum:** VS Code + Cline + build123d + build123d-mcp + OCP CAD Viewer + bir CalculiX MCP'si (CAE-Agent-Hub ya da mcp-calculix) + Ollama veya LM Studio.

**Deney görevleri:** Her görev hem bir bulut modeliyle hem de yerel modelle çalıştırılır.

| # | Görev | Tür | Beklenen sonuç / kontrol |
|---|---|---|---|
| 1 | Çelik konsol kiriş: boy 100 mm, kesit 20 mm (genişlik) × 10 mm (yükseklik), serbest uca yükseklik yönünde 500 N | Çizim + statik analiz | El hesabı (E = 210 GPa): σ_max = 150 MPa, uç sehim ≈ 0,48 mm |
| 2 | Aynı kirişin ilk doğal frekansı | Modal analiz | El hesabı (ρ = 7850 kg/m³): f₁ ≈ 835 Hz |
| 3 | Krank-biyel mekanizması (krank r = 20 mm, biyel l = 60 mm) ve animasyonu | Montaj + animasyon | Piston konumu x(θ) = r·cos θ + √(l² − r²·sin²θ); strok = 2r = 40 mm; parçalar çakışmamalı |
| 4 | Hazır bir flanşta delik çapını 8 mm'den 10 mm'ye, delik sayısını 4'ten 6'ya çıkarmak | Düzenleme | Ölçülen çap ve delik sayısı doğru, geometri geçerli |
| 5 | M8 cıvatalar için 80×80×10 mm bağlantı plakası: 4 köşe deliği, kenarlarda 1 mm pah | Sıfırdan üretim | Delik çapı 9 mm (M8 için orta geçiş deliği), ölçüler ±0,01 mm, geometri geçerli |

> Görev 1 için not: Ankastre köşelerde gerilme yığılması (tekillik) olur. Gerilmeyi mesnetten biraz uzakta karşılaştırın. 3B katı modelde sehim, kayma etkisi nedeniyle el hesabından yüzde birkaç fazla çıkabilir.

**Ölçülecekler (her görev, her model için):**
- Geometri geçerli mi?
- Sonuç el hesabından yüzde kaç sapıyor?
- Kaç düzeltme turu gerekti, toplam ne kadar sürdü?
- Kullanıcı müdahalesi gerekti mi?
- Bulut modelinde maliyet (token) ne oldu?

**Faz 0 sonunda verilecek kararlar:**
- Yerel model hangi görevlerde yeterli? Buna göre iş bölümü netleşir.
- Seçilen CalculiX MCP'si Windows'ta sorunsuz çalışıyor mu? Çalışmıyorsa WSL2/Docker ya da diğer MCP denenir.
- Hangi temel üzerine kurulacak: Casys-AI fork'u mu, CAE-Agent-Hub parçaları mı, yoksa kendi yazacağımız ince bir MCP mi?

### Faz 1 — CadAI MCP sunucusu ve proje şablonu
- Proje akışını (parametre → model → STEP → mesh → çözüm → rapor) tek komutla çalıştırmak.
- Mühendislik kontrolleri: birim, kuvvet dengesi, mesh yakınsaması, el hesabıyla karşılaştırma.
- `.clinerules` dosyası: kod yazım sözleşmesi ve mühendislik kuralları.

### Faz 2 — VS Code eklentisi
- Tek 3D görüntüleyici (three-cad-viewer tabanlı): model, animasyon ve sonuç renk haritası bir arada.
- Seçim köprüsü: tıklanan yüz/kenar hem ajan bağlamına hem çözücünün yüz grubuna aktarılır.
- Hibrit düzenlemenin 1. kademesi: parametre paneli.

### Faz 3 — Tasarım döngüsü ve genişleme
- Hedef ve kısıtlara göre otomatik iyileştirme döngüsü.
- Project Chrono ile dinamik simülasyon.
- Gerekirse Foam-Agent ile akış analizi.

### Faz 4 — Tam hibrit CAD
- Hibrit düzenlemenin 3. ve 4. kademeleri: doğrudan işlemler ve eskiz.
- Gerekirse @cline/sdk ile bağımsız masaüstü uygulaması.

## 11. Açık kararlar

| # | Soru | Seçenekler ve etkisi |
|---|---|---|
| 1 | "Animasyon" ile ne kastediliyor? | (a) Mekanizmanın hareketini göstermek (dişli, krank-biyel, robot kol) → build123d mafsalları + OCP CAD Viewer. (b) Kuvvet, hız ve ivmenin hesaplandığı simülasyon → Project Chrono. (c) Sunumluk video → Blender. |
| 2 | Hangi analizler gerekli? | Gerilme, sehim, titreşim, ısı → CalculiX yeterli. Akış analizi de gerekiyorsa → OpenFOAM / Foam-Agent (WSL2 ya da Docker ile). |
| 3 | Kimler kullanacak? | Kişisel kullanım, ekip ya da öğrenciler. Arayüzün ve kurulum kolaylığının önceliğini belirler. |
| 4 | Donanım nedir (ekran kartı/VRAM, RAM)? | Yerel modelin üstlenebileceği rolü belirler. |

## 12. Uygulama: FreeCAD eklentisi (v0.1)

4 Ekim 2026'da yön değiştirdik: ana uygulama **FreeCAD** oldu. Sebep, FreeCAD'in Bölüm 7'deki zor kısımları (seçim, eskiz, doğrudan işlemler, montaj, FEM) zaten hazır sunması. Kod: [freecad/CadAI](freecad/CadAI/README.md).

- **Ne var:**
  - FreeCAD içinde sohbet paneli: Plan ve Act modları, her değişiklikte onay, değişiklik başına tek geri alma adımı, hata çıkarsa otomatik geri alma.
  - Seçime duyarlı bağlam.
  - Ekran görüntüsünü modele gönderebilme.
  - Ollama, LM Studio ve OpenAI uyumlu servisler, ayrıca Claude.
  - 13 araç: inceleme, ölçüm, parametrik düzenleme, Python ile modelleme, Gmsh + CalculiX ile statik ve modal analiz, kiriş el hesabı, STEP/STL dışa aktarım.
- **Doğrulama:** 13 başsız (headless) test gerçek FreeCAD 1.1.1 ve CalculiX ile geçiyor. Faz 0'daki 1. ve 2. görevler el hesabıyla tutuyor:
  - Sehim: 0,4745 mm (el hesabı 0,476 mm).
  - Gerilme: 149,4 MPa (el hesabı 150 MPa).
  - İlk doğal frekans: 836,7 Hz (el hesabı 835,5 Hz).

  Eklenti gerçek FreeCAD arayüzünde de açılıp test edildi.
- **freecad-ai ile ilişkisi:** Kod sıfırdan ve kompakt yazıldı; FEM araçları ve testler bu projeye özgü. İki proje de LGPL olduğu için parçalar ileride freecad-ai'ye katkı olarak gönderilebilir.
- **v0.2 (4 Ekim 2026): VS Code + FreeCAD birlikte.**
  - FreeCAD görüntü ve elle müdahale için kullanılır, VS Code yapay zekâ ve geliştirme için. Aradaki bağlantı şöyle kuruldu:
    - FreeCAD'de, açılışta kendiliğinden başlayan bir köprü. Yalnızca 127.0.0.1 adresini dinler ve her istekte anahtar ister.
    - Yalnızca standart kütüphaneyle yazılmış bir MCP sunucusu: `freecad/CadAI/mcp_server/cadai_mcp.py`.
  - MCP sunucusu Cline, Kilo Code, Claude Code ve Copilot için tanımlandı.
  - FEM analizi arka planda çalışabiliyor (`fem_status` ile durum sorulur).
  - VS Code'da test görevi, FreeCAD'i kapatmadan kodu yeniden yükleme, hata ayıklayıcı bağlantısı ve ajan kuralları (`.clinerules`) eklendi.
- **v0.2 doğrulaması:**
  - 16 başsız test geçiyor.
  - Gerçek FreeCAD arayüzüyle uçtan uca test yapıldı: VS Code'un yaptığı gibi başlatılan MCP sunucusu FreeCAD'de seçilen yüzü okudu. Analizi arka planda kurup çözdü; FreeCAD bu sırada donmadı. Sonuç 0,4745 mm ve 149,4 MPa çıktı. Ekran görüntüsünü de görüntü olarak gönderdi.
- **v0.3 (4 Ekim 2026): her şey VS Code içinde.**
  - [vscode/cadai-vscode](vscode/cadai-vscode/README.md) VS Code eklentisi eklendi. FreeCAD arka planda, simge durumunda motor olarak çalışıyor. Eklentide şunlar var:
    - Canlı 3B görünüm (three.js): yüze tıklayınca FreeCAD'de seçiliyor ve seçimi yapay zekâ da görüyor.
    - Model ağacı: ölçü düzenleme, gizleme, silme.
    - Kontrol paneli: belge işlemleri, şekil ekleme, ölçüm, FEM sihirbazı, STEP/STL, Cline'ı açma.
    - Geliştirici düğmeleri: test, yeniden yükleme, hata ayıklayıcı.
  - Köprüye `/ui` ve `/version` uç noktaları eklendi: sahne geometrisi, ağaç, seçim, belge komutları ve değişiklik sayaçları.
  - Bulunan ve düzeltilen hata: Windows'ta iki FreeCAD aynı portu paylaşabiliyordu. Artık port özel olarak bağlanıyor.
- **v0.3 doğrulaması:**
  - 16 başsız test geçiyor.
  - Gerçek FreeCAD arayüzüyle ayrı bir kullanıcı klasöründe 22 adımlı uçtan uca test geçti; eklentinin kendi köprü istemcisiyle yapıldı.
  - Eklentinin JavaScript dosyaları sözdizimi denetiminden geçti.
  - VSIX paketlenip VS Code'a kuruldu.
  - Görüntü ve panellerin VS Code içinde gözle kontrolü kullanıcıda.
- **v0.4 (4 Ekim 2026): göstererek tarif etme.** Kullanıcı VS Code'da şekil eklemeyecek; var olan parça üzerinde göstererek tarif etmek istiyor. Bu, AI'ın sıfırdan çizmekte zayıf, düzenlemede güçlü olduğu bulgusuyla da örtüşüyor.
  - 3B görünüme modlar eklendi:
    - **İşaretle:** köşe > kenar > yüz önceliğiyle yapışan numaralı işaret ve not.
    - **Ölçü:** iki nokta arası mesafe ve hedef not.
  - İşaretler FCStd dosyasının içinde (`doc.Meta`) saklanıyor.
  - Yapay zekâ işaretleri `get_markers` aracıyla okuyor: nokta, eleman ve elemanın güncel geometrisi.
  - "Yapay zekâya uygulat" düğmesi eklendi. "Şekil ekle" bölümü kaldırıldı.
  - **Doğrulama:**
    - 17 başsız test geçiyor (işaretlerin dosyada kalması dahil).
    - Gerçek FreeCAD arayüzüyle 27 adımlı uçtan uca test geçti. Görünümdeki köşe adları FreeCAD'dekilerle aynı çıktı.
    - Görüntüleyicinin kendisi başsız Edge'de (WebGL) benzetilmiş tıklamalarla 13 testten geçti: köşe, kenar ve yüze yapışma, gizli köşeyi yok sayma, 100 mm ölçü, işaretlerin çizilmesi.
- **v0.5 (4 Ekim 2026): parça üzerine çizim.** Kullanıcı daha iyi anlatabilmek için çizgi, daire ve kalem istedi.
  - Yeni araçlar:
    - **Çizgi:** köşeye, kenara ya da yüze yapışan çok noktalı çizgi.
    - **Daire:** merkez ve yarıçap; tıklanan yüzün düzleminde çiziliyor.
    - **Kalem:** yüzeyi takip eden serbest çizim.
  - Hepsi numaralı işaret olarak not alıyor ve FCStd dosyasında saklanıyor. Yapay zekâ bunları `get_markers` aracıyla türüne göre geometrisiyle okuyor (daire çapı, çizgi noktaları, kalem izinin yolu ve dokunduğu yüzler).
  - **Doğrulama:**
    - 18 başsız test geçiyor.
    - Görüntüleyici başsız Edge'de 23 testten geçti. Kalemle yüzeye çizerken görünüm dönmüyor, boşlukta sürüklenince dönüyor; daire düzlemi ve yarıçapı doğru; çizgi Enter ya da çift tıkla bitiyor.
- **v0.6 (4 Ekim 2026): ajandan bağımsız.** İsteyen Claude Code ile, isteyen Codex, Cline ya da Kilo ile geliştirebiliyor.
  - VS Code eklentisi kurulu ajanları kendisi buluyor (eklentiler ve `claude` ile `codex` komut satırı araçları), seçimi hatırlıyor. İşaretleri seçilen ajana gönderiyor: terminal araçlarına doğrudan, Copilot'a sohbet kutusuna, panellere pano üzerinden.
  - MCP sunucusu Claude Code'a kullanıcı düzeyinde (`claude mcp get`: Connected), Codex'e `config.toml` ile (`codex mcp list`: enabled), Cline, Kilo ve Copilot'a kendi ayar dosyalarıyla eklendi.
  - Ortak kurallar `AGENTS.md` dosyasında. `CLAUDE.md`, `.clinerules` ve `.kilocode/rules` bu dosyaya yönlendiriyor.
  - Ajan modülü 13 birim testinden geçti.
- **v0.7 (4 Ekim 2026): yayın öncesi güvenlik denetimi.**
  - Projede gizli anahtar, token ya da parola yok. Saklanan API anahtarı da yok.
  - Kişisel dosyalar `.gitignore` ile depodan çıkarıldı: sohbet notu `.md`, `.claude/settings.local.json`, yerel yol içeren ayarlar, derleme çıktıları.
  - Kapatılan 6 açık:
    1. Webview'den her VS Code komutunun çalıştırılabilmesi → izin listesi.
    2. Çalıştırılabilir program yolunun çalışma alanı ayarıyla değiştirilebilmesi → makine düzeyi ayar.
    3. Dosyayla gelen işaretlerle yapay zekâya talimat sokulabilmesi → HMAC imzası, `trusted` alanı ve uyarı.
    4. Not kutusunda kaçışsız nesne adı.
    5. Köprü sertleştirmesi: sabit zamanlı token karşılaştırması, Host denetimi, boyut sınırı, POSIX'te 0600 dosya izni.
    6. Komut satırı ajanına giden mesajın seçenek (`-`) sanılması.
  - Ayrıntılar `SECURITY.md` dosyasında. 20 başsız test geçiyor (yeni güvenlik testleri dahil); ajan ve görüntüleyici testleri de geçiyor.
- **v0.8 (4 Ekim 2026): yayına hazır.**
  - VS Code eklentisi FreeCAD'i Windows, macOS ve Linux'ta kendisi buluyor. FreeCAD eklentisini içinde taşıyıp FreeCAD'e kuruyor ve güncelliyor; geliştirici bağlantısına (junction) dokunmuyor.
  - "Yapay zekâ ajanlarına bağla" komutu, MCP sunucusunu Claude Code, Codex, Cline, Kilo, Roo ve VS Code/Copilot ayarlarına ekliyor. Ekleme birleştirerek yapılıyor; bozuk bir ayar dosyasının üzerine yazılmıyor.
  - Kişisel yollar kaldırıldı. Kök klasöre README (İngilizce + Türkçe özet), LICENSE (LGPL-2.1 resmi metni), CONTRIBUTING ve Marketplace simgesi eklendi.
  - Kurulum testleri: 18 test geçiyor (algılama, kurulum/güncelleme/geliştirici bağlantısı, JSON/TOML birleştirme).
- **v0.9 (4 Ekim 2026): modern kütüphaneler, analiz ve üretim.** Kullanıcının verdiği referanslar tarandı
  (text-to-cad, step.parts, CadQuery, opencascade.js, CADability, gcad3d, cad-ai-agent, openAI-to-freeCAD-workflow,
  AI-CAD, OCC CAD Builder, Aspose.CAD, cadscript). Alınanlar ve gerekçeler:
  - **step.parts** (MIT, 16 847 STEP parça, açık API): `search_parts` + `insert_part`. HTTPS izin listesi, yönlendirme
    denetimi, boyut sınırı, SHA-256 doğrulaması, önbellek.
  - **text-to-cad**'in ölçüme dayalı DFM fikri: `dfm_check` (FDM, 3 eksen CNC, enjeksiyon, sac). B-rep üzerinde ölçer;
    duvar kalınlığı ve alttan kesme numpy ile vektörel Möller–Trumbore ışın izlemesiyle (FreeCAD'in
    `nearestFacetOnRay` fonksiyonu ışın yönüne bakmıyor). Her bulguda ölçülen değer, sınır, yüz/kenar ve kural.
  - **openAI-to-freeCAD-workflow**'un RAG fikri: `freecad_recipes`, 9 test edilmiş FreeCAD kod kalıbı (her biri test
    paketinde gerçek FreeCAD'de çalışır).
  - Alınmayanlar: OCC CAD Builder ve Aspose.CAD ticari/kapalı lisanslı; opencascade.js 2023'ten beri güncellenmiyor;
    CadQuery/build123d FreeCAD süreciyle aynı OCCT'yi paylaşamaz (ileride ayrı süreç + STEP); CADability (C#),
    gcad3d (C), cadscript ve AI-CAD yığına uymuyor ya da çok erken.
  - **MCP 2026-07-28:** sunucu artık iki dönemli: durumsuz `server/discover` + istek başına `_meta`, eski
    `initialize` istemcileri için doğru sürüm anlaşması (eskiden istemcinin sürümü olduğu gibi geri yansıtılıyordu).
    Araç açıklamaları (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, başlık),
    `structuredContent`, kaynaklar (`cadai://markers`, `cadai://document`, `cadai://selection`), istem şablonları
    (`apply_markers`, `fem_check`, `inspect_model`; Claude Code'da eğik çizgi komutu), eşzamanlı istek işleme.
    Hâlâ yalnızca standart kütüphane.
  - **FEM:** kuvvet dengesi (CalculiX `.dat` toplam tepki kuvvetleri ↔ uygulanan kuvvet ve basınç bileşkesi),
    `fem_convergence` (2–4 mesh, değişim yüzdeleri, tekillik uyarısı), numpy ile yüzdelik, 3B renk haritası için
    yüzey alanı (`fem_field`).
  - **Dışa aktarım:** GLB/glTF ve BREP. Bulunan tuzak: OCCT'nin glTF yazıcısı şekil önceden üçgenlenmezse geometrisi boş
    dosya yazıyor; düzeltildi ve testlendi.
  - **VS Code:** three.js r160 → r186; three-mesh-bvh ile hızlandırılmış seçim; nesne başına tek birleşik mesh ve
    piksel genişlikli kenarlar (LineSegments2); FEM renk haritası (Lut, lejant, %99 kırpma, şekil değiştirme ölçeği,
    imleçteki değer); DFM bulgularının modelde boyanması; kesit düzlemi (kesilen kısımda seçim yapılmaz). three.js
    `Lut.getColor(max)` dizinin bir ötesini okuyor; korundu. Vendor dosyası esbuild ile tek ES modülü
    (`npm run vendor`). VS Code'un yerel MCP API'si (`registerMcpServerDefinitionProvider`) ile kayıt; eski `mcp.json`
    girdisi çift sunucu olmasın diye temizlenebiliyor.
  - **Araçlar ve CI:** `pyproject.toml` + ruff; Node'un yerleşik test çalıştırıcısı; Playwright + başsız Edge/Chrome ile
    görünüm testi (fixture'lar gerçek FreeCAD + CalculiX çalışmasından); GitHub Actions: lint, MCP (Python 3.9/3.11/
    3.13), eklenti + görünüm, FreeCAD 1.1.4 resmi taşınabilir paketiyle tam test paketi.
  - Test çalıştırıcısında bulunan iki gizli hata düzeltildi: başarısız bir testin mesajı konsol kodlamasıyla
    yazdırılamayınca tüm çalıştırma sessizce duruyordu; testler kullanıcının gerçek CadAI klasörüne yazıyordu.
- **v0.9 doğrulaması (FreeCAD 1.1.4 + CalculiX + Gmsh, Windows):**
  - FreeCAD testleri: 26/26 (önceki 20 + 6 yeni). Kuvvet dengesi: uygulanan −500 N, tepki +500 N, dengesizlik %0,0.
    Yakınsama (6 → 4,2 → 2,94 mm): sehim 0,47314 → 0,47411 → 0,4745 mm; %99 gerilmedeki son değişim %0,96.
    DFM: T parçada 600 mm² sarkma, 0,5 mm ince duvar, L blokta 1 keskin iç köşe, 6,7×D derin delik, yan delikte
    alttan kesme, sacta Ø1,5 < t delik. step.parts: ISO 4762 M8×10 indirildi, SHA-256 doğrulandı, modele eklendi.
  - MCP testleri (FreeCAD gerekmez): 17/17; FreeCAD'in Python 3.11'inde de geçiyor.
  - Eklenti birim testleri 9/9; görünüm uçtan uca testi başsız Edge'de 11/11 (art arda 4 çalıştırma).
  - GitHub Actions iş akışı yazıldı ama henüz GitHub'da çalıştırılmadı.
- **v0.10 (5 Ekim 2026): performans, ekran kartı, otomatik tasarım geçmişi.**
  - Kullanıcı "FreeCAD çok kasıyor" dedi. Ölçüm: makinede RX 7900 XTX var ama monitör (HDMI) Ryzen 7600X'in tümleşik
    GPU'suna bağlı; FreeCAD'in OpenGL'i de tümleşik GPU'da ("AMD Radeon(TM) Graphics"). Çözüm donanım: kabloyu ekran
    kartına takmak. Yazılım tarafı:
    - **Sahne farkı + önbellek + ikili aktarım:** 414 yüzlü plakada tek parça değişince FreeCAD'in arayüz iş parçacığı
      ~750 ms → 7 ms, aktarım 8,8 MB → 15 kB. Bulunan tuzak: `Part.getShape()` her çağrıda yeni kopya döndürür;
      önbellek anahtarı `obj.Shape`'ten.
    - **İstek üzerine çizim:** görünüm boştayken GPU'ya kare göndermiyor (eskiden saniyede 60+ kare), `powerPreference:
      'high-performance'`, piksel oranı en çok 2.
    - **Markadan bağımsız GPU tanılaması** (`gpu.js`, AMD/NVIDIA/Intel/Apple/Qualcomm/yazılım/sanal; Windows/Linux/
      macOS): boştaki ekran kartı, hibrit dizüstü, yazılımla çizim, eski sürücü, FreeCAD VBO/yazılımsal OpenGL. Açılışta
      bir kez uyarır; FreeCAD ayarları ve Windows GPU tercihi kullanıcı onayıyla uygulanır.
  - **Otomatik tasarım geçmişi (isteğe bağlı, kapalı başlar):** her onaylanan FreeCAD işlemi (yapay zekâ, VS Code ya da
    elle) bir git commit'i: okunur mesaj, `model.json`, FCStd kopyası (`saveCopy`), Obsidian notları (günlük, parça
    notları, ana sayfa, `[[bağlantılar]]`). Kendi deposu ya da geliştirici için proje deposu (yalnızca kendi klasörü,
    kancalar çalışır, push yok). Eski sürüm yeni belge olarak açılır. MCP araçları: `design_history`,
    `open_design_version`. Bulunan tuzak: commit iş parçacığı geç kalınca iki değişiklik tek commit'e karışıyordu;
    dosyaları artık commit'i atan iş parçacığı yazıyor.
  - Doğrulama: FreeCAD testleri 29/29 (sahne önbelleği/fark, GPU ayarları, tasarım geçmişi gerçek git ile), eklenti
    birim testleri 16/16 (GPU sınıflandırma 20 GPU adı), görünüm uçtan uca testi 14/14.
  - **Geçmiş ağacı (5 Ekim 2026):** kullanıcı geçmişin tek tuşla açılmasını ve ağaç olarak görünmesini istedi. CadAI
    kenar çubuğunda "Tasarım geçmişi" görünümü: gün → kayıt (en yenisi işaretli, saat, kaynak) → değişiklikler
    (eklendi/silindi/değişti; tıklanınca parça seçilir). Boşken "Geçmişi aç" tuşu (viewsWelcome), başlıkta aç/durdur,
    kayıtta "sürümü aç" ve "notu aç". Commit gövdesine `CadAI-Note:` eklendi; `history.log` değişiklikleri, kaynağı ve
    notu döndürür (`design_history` aracı da). Köprünün `/version` ucunda `hist` sayacı: yeni commit gelince ağaç
    kendiliğinden yenilenir. Doğrulama: FreeCAD 29/29, eklenti birim testleri 21/21, MCP 17/17.
  - **Her ekran kartıyla uyum (5 Ekim 2026):** 3B görünüm WebGL'i kademeli açar (kenar yumuşatma + güçlü GPU →
    yumuşatmasız → varsayılan GPU). WebGL 2 yoksa nedenini gösterir; boş panel kalmaz. Sürücü sıfırlanınca
    (`webglcontextlost`) uyarı verir, bağlam geri gelince tam sahneyi yeniden ister. Hareket sırasında ortanca kare süresi
    50 ms'yi aşarsa piksel oranı ×0,75 düşer (taban: yazılımla 0,5, donanımla 0,75); 22 ms'nin altında geri çıkar. Az
    önce yavaş kalan orana ancak art arda 3 akıcı ölçümden sonra döner, her denemede bekleme iki katına çıkar.
    Tanılamaya şunlar eklendi: sürücüsüz kart, sanal/uzak ekran bağdaştırıcıları (kablo denetimini bozmasın diye
    sayılmaz), ASPEED/Matrox, Linux'ta AMD APU kod adları, WebGL hatası, sürücü sıfırlama sayısı, OpenGL < 2. VBO
    yalnızca OpenGL 3+ destekleyen gerçek GPU'da önerilir ve uygulanır. Doğrulama: birim testleri 24/24, görünüm uçtan
    uca testi 18/18 (bağlam kaybı/geri gelme, yedek ayar, WebGL'siz sayfa, çözünürlük uyarlaması), FreeCAD 29/29.
- **v0.12 (5 Ekim 2026): teknik resim (A3, Türkçe antet).**
  - Kullanıcının kitindeki stil (`teknikresim şablon`: sheet.py, render.py, AL KOL örneği) birebir: A3 yatay, 10 mm
    çerçeve, SolidWorks benzeri Türkçe antet, kalın/ince çizgi, ondalık virgül, 45° tarama, NOTLAR, gölgeli izometrik.
    Kit geometriyi elle yazıyordu; CadAI ölçüleri modelden alır.
  - Çizim motoru `cadai/drawing.py`: `TechDraw.projectEx` ile gizli çizgili görünüşler (1. açı / ISO-E), ön görünüşte
    gizli çizgi varsa X ortasından Kesit A-A (kesit yüzü `Part.makeFace` + Bullseye, yüz yüz taranır), sığan en büyük
    standart ölçek, toplam boyutlar (yuvarlak parçada "Ø60,00"), daire görünen özelliklerin gruplanmış çapları
    ("4x Ø4,20"; kesişmeyen oklar için en çok 6! sıralama denenir), merkez çizgileri, ağırlık = hacim × yoğunluk.
    Ek ölçüler (`below/above/left/right/inside`, tolerans) ve not okları; yerleşim ek ölçülerin sayısına göre yer
    ayırır, izometrik resim yer kalmazsa küçülür. `check_layout` üst üste binen yazıları, çerçeve/antet/izometrik
    dışına taşmaları raporlar. matplotlib yalnızca `Agg` Figure ile (FreeCAD'in arka ucuna dokunmaz).
  - MCP aracı `technical_drawing`: PDF + 200 dpi PNG (.FCStd'nin yanına), rapor JSON'u ve sayfanın küçük resmi (ajan
    sonucu gözle denetler; uyarı varsa düzeltip yeniden çağırır). Malzeme verilmezse FreeCAD'deki malzemesi ve
    yoğunluğu kullanılır. VS Code: Kontrol → Dışa aktar → "Teknik resim (PDF)".
  - Bulunan tuzaklar: `str.upper()` Türkçe "i"yi "I" yapıyor ("ALÜMINYUM") → `upper_tr`; lanczos ara değerlemesi
    saydam kenarlı izometrik resimde koyu saçak çiziyordu; kısa dikey ölçünün yazısı yukarı taşınınca komşu görünüşe
    giriyordu (yazı artık ölçü çizgisinin yanında, yatay).
  - Doğrulama: AL KOL kitteki gibi 2:1, ~247 g (kit ~247 g), uyarı yok. FreeCAD testleri 30/30, eklenti birim testleri
    25/25 (Kontrol düğmeleri ↔ izin listesi ↔ kayıtlı komutlar denetimi eklendi), görünüm uçtan uca testi 18/18, MCP
    17/17.
- **v0.12.1 (5 Ekim 2026): hata ayıklayıcı yeniden bağlanabiliyor.** "Hata ayıklayıcıyı bağla" "Python Debugger
  eklentisi kurulu mu?" hatası veriyordu; eklenti kuruluydu. Neden: FreeCAD `debugpy.listen()` ile dinliyordu; bu, süreç
  başına bir kez çalışır. Adaptör kapanınca (VS Code'da "Durdur") ikinci çağrı `RuntimeError` veriyor, kod bunu "zaten
  dinliyor" sayıp yutuyordu; 5678'de kimse dinlemediği için VS Code bağlanamıyordu. Artık VS Code dinliyor (attach +
  `listen`, boş port) ve FreeCAD `debugpy.connect()` ile bağlanıyor: aynı FreeCAD'e art arda bağlanılabiliyor (daha önce
  `listen()` çağrılmış süreçte de). İkinci tuzak: pydevd, bir hata ayıklayıcı nesnesi varken gelen `connect()`'i
  sessizce yok sayıyor; başarısız bir bağlantı da yarım bir nesne bırakıyordu (FreeCAD yeniden başlatılana kadar bir daha
  bağlanılamazdı). Artık oturum açıksa "zaten bağlı" hatası, artık kalırsa `pydevd.stoptrace()` ile temizlik. Test:
  gerçek debugpy adaptörüyle başarısız deneme → bağlan → durdur → yeniden bağlan (`debugger_attaches_again_after_stop`).
- **v0.12.2 (5 Ekim 2026): FreeCAD rapor görünümünde köprü hataları bitti.** Rapor görünümü `ConnectionResetError`
  [WinError 10054] / `ConnectionAbortedError` [WinError 10053] izleriyle doluyordu (`bridge.py` `_send`). Neden: istemci
  (VS Code isteği zaman aşımına uğradı, pencere yeniden yüklendi, ajan aracı iptal etti) cevap yazılmadan bağlantıyı
  kapatıyor; `socketserver`'ın varsayılan `handle_error`'ı her birini stderr'e döküyordu. `/ui` ayrıca yazma hatasını
  yakalayıp aynı kopuk bağlantıya ikinci bir cevap yazmaya çalışıyor, iz iki katına çıkıyordu. Artık köprü
  `ConnectionError`'ları sessizce geçiyor, `/ui` cevabı tek yerden yazıyor. VS Code tarafında zaman aşımı artık "FreeCAD
  kapalı" sayılmıyor (yalnızca bağlantı reddi): uzun yeniden hesaplamada FreeCAD GIL'i tuttuğu için `/version` 3 sn'de
  cevap veremeyince uzantı kopup yeniden bağlanıyor, bu da tüm eşitlemeyi (ağaç, sahne, geçmiş) yeniden başlatıp yeni
  yarım kalan istekler doğuruyordu. Test: cevap gelmeden RST ile kapanan istemci, stderr'de iz yok
  (`bridge_quiet_when_client_hangs_up`).
- **v0.13 (6 Ekim 2026): render ve diğer açık kaynak CAD programları.** Kullanıcı FreeCAD dışındaki açık kaynak
  araçların da entegre edilmesini, render aracının eklenmesini ve her şeyin VS Code içinde kalmasını istedi.
  Değerlendirilenler: OpenSCAD (FreeCAD'den sonra en yaygın açık kaynak CAD, kodla modelleme → yapay zekâya en uygun),
  Blender (render/animasyon), build123d/CadQuery (Python kod CAD), KiCad (kullanıcıda kurulu, kart → kutu tasarımı)
  alındı. SolveSpace'in Windows sürümünde komut satırı aracı yok; LibreCAD/QCAD 2B (DXF yeterli); BRL-CAD niş;
  Salome ağır ve Linux ağırlıklı; HeeksCAD ~10 yıldır durgun; Onshape açık kaynak değil → alınmadı.
  - Mimari: programlar **ayrı süreçte, penceresiz** çalışır (`cadai/external.py`: bulma, kabuksuz ve zaman aşımlı
    çalıştırma); FreeCAD dosya alışverişi yapar, sonuç açık belgeye gelir. GPL ayrımı korunur, çökme FreeCAD'i
    düşürmez. Yollar yalnızca makine ayarı / ortam değişkeni / olağan klasörlerden; yapay zekâ program seçemez.
  - **`render`**: Blender Cycles (GPU: OptiX/CUDA/HIP/oneAPI/Metal; tümleşik GPU ayıklanır), B-rep yüz yüz
    üçgenleme (yumuşak gölgeleme yüz içinde, CAD kenarları keskin), Bevel düğümüyle hafif kenar yuvarlatma, Blender'ın
    stüdyo HDRI'si + anahtar ışık + yansıma yönünde softbox, gölge yakalayıcı + saydam film (zemin FreeCAD tarafında
    birleştirilir), köşelerin görüş alanına sığdığı en yakın kamera, 17 malzeme hazır ayarı (FreeCAD malzemesi ya da
    etiketten otomatik), dönen animasyon (GIF), arka plan işi + `render_status`. Blender yoksa yerleşik numpy render
    (ertelenmiş gölgeleme, gölge haritası, temas gölgesi, 2× süper örnekleme).
  - **`code_cad`**: OpenSCAD → `.csg` → FreeCAD `importCSG` (silindir gerçek silindir; minkowski/hull mesh'e düşer) ya
    da `mesh` kipi (manifold arka ucuyla STL); build123d/CadQuery → `codecad_runner.py` ayrı Python'da → STEP.
    Kaynak, parametreler ve dosya yolu nesnede saklanır; `replace` + `params` ile yeniden üretilir (konum korunur).
    Parametreler OpenSCAD'de `-D`, Python'da üst düzey atamaların AST ile değiştirilmesi (`a, b = 1, 2` dahil).
  - **`kicad_board`**: `kicad-cli pcb export step --subst-models --user-origin 0x0mm` + konum dosyası (CSV); kart
    merkezi XY=0, alt yüzü z=0; montaj delikleri, konnektörler ve bileşen konumları model koordinatlarında.
  - **VS Code**: Kontrol → "Render ve diğer programlar" (durum listesi, Render, Dosyadan aktar, Kurulum…), Dışa aktar →
    Render. Kurulum komutları eklentide sabit (winget/brew/apt), onaydan sonra görünür bir VS Code görevinde çalışır;
    bitince programlar yeniden aranır. Makine düzeyi yol ayarları `cadai.external.*`.
  - Bulunan tuzaklar: build123d ile CadQuery 2.8 aynı ortama kurulunca OCP bozuluyor (`OCP.OCP.collections`); Windows
    uzun yol sınırı derin klasörde pip kurulumunu bozuyor; Cycles alan ışığı gücü ışınım ≈ P / (π d²) ile ayarlanmazsa
    renkler soluyor; düz metal yüzler kameranın yansıma yönünde ışık yoksa kararıyor.
  - Bulunan ve düzeltilen eski test hatası: `bridge_quiet_when_client_hangs_up` istemci RST'si sunucu isteği okumadan
    gelirse düşüyordu (temiz HEAD'de 5'te 2); artık istemci, köprü isteği okuyunca kapatıyor (10/10).
  - Doğrulama (FreeCAD 1.1.4, Blender 5.2.2, OpenSCAD 2026.10.05, KiCad 9.0.7, build123d 0.13 / CadQuery 2.8,
    RX 7900 XTX): flanş hacmi analitik değerle %0,05 içinde (gerçek silindirler), parametreyle 6 → 4 delik yeniden
    üretim; build123d plaka hacmi analitikle tutuyor; KiCad StickHub 16,5 × 40 × 1,51 mm, J2 konnektörü kendi katısıyla
    aynı yerde; Blender HIP ile 320×240 taslak ~3 s, 1600×1200 ~11 s; turntable 12 kare ~21 s.
  - **Gerçek FreeCAD arayüzüyle uçtan uca** (yalıtılmış kullanıcı klasörü, eklentinin kendi `bridge.js` istemcisi,
    VS Code düğmelerinin çağrıları): 10/10. Blender render arka plan işindeyken FreeCAD'in `/version` cevabı en çok
    14 ms. Başsız testlerin göremediği iki hata bulundu ve düzeltildi: FreeCAD 1.1 arayüzünün varsayılan rengi
    mavimsi gri (0,678; 0,71; 0,741) → "renk seçilmemiş" sayılmıyor, parçalar gri plastik çıkıyordu; dosyadan gelen
    parçanın iç adı dosya adı değil dil adıydı. KiCad kartı (`… kart`) `pcb` malzemesi alır; `importCSG`'nin ply
    ayrıştırıcısı her içe aktarmada rapor görünümüne kırmızı "Token … defined, but not used" basıyordu → susturuldu.
  - **Kurulum düzeltmeleri:** winget'teki `OpenSCAD.OpenSCAD.Nightly` kaydı eskimiş (indirme 404) → kararlı
    `OpenSCAD.OpenSCAD`. build123d artık sistem Python'una değil CadAI'nin kendi sanal ortamına
    (`%LOCALAPPDATA%\CadAI\codecad`, `~/.cadai/codecad`) kurulur ve kendiliğinden bulunur; komutlar kullanıcının
    varsayılan terminalinden bağımsız olarak `cmd` / `sh` ile çalışır.
  - **FreeCAD panelinde Claude:** `anthropic` paketi FreeCAD'in Program Files altındaki Python'una yönetici izni olmadan
    kurulamıyordu. `cadai/pydeps.py` paketi `pip install --target` ile CadAI'nin kullanıcı klasörüne (`CadAI/pylib`)
    kurar ve `sys.path`'e ekler; panel eksik paketi görünce kurmayı önerir (komut ayrıntıda görünür). Kurulum ~20 s;
    SDK oradan yüklenip API'ye ulaşıyor (sahte anahtarla 401 → "anahtar geçersiz"). Tuzak: `anthropic` paketinde
    ~95 karakterlik dosya adları var; kurulum yolu 260 karakteri aşarsa (Windows uzun yol kapalı) içe aktarma düşer —
    bu durumda "kurulu değil" yerine gerçek hata gösterilir. Panel API anahtarıyla (Console) çalışır; abonelikle
    anahtarsız kullanım için VS Code'daki Claude Code (MCP) yolu.
- **v0.14 (6 Ekim 2026): küçük yerel modeller (7–14B).** Kullanıcı, parametresi az yerel modellerin araçları
  yanlış kullandığını ve 9B bir modelin bile CadAI'yi kullanabilmesi gerektiğini söyledi. Bulunan nedenler:
  - **Bağlam kesilmesi:** 28 aracın tanımı ~6.700, sistem istemi ~1.300 token. Ollama varsayılan bağlamı ekran kartı
    belleğine göre seçiyor (24 GB'ta 32768, küçük kartlarda 4096) ve OpenAI uyumlu `/v1` uç noktası `num_ctx`'i yok
    sayıyor (denendi: KvSize değişmedi); `/api/chat` `options.num_ctx`'i uyguluyor (KvSize 12288 oldu). → Ollama artık
    kendi API'siyle, profildeki `num_ctx` (varsayılan 16384) ile; bağlam dolunca kullanıcıya uyarı.
  - **Katı argümanlar:** `obj`, `"8 mm"`, `"40,30,10"`, `face1`, `{"arguments": {...}}` doğrudan `TypeError` oluyordu. →
    `tools/args.py` çağrıyı şemaya uydurur; uyduramazsa hata doğru bir örnek çağrı gösterir. Uydurma araç adları
    (`create_box`, `drill_hole`) `resolve_name` ile çözülür. Onay pencereleri (panel + köprü) çözülmüş ad ve
    argümanları gösterir; aksi hâlde `script` adıyla gelen kod ya da takma adlı değiştirici araç onaysız geçerdi.
  - **Python zorunluluğu:** basit işler için bile FreeCAD API'si gerekiyordu. → kodsuz araçlar `add_box`, `add_cylinder`,
    `make_hole` (yüzeydeki noktadan, yön yüzden; 1 mm'den uzak nokta reddedilir), `fillet_edges` / `chamfer_edges`
    (kenar adı ya da `all/top/bottom/vertical/circular`; silindir dikişleri hariç), `boolean`, `move_object`. Nesne adı
    hep en son hâlini gösterir: "Plate"e ikinci delik ilkini kaybetmez; adlar kısa kalır (`Plate_Hole001`).
  - **Metin içi araç çağrıları ve düşünce sızıntısı:** `<tool_call>`, ```json, `[TOOL_CALLS]`, `<|python_tag|>`,
    `<function=...>` biçimleri araç çağrısına çevrilir, `<think>` atılır.
  - **Küçük model modu** (`small.py`, `prompts.SMALL`): ~15 çekirdek araç, tek cümlelik açıklama, kırpılmış şema, örnekli
    kısa istem; FEM/teknik resim/render/parça grupları kullanıcının sözcükleriyle eklenir ("yükseklik" yük sayılmaz).
    Profilde otomatik (yerel sunucu + ≤14B ya da boyutu bilinmeyen model). Ajan aynı hatalı çağrının 3. tekrarında ya da
    6 adım üst üste hatada durur; küçük modda eski araç sonuçları kısaltılır. MCP: `CADAI_TOOLSET=small[+grup]`, VS Code
    ayarı `cadai.mcp.toolset` (Claude Code, Codex, Cline, Kilo, Roo, Copilot yapılandırmalarına `env` olarak yazılır).
  - Doğrulama: FreeCAD 41/41 (yeni: kodsuz modelleme, metinle araç çağıran "küçük model" turu), FreeCAD'siz 22 birim testi
    (`test_small_models.py`), VS Code 26/26. Gerçek model ölçümü `tests/eval_local_model.py` (Türkçe istekler, sonuç
    geometriden denetlenir). Qwen3.6 35B-A3B ile küçük model modunda ilk 3 senaryo geçti (inceleme; ortaya Ø8 delik,
    hacim analitik değerle aynı; kalınlık 15 mm); ölçüm kullanıcının isteğiyle yarıda bırakıldı. 9B sınıfında deneme
    kullanıcıda.
- **v0.14.2 (7 Ekim 2026): GPU uyumluluğu.**
  - Görünüm GPU markası yerine WebGL 2 yetenekleriyle çalışır. Son bağlam denemesi güç tercihini tamamen atlar;
    eski/sanal sürücünün renderbuffer, texture ve viewport sınırları büyük/HiDPI pencerelerde çözünürlüğü sınırlar.
  - Çerçeve belleği için piksel bütçesi; pencere yeniden boyutlanınca limitleri yeniden hesaplama; bağlam kaybında
    çizimi durdurma, geri gelince GPU bilgisini güncelleme. Tarayıcı GPU adını gizlese de çizim ve seçim çalışır.
  - Tanılama, monitörün hangi GPU'ya bağlı olduğundan kartın boşta olduğunu varsaymaz. OpenGL sürümü bilinmiyorsa
    VBO açmayı önermez; Intel Xe MAX ve AMD 8060S sınıflandırması düzeltildi.
  - Birim testinin gerçek Codex ayarlarına yazma girişimi geçici kullanıcı klasörüyle giderildi.
  - WebGL 1-only kartlarda three.js r186 nedeniyle 3B görünüm desteklenmez; açıklama ve tanılama sunulur.
  - Doğrulama: FreeCAD paketi 42/42 (kurulu olmayan dış programlar ve çevrimdışı parça kataloğu atlandı), Python
    birim testleri 39/39, VS Code birim testleri 27/27, başsız Edge/SwiftShader görünüm testleri 21/21; JavaScript
    sözdizimi ve git diff --check temiz. Ruff ortamda kurulu olmadığından çalıştırılamadı. Fiziksel GPU matrisi denenmedi.
- **v0.15.0 (7 Ekim 2026): kalıcı tasarım şartları.**
  - `set_design_requirements` / `check_design_requirements`: boyut, hacim, katı sayısı, minimum mesafe ve aynı birimli
    ölçü ilişkileri `.FCStd` içinde saklanır. Kimlikle güncelleme diğer şartları korur; silme açık kimlik listesiyle yapılır.
  - Başarılı mutating araç çağrıları ortak Registry üzerinden gerçek geometriyi kontrol eder; işlem başarısı ve
    tasarım uygunluğu ayrı raporlanır. Eksik/geçersiz/eski geometri, bozuk veri ve dallanmış geçmiş uygun sayılmaz.
  - Ana/küçük model istemleri, küçük araç grubu ve belge özeti yeni denetimleri kullanır.
  - Bu adım ihlal tespitidir; parametrik kısıt çözümü veya otomatik FEM optimizasyonu değildir.
    [Kullanım, ölçüm anlamları ve kapsam sınırları](docs/design-requirements.md).
  - Doğrulama: FreeCAD paketi 49/49 (OpenSCAD, harici build123d/CadQuery ortamı ve Blender kurulu olmadığından
    bu entegrasyonlar atlandı); Python birim testleri 41/41, VS Code birim testleri 27/27; Ruff temiz.
    Yedi yeni gerçek geometri senaryosu ve iki Registry testi eklendi. Gerçek LLM/rakip karşılaştırması yapılmadı.
- **v0.16.0 (7 Ekim 2026): şartların arayüzü ve parametrik düzenleme.**
  - Kontrol panelinde şart ekleme/düzenleme/silme, belge değişiminde yeniden denetim, parça seçimi ve JSON raporu.
  - `add_mounting_plate`: dört köşe deliğinin kenar uzaklığı ve kesme derinliğini yerleşik bağıntılarla korur.
    `set_parameter_relation`: sayısal parametreleri çarpan/farkla bağlar; döngü/hesaplama hatasında geri alır.
  - `inspect_holes`, delik sayısı/çapı/merkez–sınır uzaklığı ve `interference_volume` gerçek geometriden ölçülür.
    Delik kapsamı tam silindirik yüzler, kenar uzaklığı dünya eksenlerinde sınırlayıcı kutudur.
  - Dahili ajan son yanıtından önce denetler; en çok iki düzeltme turu. Bu turlarda şart hedeflerini değiştiremez.
    Harici MCP ajanlarının son yanıtı bu döngü tarafından yönetilmez. `export_design_report` tam JSON kanıtı üretir.
  - Doğrulama: FreeCAD 54/54 (kurulu olmayan dış CAD/render programları atlandı), VS Code 27/27, başsız tarayıcı
    25/25. Rakiple veya gerçek LLM ile başarı oranı karşılaştırması yapılmadı.
- **Sıradakiler:**
  - GitHub'a yükleme (kullanıcı yapacak), ardından Marketplace ve Open VSX. İlk CI çalıştırmasını izlemek.
  - SolveSpace (Linux'ta `solvespace-cli`), DXF çıktısı (LibreCAD/QCAD için).
  - Render: FEM renk haritasını ve montaj animasyonunu Blender'da çizmek; MP4 çıktısı.
  - İngilizce arayüz; macOS ve Linux'ta gerçek deneme.
  - Faz 0'ın 3–5. görevlerini gerçek modellerle (yerel ve bulut) denemek.
  - Teknik resim: birden çok sayfa, montaj resmi ve parça listesi; DXF çıktısı.
  - İşaretlere renk seçimi ve sesli not.
  - Yanıtları akış (streaming) halinde göstermek.
  - Assembly ile hareket animasyonu.

## 13. Kaynaklar

**Çizim ve görüntüleme**
- build123d-mcp: https://github.com/pzfreo/build123d-mcp
- freecad-mcp: https://github.com/neka-nat/freecad-mcp
- OCP CAD Viewer: https://github.com/bernhard-42/vscode-ocp-cad-viewer
- three-cad-viewer: https://github.com/bernhard-42/three-cad-viewer
- PythonSCAD: https://www.pythonscad.org/
- Zoo Design Studio: https://zoo.dev/blog/zoo-design-studio-v1

**Analiz ve hareket**
- Casys-AI: https://github.com/Casys-AI
- mcp-calculix: https://github.com/Casys-AI/mcp-calculix
- CAE-Agent-Hub: https://github.com/Cai-aa/CAE-Agent-Hub
- openPASO: https://github.com/Hereon-InstituteMS/openPASO
- Foam-Agent: https://github.com/csml-rpi/Foam-Agent

**Ticari araç köprüleri**
- MATLAB MCP Core Server: https://github.com/matlab/matlab-mcp-core-server
- PyMAPDL-MCP: https://mapdl-mcp.docs.pyansys.com/
- PyMechanical-MCP: https://mechanical-mcp.docs.pyansys.com/
- PyFluent-MCP: https://github.com/ansys/pyfluent-mcp
- SolidWorks-MCP: https://github.com/hjbaard/SolidWorks-MCP

**Yapay zekâ ajanı ve ölçüm**
- Cline: https://github.com/cline/cline
- Cline SDK (ClineCore): https://docs.cline.bot/sdk/clinecore
- Cline ile yerel modeller: https://docs.cline.bot/running-models-locally/overview
- CADGenBench: https://github.com/huggingface/cadgenbench
- CADGenBench sonuç tablosu: https://benchmarklist.com/benchmarks/cadgenbench/


## v0.16.1 — Uzun model bekleme süresi (7 Ekim 2026)

- Ollama, OpenAI uyumlu sunucular ve Anthropic: varsayılan model isteği ağ zaman aşımı 7200 saniye (2 saat); mevcut profiller de bu varsayılanı kullanır.
- FreeCAD CadAI Ayarları → Model zaman aşımı: profil başına 1–1440 dakika; config.json profillerinde `timeout_seconds`. Çağıranın açık timeout değeri önceliklidir.
- Yanıt token sınırı ile ağ zaman aşımı ayrı açıklanır; Ollama uzunluk sınırını bağlam doluluğu gibi göstermeyi bıraktı. Token/bağlam kapasitesi bu değişiklikle kendiliğinden artırılmaz.
- Süre ağ işlemlerinin bekleme sınırıdır; toplam sohbet süresi veya sunucunun kendi süre sınırını değiştirmez.
- Doğrulama: FreeCAD değişiklik öncesi/sonrası 54/54; Python MCP + küçük model testleri 48/48 (5 yeni süre/uyarı testi); VS Code 27/27; görünüm 25/25; Ruff ve diff biçim denetimi başarılı.
