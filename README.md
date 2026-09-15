# OBS Otomatik Ders Kayıt

Uygulama kullanıcı adı, şifre, alınacak dersler ve bırakılacak dersleri tek
ekrandan alır. Playwright tarayıcıyı açar, OBS girişini yapar, Bearer yetkisini
ağ isteğinden yakalar ve tek-atış zamanlayıcısını otomatik kurar. Kullanıcının
DevTools, Snippet veya token ile uğraşması gerekmez.

## Kurulum

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Kullanım

1. `python bot2.py` ile uygulamayı açın.
2. Kullanıcı adı ve şifreyi girin.
3. **ALINACAK / EKLENECEK (ECRN)** ve **BIRAKILACAK / SİLİNECEK (SCRN)**
   alanlarını dikkatle doldurun.
4. Hedefe 3–30 dakika kala **Başlat** düğmesine basın.
5. Normal giriş otomatik tamamlanır. Kurumsal giriş ek doğrulama veya bölüm
   seçimi gösterirse yalnızca bu istisnai adımı açılan tarayıcıda tamamlayın.
6. Hazır ekranı geldikten sonra tarayıcıyı açık, son 10 saniye ön planda tutun.

İlk denemede **Kuru prova** açık bırakılmalıdır. Kuru prova giriş ve zamanlamayı
test eder ancak kayıt POST'u göndermez.

## Güvenlik ve zamanlama

- Kullanıcı adı ve şifre dosyaya yazılmaz; şifre Başlat'a basılınca GUI'den silinir.
- Tarayıcı geçici ve yalıtılmış bir Playwright oturumuyla açılır.
- Son 30 saniyede saat kontrolü, ping veya hazırlık isteği gönderilmez.
- Hedef anda yalnızca tek bir kayıt POST'u gönderilir; tekrar/spam modu yoktur.
- Kritik API eşlemesi testle korunur: `ECRN = alınacak`, `SCRN = bırakılacak`.

## EXE paketleme

Playwright Chromium'u EXE ile paketlemek için:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH="0"
python -m playwright install chromium
python -m PyInstaller --onefile --windowed --name OBS-Ders-Kayit bot2.py
```

Tarayıcı da pakete dahil edildiği için çıktı normal bir Python EXE'sinden büyük
olur, fakat cihazdaki Chrome/ChromeDriver sürümüne bağlı kalmaz.

## Test

```powershell
python -m unittest -v
```
