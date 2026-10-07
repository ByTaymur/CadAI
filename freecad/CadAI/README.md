# CadAI — FreeCAD için yapay zekâ asistanı (v0.2, deneysel)

FreeCAD içinde çalışan bir sohbet paneli. Modeli hem fareyle hem de yazarak düzenlersiniz. Asistan, seçtiğiniz yüzleri görür; modeli değiştirebilir, ölçebilir ve CalculiX ile sonlu eleman analizi (FEM) yapabilir. Yerel modellerle (Ollama, LM Studio) ya da bulut modelleriyle (Claude, OpenAI uyumlu servisler) çalışır.

Aynı araçlar **VS Code'dan da** kullanılabilir. FreeCAD görüntü ve elle müdahale için açık durur; VS Code'daki ajan (Cline, Kilo Code, Claude Code, Copilot) açık FreeCAD belgesi üzerinde çalışır.

## VS Code + FreeCAD birlikte

```
VS Code (Cline / Kilo / Claude Code / Copilot)
   └─ MCP: mcp_server/cadai_mcp.py ──HTTP 127.0.0.1 + anahtar──► FreeCAD içindeki CadAI köprüsü
                                                                 └─ aynı araçlar, açık belge, GUI iş parçacığı
```

1. **FreeCAD'i açın.** Köprü kendiliğinden başlar. Panelde "● VS Code köprüsü" yazısı görünür; FreeCAD konsolunda da adres yazar.
2. **VS Code'da ajanı açın.** `cadai-freecad` MCP sunucusu şu dosyalarda tanımlı:
   - Cline: `cline_mcp_settings.json`
   - Kilo Code: `mcp_settings.json`
   - Claude Code: proje kökündeki `.mcp.json`
   - Copilot: `.vscode/mcp.json`

   Yalnızca okuyan araçlar Cline'da onaysız çalışır; modeli değiştirenler için Cline onay ister.
3. **FreeCAD'de bir yüz seçin, VS Code'da yazın.** Örnek: "bu yüze 500 N uygula ve analiz et". Değişiklikler anında FreeCAD'de görünür. Ajanın her çağrısı FreeCAD'deki panelde mor renkle listelenir.
4. **Uzun analizler:** `fem_run(background=true)` hemen bir iş numarası döner ve `fem_status` ile sonuç sorulur. Analiz sürerken FreeCAD donmaz.

Ayarlar `%APPDATA%\FreeCAD\v1-1\CadAI\config.json` dosyasında:

| Ayar | Varsayılan | Ne yapar |
|---|---|---|
| `bridge_autostart` | `true` | FreeCAD açılınca köprüyü başlatır |
| `bridge_port` | `47800` | Port; doluysa sonraki boş port denenir |
| `bridge_approval` | `false` | `true` olursa VS Code'dan gelen her değişiklik için FreeCAD'de de onay sorulur |

Köprü yalnızca `127.0.0.1` adresini dinler ve her istekte rastgele bir anahtar ister. Anahtar `CadAI\bridge.json` dosyasında durur.

### Geliştirme (VS Code)
- **Testler:** Ctrl+Shift+P → "Run Test Task" → *CadAI: testleri çalıştır*.
- **Yeniden yükleme:** Kodu değiştirdikten sonra FreeCAD'de **CadAI → Geliştirici → Eklentiyi yeniden yükle**. FreeCAD'i kapatmak gerekmez.
- **Hata ayıklama:**
  1. Bir kez kurulum: `"C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install --user debugpy`
  2. FreeCAD'de **CadAI → Geliştirici → VS Code hata ayıklayıcısını bekle**.
  3. VS Code'da "FreeCAD'e bağlan (CadAI)" çalıştırma yapılandırmasını başlatın.
- **Ajan kuralları:** Proje kökündeki `.clinerules/cadai.md`.

## Kurulum

