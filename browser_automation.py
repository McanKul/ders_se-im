"""Playwright ile OBS girişi, Bearer yakalama ve zamanlayıcı kurulumu."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, Request, sync_playwright
except ImportError:  # Kaynak kod doğrudan çalıştırıldığında anlaşılır hata vermek için.
    PlaywrightError = RuntimeError
    Page = Any
    Request = Any
    sync_playwright = None


OBS_HOST = "obs.itu.edu.tr"
LOGIN_HOST = "girisv3.itu.edu.tr"
OBS_PAGE_URL = "https://obs.itu.edu.tr/ogrenci/DersKayitIslemleri/DersKayit"
MINIMUM_READY_MS = 45_000


class BrowserAutomationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserRegistrationConfig:
    # Bu isimleri API isimlerinden bilinçli olarak ayırıyoruz; ters eşleme riskini azaltır.
    add_crns: tuple[str, ...]
    drop_crns: tuple[str, ...]
    target_epoch_ms: int
    target_label: str
    clock_offset_ms: float
    clock_uncertainty_ms: float
    send_delay_ms: int
    dry_run: bool


@dataclass(frozen=True)
class LoginCredentials:
    username: str
    password: str


LOGIN_SCRIPT = r"""
(credentials => {
  const lastAttempt = Number(window.__ituObsAutoLoginAttempt || 0);
  if (Date.now() - lastAttempt < 4000) return {status: "waiting"};

  const visible = element => {
    const style = getComputedStyle(element);
    return !element.disabled && style.display !== "none" && style.visibility !== "hidden";
  };
  const inputs = Array.from(document.querySelectorAll("input")).filter(visible);
  const password = inputs.find(element => (element.type || "").toLowerCase() === "password");
  const username = document.querySelector(
    'input[autocomplete="username"],input[type="email"],input[name*="user" i],input[id*="user" i],input[name*="kullan" i],input[id*="kullan" i]'
  ) || inputs.find(element => !["hidden", "password", "submit", "button", "checkbox", "radio"].includes((element.type || "text").toLowerCase()));

  const setValue = (element, value) => {
    if (!element) return;
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    descriptor.set.call(element, value);
    for (const name of ["input", "change", "blur"]) {
      element.dispatchEvent(new Event(name, {bubbles: true}));
    }
  };
  if (username) setValue(username, credentials.username);
  if (password) setValue(password, credentials.password);
  if (!username && !password) return {status: "manual"};

  const form = (password && password.form) || (username && username.form);
  const buttons = Array.from((form || document).querySelectorAll('button,input[type="submit"]')).filter(visible);
  const submit = buttons.find(element => (element.type || "").toLowerCase() === "submit") || buttons[buttons.length - 1];
  window.__ituObsAutoLoginAttempt = Date.now();
  if (form && typeof form.requestSubmit === "function") form.requestSubmit(submit || undefined);
  else if (submit) submit.click();
  else if (form) form.submit();
  else return {status: "manual"};
  return {status: "submitted"};
})(__CREDENTIALS__)
""".strip()


TOKEN_SCAN_SCRIPT = r"""
() => {
  const candidates = [];
  const seen = new Set();
  const jwtInfo = token => {
    try {
      const payload = token.replace(/^Bearer\s+/i, "").split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(decodeURIComponent(Array.from(atob(payload), c => "%" + c.charCodeAt(0).toString(16).padStart(2, "0")).join("")));
    } catch (_) { return {}; }
  };
  const add = (raw, hint = "") => {
    if (typeof raw !== "string") return;
    const value = raw.trim();
    if (!value || seen.has(value)) return;
    seen.add(value);
    const bearer = value.match(/^Bearer\s+(.+)$/i);
    const jwt = value.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
    let token = "", score = 0;
    if (bearer) { token = "Bearer " + bearer[1]; score = 100; }
    else if (jwt) { token = "Bearer " + jwt[0]; score = /access.?token/i.test(hint) ? 120 : 90; }
    if (!token) return;
    const info = jwtInfo(token);
    if (info.exp && info.exp * 1000 < Date.now() + 30000) return;
    if (info.scp || info.roles) score += 20;
    if (info.nonce && !info.scp && !info.roles) score -= 20;
    candidates.push({token, score});
  };
  const walk = (value, hint = "", depth = 0) => {
    if (depth > 5 || value == null) return;
    if (typeof value === "string") {
      add(value, hint);
      if ((value.startsWith("{") || value.startsWith("[")) && value.length < 200000) {
        try { walk(JSON.parse(value), hint, depth + 1); } catch (_) {}
      }
    } else if (Array.isArray(value)) value.forEach(item => walk(item, hint, depth + 1));
    else if (typeof value === "object") Object.entries(value).forEach(([key, item]) => walk(item, hint + "." + key, depth + 1));
  };
  for (const storage of [sessionStorage, localStorage]) {
    try {
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        walk(storage.getItem(key), key || "");
      }
    } catch (_) {}
  }
  candidates.sort((a, b) => b.score - a.score);
  return candidates.length ? candidates[0].token : "";
}
""".strip()


SCHEDULER_TEMPLATE = r"""
() => {
  "use strict";
  const CONFIG = __CONFIG__;
  const REGISTRATION_URL = "/api/ders-kayit/v21";
  const nativeFetch = window.fetch.bind(window);
  window.stop();

  const timerCeiling = window.setTimeout(() => {}, 0);
  for (let timer = 0; timer <= timerCeiling; timer += 1) {
    window.clearTimeout(timer);
    window.clearInterval(timer);
  }
  window.fetch = () => Promise.reject(new Error("OBS bot sessiz bekleme etkin"));
  try {
    XMLHttpRequest.prototype.open = function () { throw new Error("OBS bot sessiz bekleme etkin"); };
  } catch (_) {}

  document.title = "OBS Ders Kayıt Hazır";
  document.documentElement.innerHTML = `
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      *{box-sizing:border-box} body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1020;color:#f8fafc;font:16px/1.5 system-ui,sans-serif}
      main{width:min(720px,calc(100% - 32px));padding:28px;border:1px solid #334155;border-radius:18px;background:#111827;box-shadow:0 24px 70px #0008}
      h1{margin:0 0 8px;font-size:24px}.muted{color:#94a3b8}.time{margin:22px 0;font:700 42px ui-monospace,monospace;color:#60a5fa}
      .courses{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0}.course{padding:14px;border-radius:10px}.add{background:#052e20;border:1px solid #15803d}.drop{background:#3f1218;border:1px solid #b91c1c}
      .label{display:block;font-weight:800;margin-bottom:4px}.add .label{color:#4ade80}.drop .label{color:#f87171}
      pre{white-space:pre-wrap;max-height:220px;overflow:auto;padding:14px;border-radius:10px;background:#080d19;color:#cbd5e1}
      button{padding:10px 16px;border:1px solid #475569;border-radius:8px;background:#1e293b;color:white;cursor:pointer}
    </style></head>
    <body><main><h1>OBS ders kaydı hazır</h1><div class="muted" data-target></div>
      <div class="courses"><div class="course add"><span class="label">ALINACAK / EKLENECEK (ECRN)</span><span data-add></span></div>
      <div class="course drop"><span class="label">BIRAKILACAK / SİLİNECEK (SCRN)</span><span data-drop></span></div></div>
      <div class="time" data-time>--:--:--.---</div><pre data-log></pre><button data-cancel>İptal et</button>
    </main></body>`;

  let cancelled = false;
  const targetBox = document.querySelector("[data-target]");
  const timeBox = document.querySelector("[data-time]");
  const logBox = document.querySelector("[data-log]");
  const anchorPerformance = performance.now();
  const anchorUtc = Date.now() + CONFIG.clockOffsetMs;
  const accurateNow = () => anchorUtc + (performance.now() - anchorPerformance);
  const dispatchAt = CONFIG.targetEpochMs + CONFIG.sendDelayMs;
  targetBox.textContent = `Hedef: ${CONFIG.targetLabel}`;
  document.querySelector("[data-add]").textContent = CONFIG.addCrns.join(", ") || "Yok";
  document.querySelector("[data-drop]").textContent = CONFIG.dropCrns.join(", ") || "Yok";

  const log = message => {
    const stamp = new Date().toLocaleTimeString("tr-TR", {hour12:false, fractionalSecondDigits:3});
    logBox.textContent += `[${stamp}] ${message}\n`;
    logBox.scrollTop = logBox.scrollHeight;
  };
  const formatRemaining = milliseconds => {
    const value = Math.max(0, milliseconds), hours = Math.floor(value / 3600000);
    const minutes = Math.floor((value % 3600000) / 60000), seconds = Math.floor((value % 60000) / 1000), millis = Math.floor(value % 1000);
    return [hours, minutes, seconds].map(value => String(value).padStart(2, "0")).join(":") + "." + String(millis).padStart(3, "0");
  };
  const readCookie = name => {
    const item = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith(name + "="));
    return item ? decodeURIComponent(item.slice(name.length + 1)) : "";
  };
  const sleep = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds));
  const wait = async () => {
    while (!cancelled) {
      const remaining = dispatchAt - accurateNow();
      if (remaining <= 0) return;
      if (remaining > 3500) await sleep(Math.min(30000, remaining - 3000));
      else {
        timeBox.textContent = "HASSAS BEKLEME";
        while (!cancelled && accurateNow() < dispatchAt) {}
        return;
      }
    }
    throw new Error("İşlem iptal edildi.");
  };

  document.querySelector("[data-cancel]").addEventListener("click", () => {
    cancelled = true;
    log("İşlem iptal edildi.");
  });
  const countdown = window.setInterval(() => {
    timeBox.textContent = formatRemaining(dispatchAt - accurateNow());
  }, 50);

  (async () => {
    try {
      log(`Hazır. NTP ofseti ${CONFIG.clockOffsetMs.toFixed(1)} ms (±${CONFIG.clockUncertaintyMs.toFixed(1)} ms).`);
      log("Son 30 saniyede kontrol/ping yok; hedefte tek kayıt isteği var.");
      await wait();
      window.clearInterval(countdown);
      timeBox.textContent = "00:00:00.000";
      if (cancelled) throw new Error("İşlem iptal edildi.");
      const delta = accurateNow() - dispatchAt;
      if (CONFIG.dryRun) {
        const message = `KURU PROVA: tetikleme farkı ${delta >= 0 ? "+" : ""}${delta.toFixed(2)} ms.`;
        log(message); timeBox.style.color = "#34d399";
        window.__ituObsBotResult = {done:true,ok:true,dryRun:true,message};
        return;
      }

      const headers = {"Accept":"application/json, text/plain, */*","Content-Type":"application/json","X-Requested-With":"XMLHttpRequest","Authorization":CONFIG.bearer};
      const xsrf = readCookie("XSRF-TOKEN") || readCookie("xsrf-token");
      if (xsrf) headers["X-XSRF-TOKEN"] = xsrf;
      // Kritik ve test edilen eşleme: alınacaklar ECRN, bırakılacaklar SCRN.
      const requestBody = {ECRN: CONFIG.addCrns, SCRN: CONFIG.dropCrns};
      log(`POST başlatıldı; tetikleme farkı ${delta >= 0 ? "+" : ""}${delta.toFixed(2)} ms.`);
      const response = await nativeFetch(REGISTRATION_URL, {method:"POST",headers,credentials:"include",cache:"no-store",body:JSON.stringify(requestBody)});
      const text = await response.text();
      let body = text; try { body = text ? JSON.parse(text) : null; } catch (_) {}
      const message = `HTTP ${response.status}: ${JSON.stringify(body)}`;
      log(message); timeBox.style.color = response.ok ? "#34d399" : "#f87171";
      window.__ituObsBotResult = {done:true,ok:response.ok,status:response.status,message};
    } catch (error) {
      const message = String(error.message || error); log(message); timeBox.style.color = "#f87171";
      window.__ituObsBotResult = {done:true,ok:false,message};
    }
  })();
  return true;
}
""".strip()


def build_login_script(credentials: LoginCredentials) -> str:
    payload = json.dumps(
        {"username": credentials.username, "password": credentials.password},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return LOGIN_SCRIPT.replace("__CREDENTIALS__", payload, 1)


def build_scheduler(config: BrowserRegistrationConfig, bearer: str) -> str:
    normalized = bearer if bearer.lower().startswith("bearer ") else f"Bearer {bearer}"
    payload = {
        "addCrns": list(config.add_crns),
        "dropCrns": list(config.drop_crns),
        "targetEpochMs": config.target_epoch_ms,
        "targetLabel": config.target_label,
        "clockOffsetMs": round(config.clock_offset_ms, 3),
        "clockUncertaintyMs": round(config.clock_uncertainty_ms, 3),
        "sendDelayMs": config.send_delay_ms,
        "dryRun": config.dry_run,
        "bearer": normalized,
    }
    return SCHEDULER_TEMPLATE.replace(
        "__CONFIG__",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        1,
    )


class BrowserAutomation:
    """Kullanıcı müdahalesi gerektirmeyen Playwright kayıt akışı."""

    def __init__(self, status_callback: Callable[[str], None]) -> None:
        self._status = status_callback
        self._stop = threading.Event()
        self._bearer = ""

    def _capture_authorization(self, request: Request) -> None:
        if self._bearer:
            return
        host = (urlparse(request.url).hostname or "").lower()
        if host != OBS_HOST or request.resource_type not in {"fetch", "xhr"}:
            return
        try:
            value = request.header_value("authorization") or ""
        except PlaywrightError:
            return
        if value.lower().startswith("bearer "):
            self._bearer = value

    def run(self, credentials: LoginCredentials, config: BrowserRegistrationConfig) -> dict[str, Any]:
        if sync_playwright is None:
            raise BrowserAutomationError(
                "Playwright kurulu değil. 'python -m pip install playwright' komutunu çalıştırın."
            )
        self._status("Güvenli tarayıcı açılıyor…")
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(
                        headless=False,
                        args=["--incognito", "--disable-background-networking", "--disable-sync"],
                    )
                except PlaywrightError as exc:
                    raise BrowserAutomationError(
                        "Playwright Chromium bulunamadı. 'python -m playwright install chromium' çalıştırın."
                    ) from exc

                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    locale="tr-TR",
                    service_workers="block",
                )
                page = context.new_page()
                page.on("request", self._capture_authorization)
                try:
                    result = self._login_capture_and_arm(page, credentials, config)
                    return result
                finally:
                    context.close()
                    browser.close()
        except BrowserAutomationError:
            raise
        except PlaywrightError as exc:
            raise BrowserAutomationError(f"Tarayıcı hatası: {exc}") from exc

    def _login_capture_and_arm(
        self,
        page: Page,
        credentials: LoginCredentials,
        config: BrowserRegistrationConfig,
    ) -> dict[str, Any]:
        self._status("OBS açılıyor…")
        page.goto(OBS_PAGE_URL, wait_until="domcontentloaded", timeout=45_000)
        last_login_attempt = 0.0
        last_storage_scan = 0.0
        manual_notice_sent = False

        while not self._stop.is_set():
            remaining_ms = config.target_epoch_ms + config.send_delay_ms - (
                time.time() * 1000 + config.clock_offset_ms
            )
            if remaining_ms < MINIMUM_READY_MS:
                raise BrowserAutomationError("Giriş/token hazırlığı zamanında tamamlanamadı; kayıt kurulmadı.")

            host = (urlparse(page.url).hostname or "").lower()
            now = time.monotonic()
            if host == LOGIN_HOST and now - last_login_attempt >= 2.0:
                self._status("Kullanıcı adı ve şifre giriliyor…")
                result = page.evaluate(build_login_script(credentials))
                last_login_attempt = now
                if isinstance(result, dict) and result.get("status") == "manual" and not manual_notice_sent:
                    self._status("Ek doğrulama/bölüm seçimi çıktıysa açılan tarayıcıda tamamlayın.")
                    manual_notice_sent = True

            if host == OBS_HOST:
                bearer = self._bearer
                if not bearer and now - last_storage_scan >= 1.0:
                    self._status("Giriş tamamlandı; yetki anahtarı alınıyor…")
                    scanned = page.evaluate(TOKEN_SCAN_SCRIPT)
                    if isinstance(scanned, str) and scanned.lower().startswith("bearer "):
                        bearer = scanned
                        self._bearer = scanned
                    last_storage_scan = now
                if bearer:
                    self._status("Yetki bulundu; tek-atış zamanlayıcısı kuruluyor…")
                    installed = page.evaluate(build_scheduler(config, bearer))
                    if installed is not True:
                        raise BrowserAutomationError("Tarayıcı zamanlayıcısı kurulamadı.")
                    self._status("Hazır. Tarayıcıyı açık ve son 10 saniye ön planda tutun.")
                    return self._monitor_result(page, config)

            page.wait_for_timeout(250)

        raise BrowserAutomationError("İşlem kullanıcı tarafından iptal edildi.")

    def _monitor_result(
        self,
        page: Page,
        config: BrowserRegistrationConfig,
    ) -> dict[str, Any]:
        # Hedefin 500 ms sonrasına kadar Playwright/CDP komutu da yollamayız;
        # son saniyelerde yalnızca sayfaya önceden kurulmuş zamanlayıcı çalışır.
        while not self._stop.is_set():
            remaining_ms = config.target_epoch_ms + config.send_delay_ms - (
                time.time() * 1000 + config.clock_offset_ms
            )
            if remaining_ms <= -500:
                break
            self._stop.wait(min(0.5, max(0.01, (remaining_ms + 500) / 1000)))

        while not self._stop.is_set():
            result = page.evaluate("() => window.__ituObsBotResult || null")
            if isinstance(result, dict) and result.get("done"):
                self._status(str(result.get("message", "İşlem tamamlandı.")))
                page.wait_for_timeout(1500)
                return result
            page.wait_for_timeout(250)
        raise BrowserAutomationError("İşlem kullanıcı tarafından iptal edildi.")

    def cancel(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.cancel()
