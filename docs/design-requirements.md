# Tasarım şartları ve parametrik düzenleme — v0.16.0

CadAI, kullanıcının istediği ölçüleri ve ölçüler arasındaki ilişkileri `.FCStd` içinde saklar.
Bir araç çağrısının başarılı olması, parçanın bütün şartlara uyduğu anlamına gelmez:
modeli/dosyaları değiştiren başarılı CadAI ajanı ve MCP araç çağrıları `design_validation` raporu döndürür.
Şartlar yoksa mevcut araç çıktıları değişmez.

VS Code **Kontrol → Tasarım şartları** bölümünde şart ekleyebilir, düzenleyebilir, silebilir, ilgili parçayı
seçebilir ve JSON raporu kaydedebilirsiniz. Panel belge değişikliklerinde yeniden denetler. Ölçülen ve beklenen
değerler, tolerans ve geçemeyen şartlar birlikte gösterilir. Bir belgeyi kapatıp başka belgeye geçtiğinizde
eski sonuçlar temizlenir. **Ölçüleri bağla** ile iki sayısal boyutu çarpan ve fark kullanarak bağlayabilirsiniz.

## Kullanım

Ajana örneğin şunu söyleyin:

> Plakanın kalınlığı 8 mm olsun; bunu tasarım şartı olarak kaydet. Kapak ile plaka arasında
> en az 2 mm boşluk kalsın. Sonraki değişikliklerde bu şartları kontrol et.

Ajan önce nesneleri inceleyip oluşturur, ardından yalnızca sizin istediğiniz hedefleri kaydeder.
Toleransları belirtmelidir. Gereksinimin bozulması bir uyarı ve ölçüm üretir. Denetleyici geometriyi değiştirmez.
Parametre bağıntıları FreeCAD tarafından yeniden hesaplanır. FreeCAD içindeki ajan, değişikliklerden sonra son
yanıtını vermeden denetler; başarısızsa en fazla iki düzeltme turu ister. Hâlâ başarısızsa başarı yanıtını yayınlamaz.
Bu düzeltme turlarında `set_design_requirements` ile hedef değiştirmek engellenir. Harici MCP ajanlarının son yanıtı
CadAI tarafından yönetilmez; aynı ölçüm raporu ve çalışma talimatları onlara da verilir.

`set_design_requirements` kimliğe göre ekler/günceller; çağrıda bulunmayan şartları korur:

```json
{
  "requirements": [
    {"id": "thickness", "measure": {"object": "Plate", "metric": "bbox_z"},
     "value": 8, "tolerance": 0.01},
    {"id": "gap", "measure": {"object": "Plate", "metric": "min_distance", "other": "Lid"},
     "operator": "min", "value": 2, "tolerance": 0.01},
    {"id": "matching_width", "measure": {"object": "Lid", "metric": "bbox_x"},
     "reference": {"object": "Plate", "metric": "bbox_x"}, "value": 0, "tolerance": 0.01}
  ]
}
```

İlişki hedefi `factor × reference ölçümü + value` olarak hesaplanır; varsayılan `factor=1`.
İki ölçüm aynı birimde olmalıdır. `operator`: `eq` eşitlik, `min` alt sınır, `max` üst sınırdır.
Tolerans, eşitlikte mutlak fark; sınırlarda izin verilen sapmadır. Varsayılan 0,01, katı sayısında 0'dır.

Kullanıcı kalınlık hedefini 12 mm olarak değiştirirse aynı `thickness` kimliği güncellenir.
Başarısız bir denetimi gizlemek amacıyla hedef veya tolerans değiştirilmez.
Silme açıktır: `{"requirements": [], "remove_ids": ["thickness"]}`. Her belge en fazla 20 şart saklar.

`check_design_requirements {}` salt okunur rapor verir; belge özeti de kayıtlı şartların güncel sonucunu içerir.
`include_definitions=true` kayıtlı kuralları da verir. Büyük araç yanıtlarında `omitted_ids` ayrıntısı kısaltılan
şartları belirtir; `ids` ile küçük bir alt kümenin ayrıntısı alınabilir. Genel sonuç bütün şartlardan hesaplanır;
`ids` ile istenen alt küme `partial=true` taşır ve tek başına tasarımın tamamını doğrulamaz.
`pass`: tüm kayıtlı şartlar geçti; `fail`: en az biri sağlanmadı/ölçülemedi;
`error`: kayıtlı şart verisi okunamadı; `not_configured`: kayıtlı şart yok, uygunluk doğrulanmış sayılmaz.
Her kontrol ölçülen değeri, bekleneni, toleransı, birimi ve kullanılan sonuç nesnelerini bildirir.
Ölçülemeyen kontrolün durumu `error` olur; eski/geçersiz geometri başarılı sayılmaz.

## Kapsam ve sınırlar