1. FreeCAD 1.0 veya üstü gerekir (FreeCAD 1.1.1 ile test edildi). CalculiX ve Gmsh, FreeCAD'in Windows paketinde zaten var.
2. **En kolayı:** VS Code'a CadAI eklentisini kurun; bu klasörü FreeCAD'e kendisi kurar ve günceller.
   **Elle kurulum:** bu klasörü FreeCAD'in kullanıcı eklenti klasörüne (`Mod`) kopyalayın ya da bağlayın. Klasörün yerini FreeCAD'in Python konsolunda `App.getUserAppDataDir()` gösterir; örneğin Windows'ta:
   ```powershell
   New-Item -ItemType Junction -Path "$env:APPDATA\FreeCAD\v1-1\Mod\CadAI" -Target "<depo>\freecad\CadAI"
   ```
3. FreeCAD'i yeniden başlatın, tezgâh listesinden **CadAI**'yi seçin ve **Asistan paneli** düğmesine basın (kısayol: Ctrl+Shift+A).

### Model bağlantısı
Panelin sağ üstündeki ⚙ düğmesinden profilleri düzenleyin.

| Profil | Ne gerekir |
|---|---|
| Ollama (yerel) | Ollama çalışıyor olmalı (`http://localhost:11434/v1`) ve araç çağırmayı destekleyen bir model indirilmiş olmalı (ör. Qwen3). CadAI, Ollama'ya kendi API'siyle bağlanır ve bağlam penceresini profildeki "Bağlam (num_ctx)" değeriyle ayarlar (varsayılan 16384); ayrıca `OLLAMA_CONTEXT_LENGTH` gerekmez. |
| LM Studio (yerel) | LM Studio'da sunucu açık olmalı (`http://localhost:1234/v1`) ve "model" alanına yüklü modelin adı yazılmalı. |
| Claude | FreeCAD'in Python'una SDK kurulmalı: `"C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install anthropic`. API anahtarını `ANTHROPIC_API_KEY` ortam değişkenine koyun. Varsayılan model: `claude-opus-5-5`. |
| OpenAI uyumlu | Herhangi bir `/chat/completions` uç noktası (OpenRouter, vLLM, llama.cpp sunucusu…). |

## Kullanım

- **Uygula (Act) modu:** Asistan modeli değiştirebilir. Her değişiklikten önce onay ister (kodu "Ayrıntılar" bölümünde görürsünüz). Her değişiklik tek bir geri alma (Ctrl+Z) adımıdır; hata çıkarsa otomatik olarak geri alınır.
- **Planla (Plan) modu:** Asistan modeli yalnızca inceler ve yapacaklarını madde madde yazar.
- **Seçimi ekle:** 3B görünümde seçtiğiniz yüz/kenarın bilgisi (tür, alan, normal, yarıçap) mesaja eklenir. Böylece "bu yüze 500 N uygula" demeniz yeterli olur.
- **Görüntü ekle:** 3B görünümün ekran görüntüsünü gönderir. Yalnızca görüntü okuyabilen (vision) modellerde işe yarar.

Örnek komutlar:
- "100×20×10 mm çelik bir kiriş çiz. Sol ucu sabit olsun, sağ uca 500 N aşağı kuvvet uygula, analiz et ve el hesabıyla karşılaştır."
- (Yüz seçiliyken) "Bu deliğin çapını 10 mm yap."
- "Parçanın ilk 3 doğal frekansını bul."

## Asistanın araçları

| Araç | Ne yapar | Modeli değiştirir mi? |
|---|---|---|
| `get_document_summary` | Nesneler, ölçüler, ifadeler, sınır kutusu, hacim | Hayır |
| `get_selection` | Seçili yüz/kenarların geometrisi | Hayır |
| `find_faces`, `list_edges` | Yüz/kenarı ölçüte göre bulur (ör. "+X ucundaki yüz", "R4 delikler") | Hayır |
| `measure` | Hacim, alan, kütle, iki referans arası mesafe | Hayır |
| `capture_view` | 3B görünümün ekran görüntüsü | Hayır |
| `beam_hand_calc` | Kiriş el hesabı (sehim, gerilme, ilk doğal frekans) | Hayır |
| `set_property` | Var olan bir ölçüyü değiştirir (parametrik düzenleme) | Evet |
| `run_python` | FreeCAD Python kodu çalıştırır (yeni geometri) | Evet |
| `fem_setup`, `fem_run` | Malzeme, mesnet, kuvvet/basınç, Gmsh mesh; CalculiX ile statik ya da modal çözüm (istenirse arka planda) | Evet |
| `fem_status` | Arka plandaki analizin durumu ya da sonucu | Hayır |
| `export_model` | STEP, IGES, STL, OBJ, 3MF dışa aktarımı | Evet (dosya yazar) |

