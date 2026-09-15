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

## Güvenlik ve zamanlama

- Kullanıcı adı ve şifre dosyaya yazılmaz; şifre Başlat'a basılınca GUI'den silinir.
- Tarayıcı geçici ve yalıtılmış bir Playwright oturumuyla açılır.
- Son 30 saniyede saat kontrolü, ping veya hazırlık isteği gönderilmez.
- Hedef anda yalnızca tek bir kayıt POST'u gönderilir; tekrar/spam modu yoktur.
- Son kullanıcı arayüzünde kuru prova seçeneği yoktur; Başlat gerçek kayıt kurar.
- Kritik API eşlemesi testle korunur: `ECRN = alınacak`, `SCRN = bırakılacak`.

## EXE paketleme

Playwright Chromium'u EXE ile paketlemek için:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Tarayıcı da pakete dahil edildiği için çıktı normal bir Python EXE'sinden büyük
olur, fakat cihazdaki Chrome/ChromeDriver sürümüne bağlı kalmaz. Build yalnızca
uygulamanın kullandığı tam Chromium'u ekler; ayrı headless-shell eklenmez.

Paketin gömülü Chromium'u çalıştırabildiğini doğrulamak için:

```powershell
$test = Start-Process .\dist\OBS-Ders-Kayit.exe -ArgumentList "--self-test" -Wait -PassThru
if ($test.ExitCode -eq 0) { "Self-test başarılı" }
```

## Test

```powershell
python -m unittest -v
```
