# Facebook Group Scraper + NotebookLM

## Çalıştırma

Windows'ta `run.bat` dosyasını başlatın. Gerekli paketler için:

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
```

Web arayüzü `http://127.0.0.1:5000` adresinde açılır.

## NotebookLM

Ana sayfadaki **NotebookLM Upload** ve **NotebookLM Chat** bağlantıları iki NotebookLM ekranını açar. İlk kullanımda giriş düğmesine bastıktan sonra ayrı konsolda açılan NotebookLM CLI komutunu takip edin. Tarayıcıda Google girişi tamamlandıktan sonra oturumu kaydetmek için bu konsola dönüp Enter'a basın.

Upload ekranı NotebookLM defteri oluşturmayı, kaynakları listeleyip silmeyi ve dosya, URL veya metin kaynağı eklemeyi destekler. Yüklenen dosyalar NotebookLM'e aktarım sırasında geçici olarak saklanır. Chat ekranında bir defter seçip kaynaklarına soru sorabilirsiniz.

## Facebook veri çekme

Mevcut grup URL'si ve yorum toplama akışları aynı scraper modüllerini kullanmaya devam eder. NotebookLM ekranları bu veri çekme mekanizmasını değiştirmez ve scraper JSON raporlarını otomatik olarak NotebookLM'e yüklemez.

`data/` içindeki mevcut ve yeni `.json` sonuçları, uygulama açılırken ve sonuç listesi yenilenirken `data/txt/` altında aynı dosya adlarıyla `.txt` kopyalarına dönüştürülür. JSON içeriği ayrıştırılıp yeniden biçimlendirilmez; kopya kaynak dosyanın içeriğini aynen korur ve orijinal JSON dosyası yerinde kalır.

NotebookLM upload sayfası `data/txt/` dosyalarını listeler. TXT dosyaları seçili deftere tek tek ya da topluca yüklenebilir; defterde adı zaten bulunan dosyalar atlanır, yükleme hataları ayrı gösterilir.
