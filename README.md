# OBS Ders Kayıt Snippet Hazırlayıcı

`bot2.py`, ChromeDriver veya Selenium kullanmadan normal tarayıcıdaki açık OBS
oturumunda çalışacak tek-atış JavaScript Snippet'i üretir. Kullanıcı adı ve şifre
uygulamaya verilmez.

## Kullanım

1. `python bot2.py` ile uygulamayı açın.
2. CRN, hedef tarih ve hedef saati girin. İlk denemede **Kuru prova** açık kalsın.
3. **OBS'yi aç** ile normal şekilde giriş yapın.
4. **Snippet'i hazırla ve kopyala** düğmesine basın.
5. Chrome'da `F12 → Sources → Snippets → New snippet` yolunu açın.
6. `Ctrl+V`, ardından `Ctrl+Enter` ile çalıştırın.
7. OBS sayfasında açılan panelden **Kontrol et ve kur** düğmesine hedefe 1–10
   dakika kala basın.

Hazırlık bittikten sonra hedefe son 30 saniye kala OBS'ye hiçbir kontrol, saat
veya bağlantı ısıtma isteği atılmaz. Gerçek kayıt modunda hedef anda yalnızca tek
bir POST gönderilir; tekrar/spam modu yoktur.

Saat ölçümünün ve tokenın taze kalması için Snippet hedefe son 30 dakika içinde
hazırlanır. Son 10 saniye boyunca OBS sekmesini ön planda tutun.

## EXE oluşturma

```powershell
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --name OBS-Ders-Kayit bot2.py
```

Çıktı `dist/OBS-Ders-Kayit.exe` olur. EXE sistem tarayıcısını kullandığı için
Chrome/ChromeDriver sürüm eşleşmesine ihtiyaç duymaz.

## Test

```powershell
python -m unittest -v
```
