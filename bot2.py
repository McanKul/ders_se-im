# -*- coding: utf-8 -*-
"""OBS ders kaydı için tarayıcı içi tek-atış Snippet hazırlayıcı.

Uygulama Chrome'u otomasyonla yönetmez. Kullanıcı normal tarayıcıda OBS'ye
giriş yapar; bu araç yalnızca NTP saat farkını ölçer ve açık OBS sayfasında
çalıştırılacak JavaScript Snippet'ini üretir.
"""

from __future__ import annotations

import json
import socket
import statistics
import struct
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Iterable

import tkinter as tk
from tkinter import messagebox, ttk


OBS_PAGE_URL = "https://obs.itu.edu.tr/ogrenci/DersKayitIslemleri/DersKayit"
NTP_EPOCH_DELTA = 2_208_988_800
NTP_SERVERS = ("time.cloudflare.com", "time.google.com", "pool.ntp.org")
MINIMUM_PREPARE_SECONDS = 90
MAXIMUM_PREPARE_SECONDS = 30 * 60


@dataclass(frozen=True)
class NtpSample:
    server: str
    offset_ms: float
    round_trip_ms: float


@dataclass(frozen=True)
class ClockEstimate:
    offset_ms: float
    uncertainty_ms: float
    best_round_trip_ms: float
    sample_count: int


@dataclass(frozen=True)
class RegistrationConfig:
    ecrn: tuple[str, ...]
    scrn: tuple[str, ...]
    target_epoch_ms: int
    target_label: str
    clock_offset_ms: float
    clock_uncertainty_ms: float
    send_delay_ms: int
    dry_run: bool


def parse_crns(raw: str) -> tuple[str, ...]:
    """Virgül veya boşlukla ayrılmış CRN'leri sıralı ve tekrarsız döndürür."""
    result: list[str] = []
    seen: set[str] = set()
    for item in raw.replace(",", " ").split():
        crn = item.strip()
        if not crn:
            continue
        if not crn.isdigit():
            raise ValueError(f"CRN yalnızca rakamlardan oluşmalı: {crn}")
        if crn not in seen:
            seen.add(crn)
            result.append(crn)
    return tuple(result)