## Testler

```powershell
& "C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" tests\run_tests.py
```

Testler gerçek FreeCAD, Gmsh ve CalculiX ile çalışır; yapay zekâ modelinin yerine senaryolu sahte bir model kullanılır. Referans sonuçlar (100×20×10 mm çelik konsol kiriş, uçta 500 N):

| | CadAI + CalculiX | El hesabı |
|---|---|---|
| Uç sehim | 0,4745 mm | 0,4762 mm |
| En büyük von Mises gerilmesi | 149,4 MPa | 150 MPa |
| İlk doğal frekans | 836,7 Hz | 835,5 Hz |

## Bilinen sınırlar

- `fem_run` varsayılan olarak çözüm bitene kadar bekler ve bu sırada FreeCAD donar. Bunu önlemek için `background=true` kullanın. Mesh oluşturma her durumda kısa süre arayüzü bekletir.
- Hata ayıklayıcıyla bağlanma özelliği (debugpy) yazıldı ama henüz denenmedi.
- Yanıtlar akış (streaming) halinde değil, tamamı gelince görünür.
- `run_python` keyfi kod çalıştırır; bu yüzden onay penceresi varsayılan olarak açıktır.
- Model ne kadar iyiyse sonuç o kadar iyidir. Küçük yerel modeller basit düzenlemelerde iyi iş çıkarır, karmaşık parçalarda zorlanır.
- **Küçük model modu** (Ayarlar → profil; varsayılan "Otomatik": yerel sunucu ve 14B'ye kadar model): kısa bir sistem istemi ve
  yaklaşık 15 araç gönderilir; FEM, teknik resim, render gibi araçlar yalnızca isteğinizde konuları geçince eklenir. Kutu,
  silindir, delik, yuvarlatma, pah, birleştirme ve taşıma Python yazmadan yapılır. Model argümanları biraz yanlış yazsa
  da (`obj`, `"8 mm"`, `"40,30,10"`) çağrı düzeltilir; düzeltilemezse hata mesajı doğru bir örnek gösterir. VS Code'daki
  ajanlar (Cline, Continue…) yerel modelle kullanılıyorsa `cadai.mcp.toolset` ayarını `small` yapın.
- Analiz sonuçları mühendislik muhakemesinin yerini tutmaz. Sınır koşullarını, birimleri ve mesh yakınsamasını kontrol edin.

## Yapı

```
CadAI/
├─ InitGui.py, Init.py, package.xml   FreeCAD eklenti girişleri
├─ cadai/
│  ├─ agent.py        model ↔ araç döngüsü (Plan/Act)
│  ├─ providers.py    OpenAI uyumlu (urllib) ve Anthropic (SDK) sağlayıcıları
│  ├─ prompts.py      sistem istemi
│  ├─ config.py       profiller (%APPDATA%\FreeCAD\v1-1\CadAI\config.json)
│  ├─ bridge.py       VS Code köprüsü (yerel HTTP, GUI iş parçacığına yönlendirme)
│  ├─ tools/          inceleme, modelleme ve FEM araçları
│  └─ gui/            sohbet paneli, ayarlar, komutlar
├─ mcp_server/cadai_mcp.py   VS Code'un başlattığı MCP sunucusu (yalnızca standart kütüphane)
└─ tests/run_tests.py
```

Lisans: LGPL-2.1-or-later (FreeCAD ile aynı).