| Ölçüm | Anlamı |
|---|---|
| `bbox_x`, `bbox_y`, `bbox_z` | Dünya eksenleri boyunca sınırlayıcı kutu boyutları, mm; dönmüş parçanın yerel kalınlığı değildir |
| `volume` | Katı geometrinin hacmi, mm³ |
| `solid_count` | Katı sayısı |
| `min_distance` | İki şekil arasındaki en kısa mesafe, mm; çakışma/hacim girişimi kontrolü değildir |
| `interference_volume` | İki katının ortak hacmi, mm³; çakışmama için `max=0`, uygun hacim toleransıyla |
| `hole_count` | `axis` dünya eksenine paralel, kapalı silindirik iç yüzlerden tanınan delik sayısı |
| `hole_diameter` | Tanınan tüm delik bölümlerinin çapları; her biri şartı sağlamalı |
| `hole_edge_offset` | Her delik merkezinden sınırlayıcı kutunun en yakın yanına `edge_axis` yönündeki uzaklık |

`inspect_holes` merkezleri, çapları, eksen boyunca aralıkları ve yüz adlarını verir. `axis` varsayılanı `z`.
`edge_axis` delik ekseninden farklı olmalıdır. Çap/kenar uzaklığı şartını delik sayısıyla birlikte kullanın:
bir deliğin silinmesi, yalnızca kalan deliklerin çapına bakarak anlaşılamaz.

- Delik/fillet/boolean işlemlerinde kayıtlı nesnenin devamındaki sonucu izler. Geçmiş dallanıyorsa tahmin
  yerine hata verir; hedef açık sonuç nesnesine bağlanmalıdır. Nesne silinirse aynı etiketli başka nesneye bağlanmaz.
- Kayıtlı veri yalnızca izinli sayısal ölçümleri tanımlar; Python veya FreeCAD ifadesi çalıştırmaz.
- Kaydetme, tekrar açma ve geri alma desteklenir. VS Code Kontrol paneli belge yenilendikçe tekrar denetler;
  panel kapalıyken veya yalnızca FreeCAD kullanırken `check_design_requirements` ile denetleyin.
- Delik tanıma yalnızca tam silindirik yüzleri kapsar. Açık kanallar, konik ve bölünmüş silindirik yüzler bu kapsamda
  değildir; aynı eksendeki bitişik kademeler birlikte sayılır. Kenar uzaklığı gerçek bir eğrisel dış kontura değil,
  dünya eksenlerindeki sınırlayıcı kutuya göredir. Döndürülmüş delikler için uygun dünya ekseni seçilmelidir.
- Genel montaj kısıt çözümü ve FEM hedef optimizasyonu bu sürümün kapsamı dışındadır.
- Bu sürüm rakip karşılaştırma sonucu içermez. text-to-cad karşılaştırması için sonraki adım: aynı modelle,
  aynı parça ve ardışık düzenleme görevlerinde geometri doğruluğu, şart ihlalleri ve düzeltme sayısını ölçmek.

## Parametrik plaka ve ölçü ilişkileri

`add_mounting_plate` ile örnek:

```json
{"length":80,"width":60,"thickness":8,"hole_diameter":6,"edge_offset_x":10,"edge_offset_y":10,"name":"Plate"}
```

Araç dört köşe deliğini gerçek FreeCAD Box/Cylinder/Cut özellikleriyle üretir. Dönen `parameters_object` üzerinde
`Length`, `Width`, `Height`, `HoleDiameter`, `EdgeOffsetX` ve `EdgeOffsetY` değiştirilebilir. Deliklerin konumu ve
boydan boya kesme derinliği bağıntıyla güncellenir; dosyayı yeniden açınca özel Python proxy'si gerekmez.
Şablon XY düzlemindedir; döndürmek için parametre nesnesi yerine sonuç nesnesini kullanın.
Çok büyük çap veya çok küçük plaka gibi sonraki uygunsuz değişiklikler için tasarım şartlarını ayrıca kaydedin.

`set_parameter_relation` hedefi `factor × kaynak + offset` olarak bağlar. Kaynak ve hedef aynı birimde sayısal
özellikler olmalı; `Placement.Base.x/y/z` de desteklenir. Döngülü/hesaplanamayan bağıntı geri alınır.
`remove=true` bağıntıyı kaldırır ve mevcut değeri korur. Kullanıcının keyfi kodu veya ifadesi çalıştırılmaz.
FreeCAD'in yerleşik [ifade sistemi](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Expressions.md) kullanılır.

`export_design_report` `.json` dosyasına tam ölçüm raporunu, kuralları, UTC zamanını ve model dosyası yolunu yazar.
Başarısız denetim de raporlanır; rapor kaydetmek tasarımın uygun olduğu anlamına gelmez.

## Doğrulama

`tests/test_design_requirements.py`, ana `tests/run_tests.py` tarafından gerçek FreeCAD geometrisiyle çalıştırılır:
delik açılması sonrası hacim/ölçü, ilişki ve boşluk ihlali, tolerans, kayıt/açma/geri alma,
silinen veya güncellenmemiş geometri, belirsiz sonuçlar, hatalı veri ve belgeler arası ayrım; ardışık parametrik plaka
düzenlemeleri, yeniden açma, bağıntı döngüsü/geri alma, delik tanıma, çakışma hacmi ve rapor çıktısı.
Başsız tarayıcı testleri gerçek FreeCAD verisiyle paneli, düzenleme mesajlarını, belge değiştirmeyi ve HTML kaçışını denetler.
