# OBS Ders Kayıt

İTÜ OBS için belirlenen tarih ve saatte ders ekleme ve bırakma isteği gönderen
masaüstü uygulaması. Kullanıcı girişi, saat ölçümü ve tarayıcı hazırlığı tek
ekrandan yönetilir.

**[Windows için indir — OBS-Ders-Kayit.exe](https://github.com/McanKul/ders_se-im/releases/latest/download/OBS-Ders-Kayit.exe)**

[Tüm sürümler ve sürüm notları](https://github.com/McanKul/ders_se-im/releases)

Windows sürümü Chromium tarayıcısını içerir; ayrıca Python veya ChromeDriver
kurmanız gerekmez. İndirdiğiniz `.exe` dosyasını açarak başlayabilirsiniz.

## Kullanım

1. OBS kullanıcı adınızı ve şifrenizi girin.
2. Alınacak derslerin CRN'lerini **ALINACAK / EKLENECEK (ECRN)**, bırakılacak
   derslerin CRN'lerini **BIRAKILACAK / SİLİNECEK (SCRN)** alanına yazın.
   Birden fazla CRN için virgül veya boşluk kullanın.
3. Hedef tarih ve saati bilgisayarınızın yerel saat dilimine göre ayarlayın.
   Tarih biçimi `YYYY-AA-GG`, saat biçimi örneğin `10:00:00.000` olmalıdır.
   **Ek gönderim payı** varsayılan olarak `0` ms'dir.
4. Hedefe 3–30 dakika kala **Başlat** düğmesine basın. Açılan son kontrol
   penceresinde alınacak dersleri, bırakılacak dersleri ve hedef zamanı
   doğrulayarak onaylayın.
5. Uygulama saati ölçer, tarayıcıyı açar ve giriş yapar. Ek doğrulama veya bölüm
   seçimi gerekirse açılan tarayıcıda tamamlayın.
6. **Hazır** durumunu bekleyin. Tarayıcıyı açık, son 10 saniyede ön planda
   tutun. İşlem tamamlandığında uygulamadaki sonucu ve OBS'deki ders listenizi
   kontrol edin.

**Onayladığınız işlem gerçek ders kaydı içindir.** Arayüzde kuru prova seçeneği
yoktur. Hedef zamanda tek kayıt isteği gönderilir; otomatik tekrar yapılmaz.
Kayıt sonucu OBS'nin yanıtına bağlıdır; kontenjan veya başarılı kayıt garantisi
verilmez.

## Çalışma biçimi

- Alınacak CRN'ler `ECRN`, bırakılacak CRN'ler `SCRN` olarak gönderilir.
  Bu eşleme birim testleriyle kontrol edilir.
- Bilgisayar saatinin farkı NTP ile ölçülür. Son 30 saniyede uygulama saat
  kontrolü, ping veya hazırlık isteği göndermez.
- Tarayıcı ayrı, geçici bir oturumla açılır. Uygulama kullanıcı adını ve şifreyi
  dosyaya yazmaz; şifre son kontrol onaylandıktan sonra formdan temizlenir.
- OBS girişinde alınan oturum yetkisi otomatik kullanılır; elle token girmeniz
  gerekmez.

## Kaynaktan çalıştırma

Python 3.12 ve Tkinter gereklidir. Depoyu indirdikten sonra proje klasöründe bir
sanal ortam oluşturun.

Windows / PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe app.py
```

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

Python kurulumunuz Tkinter içermiyorsa önce işletim sisteminize uygun Tkinter
paketini kurun. Çalışma sırasında OBS'ye ve NTP sunucularına erişim gerekir.

## Test

Sanal ortamın Python yorumlayıcısıyla birim testlerini çalıştırın:

```bash
python -m unittest -v
```

Windows'ta yukarıdaki ortamı etkinleştirmeden kullanıyorsanız `python` yerine
`.\.venv\Scripts\python.exe` yazın. Testler CRN ayrıştırmasını, tarih/saat
ayrıştırmasını, istek eşlemesini ve giriş betiğini kontrol eder.

Tarayıcının açılabildiğini kontrol etmek için:

```bash
python app.py --self-test
```

Bu kontrol yerel bir test sayfası açar; OBS'ye giriş yapmaz ve ders kayıt isteği
göndermez. Başarılı olduğunda çıkış kodu `0` olur.

## Windows EXE oluşturma

Windows'ta Python 3.12'nin `python` komutuyla erişilebilir olduğu bir PowerShell
oturumunda çalıştırın:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Betik ayrı bir `.venv-build` ortamı oluşturur, bağımlılıkları ve Chromium'u kurar,
ardından PyInstaller ile `dist\OBS-Ders-Kayit.exe` dosyasını üretir. Chromium
pakete dahil olduğu için çıktı dosyası büyüktür.

Paketin gömülü tarayıcıyla çalışabildiğini doğrulayın:

```powershell
$test = Start-Process .\dist\OBS-Ders-Kayit.exe -ArgumentList "--self-test" -Wait -PassThru
if ($test.ExitCode -ne 0) { throw "Self-test başarısız." }
Write-Host "Self-test başarılı."
```

## Proje yapısı

| Dosya | Görevi |
| --- | --- |
| `app.py` | Masaüstü arayüzü, giriş doğrulaması ve NTP saat ölçümü |
| `browser_automation.py` | OBS girişi, tarayıcı oturumu ve zamanlanmış kayıt isteği |
| `test_app.py` | Birim testleri |
| `build_exe.ps1` | Chromium içeren Windows EXE paketleme betiği |