def parse_target(target_date: str, target_time: str) -> tuple[int, str]:
    """Yerel tarih/saati Unix milisaniyesine çevirir."""
    try:
        day = datetime.strptime(target_date.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalı.") from exc

    raw_time = target_time.strip()
    fmt = "%H:%M:%S.%f" if "." in raw_time else "%H:%M:%S"
    try:
        parsed_time = datetime.strptime(raw_time, fmt).time()
    except ValueError as exc:
        raise ValueError("Saat SS:DD:SS.mmm biçiminde olmalı.") from exc

    local_target = datetime.combine(day, parsed_time).astimezone()
    epoch_ms = round(local_target.timestamp() * 1000)
    label = local_target.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return epoch_ms, label


def _ntp_parts(unix_seconds: float) -> tuple[int, int]:
    ntp_seconds = unix_seconds + NTP_EPOCH_DELTA
    whole = int(ntp_seconds)
    fraction = int((ntp_seconds - whole) * (1 << 32))
    return whole, fraction


def _ntp_timestamp(seconds: int, fraction: int) -> float:
    return seconds - NTP_EPOCH_DELTA + fraction / float(1 << 32)


def query_ntp(server: str, timeout: float = 1.8) -> NtpSample:
    """Tek bir SNTP ölçümü yapar ve NTP ofset formülünü uygular."""
    addresses = socket.getaddrinfo(server, 123, type=socket.SOCK_DGRAM)
    last_error: OSError | None = None

    for family, socktype, proto, _canonname, address in addresses:
        packet = bytearray(48)
        packet[0] = 0x23  # LI=0, NTP v4, client mode
        t1 = time.time()
        sec, frac = _ntp_parts(t1)
        struct.pack_into("!II", packet, 40, sec, frac)

        try:
            with socket.socket(family, socktype, proto) as client:
                client.settimeout(timeout)
                client.sendto(packet, address)
                response, _ = client.recvfrom(512)
                t4 = time.time()
        except OSError as exc:
            last_error = exc
            continue

        if len(response) < 48:
            raise OSError(f"{server} kısa NTP yanıtı döndürdü")

        leap = response[0] >> 6
        mode = response[0] & 0x07
        stratum = response[1]
        if leap == 3 or mode not in (4, 5) or not 1 <= stratum <= 15:
            raise OSError(f"{server} geçersiz NTP yanıtı döndürdü")

        values = struct.unpack("!12I", response[:48])
        t2 = _ntp_timestamp(values[8], values[9])
        t3 = _ntp_timestamp(values[10], values[11])
        offset = ((t2 - t1) + (t3 - t4)) / 2.0
        delay = (t4 - t1) - (t3 - t2)
        return NtpSample(server, offset * 1000.0, max(0.0, delay * 1000.0))

    raise last_error or OSError(f"{server} için adres bulunamadı")


def estimate_clock(
    servers: Iterable[str] = NTP_SERVERS,
    samples_per_server: int = 2,
) -> ClockEstimate:
    """Paralel NTP örneklerinden düşük gecikmeli, aykırı değerlere dayanıklı tahmin üretir."""
    jobs = [server for server in servers for _ in range(samples_per_server)]
    samples: list[NtpSample] = []
    with ThreadPoolExecutor(max_workers=len(jobs) or 1) as executor:
        futures = [executor.submit(query_ntp, server) for server in jobs]
        for future in as_completed(futures):
            try:
                samples.append(future.result())
            except OSError:
                continue

    if not samples:
        raise OSError("NTP sunucularına ulaşılamadı")

    ranked = sorted(samples, key=lambda sample: sample.round_trip_ms)
    selected = ranked[: min(3, len(ranked))]
    offsets = [sample.offset_ms for sample in selected]
    offset_ms = statistics.median(offsets)
    spread = max(abs(value - offset_ms) for value in offsets)
    uncertainty_ms = max(selected[0].round_trip_ms / 2.0, spread)
    return ClockEstimate(
        offset_ms=offset_ms,
        uncertainty_ms=uncertainty_ms,
        best_round_trip_ms=selected[0].round_trip_ms,
        sample_count=len(samples),
    )


SNIPPET_TEMPLATE = r"""
(() => {
  "use strict";

  const CONFIG = __CONFIG_JSON__;
  const REGISTRATION_URL = "/api/ders-kayit/v21";
  const TIME_URL = "/api/ogrenci/Takvim/KayitZamaniKontrolu";
  const INSTANCE_KEY = "__ituObsRegistrationSnippet";
  const PANEL_ID = "itu-obs-snippet-panel";
  const QUIET_WINDOW_MS = 30000;
  const MINIMUM_ARM_LEAD_MS = 40000;
  const MAXIMUM_ARM_LEAD_MS = 20 * 60 * 1000;

  if (location.hostname !== "obs.itu.edu.tr") {
    alert("Bu Snippet yalnızca obs.itu.edu.tr sayfasında çalıştırılabilir.");
    return;
  }

  if (window[INSTANCE_KEY] && typeof window[INSTANCE_KEY].cancel === "function") {
    window[INSTANCE_KEY].cancel();
  }
  const previousPanel = document.getElementById(PANEL_ID);
  if (previousPanel) previousPanel.remove();

  let cancelled = false;
  let running = false;
  let countdownTimer = null;
  const instance = {
    cancel() {
      cancelled = true;
      if (countdownTimer) clearInterval(countdownTimer);
    }
  };
  window[INSTANCE_KEY] = instance;

  const anchorPerformance = performance.now();
  const anchorUtc = Date.now() + CONFIG.clockOffsetMs;
  const accurateNow = () => anchorUtc + (performance.now() - anchorPerformance);
  const dispatchAt = CONFIG.targetEpochMs + CONFIG.sendDelayMs;

  const panel = document.createElement("section");
  panel.id = PANEL_ID;
  panel.style.cssText = [
    "position:fixed", "right:18px", "bottom:18px", "z-index:2147483647",
    "width:390px", "padding:16px", "border-radius:12px",
    "background:#111827", "color:#f9fafb", "font:13px/1.45 system-ui,sans-serif",
    "box-shadow:0 18px 50px rgba(0,0,0,.42)", "border:1px solid #374151"
  ].join(";");
  panel.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
      <strong style="font-size:15px">OBS tek-atış ${CONFIG.dryRun ? "kuru prova" : "kayıt"}</strong>
      <button data-close title="Paneli kapat" style="border:0;background:transparent;color:#9ca3af;font-size:20px;cursor:pointer">×</button>
    </div>
    <div data-summary style="margin-top:8px;color:#d1d5db"></div>
    <div data-countdown style="margin-top:8px;font:700 22px ui-monospace,monospace;color:#60a5fa">--:--:--.---</div>
    <div data-auth-row style="display:none;margin-top:10px">
      <label style="display:block;margin-bottom:4px;color:#fbbf24">Bearer otomatik bulunamadı</label>
      <input data-token type="password" autocomplete="off" placeholder="Bearer tokenı yalnızca bu sekme için yapıştır"
        style="box-sizing:border-box;width:100%;padding:8px;border:1px solid #4b5563;border-radius:6px;background:#1f2937;color:#fff">
    </div>
    <pre data-log style="white-space:pre-wrap;max-height:150px;overflow:auto;margin:10px 0;padding:8px;border-radius:6px;background:#0b1020;color:#d1d5db"></pre>
    <div style="display:flex;gap:8px">
      <button data-arm style="flex:1;padding:9px;border:0;border-radius:7px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer">Kontrol et ve kur</button>
      <button data-cancel disabled style="padding:9px 14px;border:1px solid #4b5563;border-radius:7px;background:#1f2937;color:#fff;cursor:pointer">İptal</button>
    </div>
    <small style="display:block;margin-top:8px;color:#9ca3af">Son 30 saniye ağ tamamen sessizdir; hedefte yalnızca kayıt POST'u çıkar.</small>
  `;
  document.body.appendChild(panel);

  const summary = panel.querySelector("[data-summary]");
  const countdown = panel.querySelector("[data-countdown]");
  const logBox = panel.querySelector("[data-log]");
  const authRow = panel.querySelector("[data-auth-row]");
  const tokenInput = panel.querySelector("[data-token]");
  const armButton = panel.querySelector("[data-arm]");
  const cancelButton = panel.querySelector("[data-cancel]");
  summary.textContent = `${CONFIG.targetLabel} · ECRN: ${CONFIG.ecrn.join(", ") || "—"} · SCRN: ${CONFIG.scrn.join(", ") || "—"}`;

  const writeLog = (message) => {
    const stamp = new Date().toLocaleTimeString("tr-TR", { hour12: false, fractionalSecondDigits: 3 });
    logBox.textContent += `[${stamp}] ${message}\n`;
    logBox.scrollTop = logBox.scrollHeight;
  };

  const formatRemaining = (milliseconds) => {
    const value = Math.max(0, milliseconds);
    const hours = Math.floor(value / 3600000);
    const minutes = Math.floor((value % 3600000) / 60000);
    const seconds = Math.floor((value % 60000) / 1000);
    const millis = Math.floor(value % 1000);
    return [hours, minutes, seconds].map(v => String(v).padStart(2, "0")).join(":") + "." + String(millis).padStart(3, "0");
  };

  const readCookie = (name) => {
    const item = document.cookie.split(";").map(v => v.trim()).find(v => v.startsWith(name + "="));
    return item ? decodeURIComponent(item.slice(name.length + 1)) : "";
  };

  const jwtInfo = (token) => {
    try {
      const raw = token.replace(/^Bearer\s+/i, "");
      const payload = raw.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(decodeURIComponent(Array.from(atob(payload), c => "%" + c.charCodeAt(0).toString(16).padStart(2, "0")).join("")));
    } catch (_) {
      return {};
    }
  };

  const findBearer = () => {
    const candidates = [];
    const seen = new Set();
    const addCandidate = (raw, hint = "") => {
      if (typeof raw !== "string") return;
      const value = raw.trim();
      if (!value || seen.has(value)) return;
      seen.add(value);
      const bearer = value.match(/^Bearer\s+(.+)$/i);
      const jwt = value.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
      let token = "";
      let score = 0;
      if (bearer) { token = "Bearer " + bearer[1]; score = 100; }
      else if (jwt) { token = "Bearer " + jwt[0]; score = /access.?token/i.test(hint) ? 120 : 90; }
      else if (/authorization|access.?token|bearer/i.test(hint) && value.length > 24) {
        token = "Bearer " + value;
        score = 60;
      }
      if (!token) return;
      const info = jwtInfo(token);
      if (info.exp && info.exp * 1000 < Date.now() + 30000) return;
      if (info.scp || info.roles) score += 20;
      if (info.nonce && !info.scp && !info.roles) score -= 20;
      candidates.push({ token, score: score + (Number(info.exp) || 0) / 1e12 });
    };

    const walk = (value, hint = "", depth = 0) => {
      if (depth > 5 || value == null) return;
      if (typeof value === "string") {
        addCandidate(value, hint);
        if ((value.startsWith("{") || value.startsWith("[")) && value.length < 200000) {
          try { walk(JSON.parse(value), hint, depth + 1); } catch (_) {}
        }
        return;
      }
      if (Array.isArray(value)) {
        value.forEach(item => walk(item, hint, depth + 1));
      } else if (typeof value === "object") {
        Object.entries(value).forEach(([key, item]) => walk(item, hint + "." + key, depth + 1));
      }
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
  };

  const buildHeaders = () => {
    const headers = {
      "Accept": "application/json, text/plain, */*",
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest"
    };
    const bearer = findBearer() || tokenInput.value.trim();
    if (bearer) headers.Authorization = /^Bearer\s+/i.test(bearer) ? bearer : "Bearer " + bearer;
    const xsrf = readCookie("XSRF-TOKEN") || readCookie("xsrf-token");
    if (xsrf) headers["X-XSRF-TOKEN"] = xsrf;
    return headers;
  };

  const checkedFetch = async (url, options) => {
    const response = await fetch(url, options);
    const text = await response.text();
    let body = text;
    try { body = text ? JSON.parse(text) : null; } catch (_) {}
    return { response, body };
  };

  const preflight = async () => {
    const headers = buildHeaders();
    const tokenInfo = jwtInfo(headers.Authorization || "");
    if (tokenInfo.exp && tokenInfo.exp * 1000 <= dispatchAt + 5000) {
      throw new Error("Bearer token hedef saatten önce geçersiz olacak. OBS oturumunu yenileyip Snippet'i tekrar çalıştır.");
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3500);
    try {
      const result = await checkedFetch(TIME_URL + "?_snippet=" + Date.now(), {
        method: "GET", headers, credentials: "include", cache: "no-store", signal: controller.signal
      });
      if (result.response.status === 401 || result.response.status === 403) {
        authRow.style.display = "block";
        throw new Error("OBS oturumu/yetkisi doğrulanamadı. Bearer alanını doldurup tekrar dene.");
      }
      if (!result.response.ok) throw new Error(`Hazırlık isteği HTTP ${result.response.status} döndürdü.`);
      authRow.style.display = "none";
      return headers;
    } finally {
      clearTimeout(timeout);
    }
  };

  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const waitForDispatch = async () => {
    while (!cancelled) {
      const remaining = dispatchAt - accurateNow();
      if (remaining <= 0) return;
      if (remaining > 500) await sleep(Math.min(30000, remaining - 250));
      else if (remaining > 25) await sleep(Math.max(1, remaining - 12));
      else {
        while (!cancelled && accurateNow() < dispatchAt) {}
        return;
      }
    }
    throw new Error("İşlem iptal edildi.");
  };

  const run = async () => {
    if (running) return;
    const initialRemaining = dispatchAt - accurateNow();
    if (initialRemaining < MINIMUM_ARM_LEAD_MS) {
      writeLog("Kurmak için çok geç: hazırlık kontrolü hedefe en az 40 saniye kala yapılmalı.");
      return;
    }
    if (initialRemaining > MAXIMUM_ARM_LEAD_MS) {
      writeLog("Çok erken: saat/token taze kalsın diye hedefe son 20 dakika içinde kur.");
      return;
    }
    running = true;
    armButton.disabled = true;
    cancelButton.disabled = false;
    try {
      const headers = await preflight();
      if (dispatchAt - accurateNow() < QUIET_WINDOW_MS) {
        throw new Error("Hazırlık sessiz pencereye sarktı; güvenlik için kayıt kurulmadı.");
      }
      writeLog(`Yetki hazır. NTP ofseti ${CONFIG.clockOffsetMs.toFixed(1)} ms (tahmini ±${CONFIG.clockUncertaintyMs.toFixed(1)} ms).`);
      writeLog(CONFIG.dryRun ? "Kuru prova kuruldu; POST gönderilmeyecek." : "Gerçek kayıt tek atış için kuruldu.");
      writeLog("Hedefe 30 saniye kala ağ sessiz; ek kontrol/ping yapılmayacak.");

      const body = JSON.stringify({ ECRN: CONFIG.ecrn, SCRN: CONFIG.scrn });
      const request = {
        method: "POST", headers, credentials: "include", cache: "no-store", body
      };

      countdownTimer = setInterval(() => {
        countdown.textContent = formatRemaining(dispatchAt - accurateNow());
      }, 50);
      await waitForDispatch();
      clearInterval(countdownTimer);
      countdown.textContent = "00:00:00.000";
      if (cancelled) throw new Error("İşlem iptal edildi.");

      const delta = accurateNow() - dispatchAt;
      if (CONFIG.dryRun) {
        writeLog(`KURU PROVA: tetikleme farkı ${delta >= 0 ? "+" : ""}${delta.toFixed(2)} ms.`);
        countdown.style.color = "#34d399";
        return;
      }

      writeLog(`POST başlatıldı; tetikleme farkı ${delta >= 0 ? "+" : ""}${delta.toFixed(2)} ms.`);
      const result = await checkedFetch(REGISTRATION_URL, request);
      writeLog(`HTTP ${result.response.status}: ${JSON.stringify(result.body)}`);
      countdown.style.color = result.response.ok ? "#34d399" : "#f87171";
    } catch (error) {
      const message = error && error.name === "AbortError" ? "Hazırlık isteği zaman aşımına uğradı." : String(error.message || error);
      writeLog(message);
      countdown.style.color = "#f87171";
      armButton.disabled = false;
    } finally {
      running = false;
      cancelButton.disabled = true;
    }
  };

  panel.querySelector("[data-close]").addEventListener("click", () => {
    instance.cancel();
    panel.remove();
  });
  cancelButton.addEventListener("click", () => {
    instance.cancel();
    writeLog("İşlem iptal edildi.");
    cancelButton.disabled = true;
    armButton.disabled = true;
  });
  armButton.addEventListener("click", run);

  countdown.textContent = formatRemaining(dispatchAt - accurateNow());
  writeLog("Ön kontrol için ‘Kontrol et ve kur’ düğmesine bas.");
})();
""".strip()


def build_snippet(config: RegistrationConfig) -> str:
    payload = {
        "ecrn": list(config.ecrn),
        "scrn": list(config.scrn),
        "targetEpochMs": config.target_epoch_ms,
        "targetLabel": config.target_label,
        "clockOffsetMs": round(config.clock_offset_ms, 3),
        "clockUncertaintyMs": round(config.clock_uncertainty_ms, 3),
        "sendDelayMs": config.send_delay_ms,
        "dryRun": config.dry_run,
    }
    return SNIPPET_TEMPLATE.replace(
        "__CONFIG_JSON__",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        1,
    )


def _next_default_registration_day() -> date:
    now = datetime.now()
    today_at_ten = datetime.combine(now.date(), datetime_time(10, 0))
    return now.date() if now < today_at_ten else now.date() + timedelta(days=1)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OBS Ders Kayıt · Snippet")
        self.geometry("650x475")
        self.minsize(610, 450)

        self.var_ecrn = tk.StringVar()
        self.var_scrn = tk.StringVar()
        self.var_date = tk.StringVar(value=_next_default_registration_day().isoformat())
        self.var_time = tk.StringVar(value="10:00:00.000")
        self.var_delay = tk.StringVar(value="0")
        self.var_dry_run = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="Önce OBS'yi açıp normal şekilde giriş yap.")
        self._last_snippet: str | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="OBS tek-atış hazırlayıcı", font=("Segoe UI", 15, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )

        ttk.Label(outer, text="Eklenecek CRN").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_ecrn).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=5)

        ttk.Label(outer, text="Bırakılacak CRN").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_scrn).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=5)

        ttk.Label(outer, text="Hedef tarih").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_date, width=14).grid(row=3, column=1, sticky="w", padx=(12, 0), pady=5)
        ttk.Label(outer, text="YYYY-AA-GG").grid(row=3, column=2, sticky="w", padx=(8, 0))

        ttk.Label(outer, text="Hedef saat").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_time, width=16).grid(row=4, column=1, sticky="w", padx=(12, 0), pady=5)
        ttk.Label(outer, text="SS:DD:SS.mmm").grid(row=4, column=2, sticky="w", padx=(8, 0))

        ttk.Label(outer, text="Ek gönderim payı").grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_delay, width=8).grid(row=5, column=1, sticky="w", padx=(12, 0), pady=5)
        ttk.Label(outer, text="ms (0 = tam hedefte başlat)").grid(row=5, column=2, sticky="w", padx=(8, 0))

        ttk.Checkbutton(
            outer,
            text="Kuru prova — zamanlamayı ölç, kayıt POST'u gönderme",
            variable=self.var_dry_run,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 8))

        buttons = ttk.Frame(outer)
        buttons.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 12))
        self.btn_open = ttk.Button(buttons, text="1 · OBS'yi aç", command=self.open_obs)
        self.btn_open.pack(side=tk.LEFT)
        self.btn_prepare = ttk.Button(buttons, text="2 · Snippet'i hazırla ve kopyala", command=self.prepare)
        self.btn_prepare.pack(side=tk.LEFT, padx=8)
        self.btn_copy = ttk.Button(buttons, text="Tekrar kopyala", command=self.copy_again, state=tk.DISABLED)
        self.btn_copy.pack(side=tk.LEFT)

        status_box = ttk.LabelFrame(outer, text="Durum", padding=10)
        status_box.grid(row=8, column=0, columnspan=3, sticky="nsew")
        outer.rowconfigure(8, weight=1)
        ttk.Label(status_box, textvariable=self.var_status, wraplength=575, justify=tk.LEFT).pack(anchor="w")

        instructions = (
            "Chrome: F12 → Sources → Snippets → New snippet → Ctrl+V → Ctrl+Enter. "
            "OBS üzerinde açılan panelden ‘Kontrol et ve kur’a hedefe en az 40 saniye kala bas."
        )
        ttk.Label(outer, text=instructions, wraplength=600, foreground="#555555", justify=tk.LEFT).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(12, 0)
        )

    def open_obs(self) -> None:
        webbrowser.open(OBS_PAGE_URL, new=2)
        self.var_status.set("OBS açıldı. Giriş yaptıktan sonra Snippet'i hazırlayabilirsin.")

    def _read_config_without_clock(self) -> tuple[tuple[str, ...], tuple[str, ...], int, str, int]:
        ecrn = parse_crns(self.var_ecrn.get())
        scrn = parse_crns(self.var_scrn.get())
        if not ecrn and not scrn:
            raise ValueError("En az bir ECRN veya SCRN girmelisin.")
        overlap = set(ecrn).intersection(scrn)
        if overlap:
            raise ValueError("Aynı CRN hem ekleme hem bırakma listesinde olamaz: " + ", ".join(sorted(overlap)))

        target_epoch_ms, target_label = parse_target(self.var_date.get(), self.var_time.get())
        try:
            delay_ms = int(self.var_delay.get().strip())
        except ValueError as exc:
            raise ValueError("Ek gönderim payı tam sayı olmalı.") from exc
        if not 0 <= delay_ms <= 1000:
            raise ValueError("Ek gönderim payı 0–1000 ms arasında olmalı.")
        remaining_seconds = (target_epoch_ms - time.time() * 1000) / 1000
        if remaining_seconds < MINIMUM_PREPARE_SECONDS:
            raise ValueError(f"Hazırlık hedefe en az {MINIMUM_PREPARE_SECONDS} saniye kala yapılmalı.")
        if remaining_seconds > MAXIMUM_PREPARE_SECONDS:
            raise ValueError("NTP ölçümünün taze kalması için Snippet hedefe son 30 dakika içinde hazırlanmalı.")
        return ecrn, scrn, target_epoch_ms, target_label, delay_ms

    def prepare(self) -> None:
        try:
            values = self._read_config_without_clock()
        except ValueError as exc:
            messagebox.showerror("Geçersiz bilgi", str(exc), parent=self)
            return

        if not self.var_dry_run.get():
            confirmed = messagebox.askyesno(
                "Gerçek kayıt",
                "Bu Snippet hedef anda OBS'ye tek bir gerçek kayıt POST'u gönderecek. Devam edilsin mi?",
                parent=self,
            )
            if not confirmed:
                return

        self.btn_prepare.config(state=tk.DISABLED)
        self.var_status.set("NTP saat farkı ölçülüyor…")

        def worker() -> None:
            try:
                estimate = estimate_clock()
                error: Exception | None = None
            except OSError as exc:
                estimate = ClockEstimate(0.0, 1000.0, 0.0, 0)
                error = exc
            self.after(0, lambda: self._finish_prepare(values, estimate, error))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_prepare(
        self,
        values: tuple[tuple[str, ...], tuple[str, ...], int, str, int],
        estimate: ClockEstimate,
        error: Exception | None,
    ) -> None:
        ecrn, scrn, target_epoch_ms, target_label, delay_ms = values
        config = RegistrationConfig(
            ecrn=ecrn,
            scrn=scrn,
            target_epoch_ms=target_epoch_ms,
            target_label=target_label,
            clock_offset_ms=estimate.offset_ms,
            clock_uncertainty_ms=estimate.uncertainty_ms,
            send_delay_ms=delay_ms,
            dry_run=self.var_dry_run.get(),
        )
        self._last_snippet = build_snippet(config)
        self._copy_to_clipboard(self._last_snippet)
        self.btn_prepare.config(state=tk.NORMAL)
        self.btn_copy.config(state=tk.NORMAL)

        if error:
            if not config.dry_run:
                self._last_snippet = None
                self.clipboard_clear()
                self.btn_copy.config(state=tk.DISABLED)
                self.var_status.set("NTP'ye ulaşılamadığı için gerçek kayıt Snippet'i üretilmedi.")
                messagebox.showerror("NTP ölçülemedi", str(error), parent=self)
                return
            self.var_status.set(
                "Kuru prova Snippet'i yerel saatle kopyalandı; NTP'ye ulaşılamadı."
            )
            messagebox.showwarning("NTP ölçülemedi", str(error), parent=self)
            return

        self.var_status.set(
            f"Snippet panoda. Saat ofseti {estimate.offset_ms:+.2f} ms, "
            f"tahmini belirsizlik ±{estimate.uncertainty_ms:.2f} ms, "
            f"en iyi RTT {estimate.best_round_trip_ms:.2f} ms ({estimate.sample_count} örnek)."
        )

    def _copy_to_clipboard(self, snippet: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(snippet)
        self.update_idletasks()

    def copy_again(self) -> None:
        if not self._last_snippet:
            return
        self._copy_to_clipboard(self._last_snippet)
        self.var_status.set("Snippet tekrar panoya kopyalandı.")


if __name__ == "__main__":
    App().mainloop()
