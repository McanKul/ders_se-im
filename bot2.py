# -*- coding: utf-8 -*-
"""Tek düğmeyle OBS girişi ve hassas zamanlı ders kaydı."""

from __future__ import annotations

import sys
import socket
import statistics
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Iterable

import tkinter as tk
from tkinter import messagebox, ttk

from browser_automation import (
    BrowserAutomation,
    BrowserAutomationError,
    BrowserRegistrationConfig,
    LoginCredentials,
)


NTP_EPOCH_DELTA = 2_208_988_800
NTP_SERVERS = ("time.cloudflare.com", "time.google.com", "pool.ntp.org")
MINIMUM_PREPARE_SECONDS = 180
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
    addresses = socket.getaddrinfo(server, 123, type=socket.SOCK_DGRAM)
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, address in addresses:
        packet = bytearray(48)
        packet[0] = 0x23
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
        leap, mode, stratum = response[0] >> 6, response[0] & 0x07, response[1]
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
    return ClockEstimate(
        offset_ms=offset_ms,
        uncertainty_ms=max(selected[0].round_trip_ms / 2.0, spread),
        best_round_trip_ms=selected[0].round_trip_ms,
        sample_count=len(samples),
    )


def _next_default_registration_day() -> date:
    now = datetime.now()
    today_at_ten = datetime.combine(now.date(), datetime_time(10, 0))
    return now.date() if now < today_at_ten else now.date() + timedelta(days=1)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OBS Ders Kayıt")
        self.geometry("680x590")
        self.minsize(650, 560)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.var_username = tk.StringVar()
        self.var_password = tk.StringVar()
        self.var_add_crns = tk.StringVar()
        self.var_drop_crns = tk.StringVar()
        self.var_date = tk.StringVar(value=_next_default_registration_day().isoformat())
        self.var_time = tk.StringVar(value="10:00:00.000")
        self.var_delay = tk.StringVar(value="0")
        self.var_status = tk.StringVar(value="Bilgileri girip Başlat'a basın.")
        self._automation: BrowserAutomation | None = None
        self._running = False
        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text="OBS otomatik ders kaydı", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )

        ttk.Label(outer, text="Kullanıcı adı").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_username).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(14, 0), pady=5)
        ttk.Label(outer, text="Şifre").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_password, show="●").grid(row=2, column=1, columnspan=2, sticky="ew", padx=(14, 0), pady=5)

        tk.Label(outer, text="ALINACAK / EKLENECEK CRN (ECRN)", fg="#087a3d", font=("Segoe UI", 10, "bold")).grid(
            row=3, column=0, sticky="w", pady=(14, 5)
        )
        ttk.Entry(outer, textvariable=self.var_add_crns).grid(row=3, column=1, columnspan=2, sticky="ew", padx=(14, 0), pady=(14, 5))

        tk.Label(outer, text="BIRAKILACAK / SİLİNECEK CRN (SCRN)", fg="#b42318", font=("Segoe UI", 10, "bold")).grid(
            row=4, column=0, sticky="w", pady=5
        )
        ttk.Entry(outer, textvariable=self.var_drop_crns).grid(row=4, column=1, columnspan=2, sticky="ew", padx=(14, 0), pady=5)

        ttk.Label(outer, text="Hedef tarih").grid(row=5, column=0, sticky="w", pady=(14, 5))
        ttk.Entry(outer, textvariable=self.var_date, width=14).grid(row=5, column=1, sticky="w", padx=(14, 0), pady=(14, 5))
        ttk.Label(outer, text="YYYY-AA-GG").grid(row=5, column=2, sticky="w", padx=(8, 0), pady=(14, 5))
        ttk.Label(outer, text="Hedef saat").grid(row=6, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_time, width=16).grid(row=6, column=1, sticky="w", padx=(14, 0), pady=5)
        ttk.Label(outer, text="SS:DD:SS.mmm").grid(row=6, column=2, sticky="w", padx=(8, 0))
        ttk.Label(outer, text="Ek gönderim payı").grid(row=7, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.var_delay, width=8).grid(row=7, column=1, sticky="w", padx=(14, 0), pady=5)
        ttk.Label(outer, text="ms (önerilen: 0)").grid(row=7, column=2, sticky="w", padx=(8, 0))

        buttons = ttk.Frame(outer)
        buttons.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(18, 14))
        self.btn_start = ttk.Button(buttons, text="Başlat", command=self.start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_cancel = ttk.Button(buttons, text="İptal", command=self.cancel, state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.LEFT, padx=8)

        status_box = ttk.LabelFrame(outer, text="Durum", padding=12)
        status_box.grid(row=9, column=0, columnspan=3, sticky="nsew")
        outer.rowconfigure(9, weight=1)
        ttk.Label(status_box, textvariable=self.var_status, wraplength=610, justify=tk.LEFT).pack(anchor="w")
        ttk.Label(
            outer,
            text="Başlatınca tarayıcı ve giriş otomatik yönetilir. Son 30 saniyede kontrol/ping yapılmaz; hedefte tek istek çıkar.",
            wraplength=620,
            foreground="#555555",
            justify=tk.LEFT,
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _read_form(self) -> tuple[LoginCredentials, tuple[str, ...], tuple[str, ...], int, str, int]:
        username, password = self.var_username.get().strip(), self.var_password.get()
        if not username or not password:
            raise ValueError("Kullanıcı adı ve şifre zorunlu.")
        add_crns = parse_crns(self.var_add_crns.get())
        drop_crns = parse_crns(self.var_drop_crns.get())
        if not add_crns and not drop_crns:
            raise ValueError("En az bir alınacak veya bırakılacak CRN girmelisiniz.")
        overlap = set(add_crns).intersection(drop_crns)
        if overlap:
            raise ValueError("Aynı CRN hem alınacak hem bırakılacak olamaz: " + ", ".join(sorted(overlap)))
        target_epoch_ms, target_label = parse_target(self.var_date.get(), self.var_time.get())
        try:
            delay_ms = int(self.var_delay.get().strip())
        except ValueError as exc:
            raise ValueError("Ek gönderim payı tam sayı olmalı.") from exc
        if not 0 <= delay_ms <= 1000:
            raise ValueError("Ek gönderim payı 0–1000 ms arasında olmalı.")
        remaining = (target_epoch_ms - time.time() * 1000) / 1000
        if remaining < MINIMUM_PREPARE_SECONDS:
            raise ValueError(f"Başlatma hedefe en az {MINIMUM_PREPARE_SECONDS // 60} dakika kala yapılmalı.")
        if remaining > MAXIMUM_PREPARE_SECONDS:
            raise ValueError("Saat ölçümünün taze kalması için hedefe son 30 dakika içinde başlatın.")
        return LoginCredentials(username, password), add_crns, drop_crns, target_epoch_ms, target_label, delay_ms

    def start(self) -> None:
        if self._running:
            return
        try:
            credentials, add_crns, drop_crns, target_epoch_ms, target_label, delay_ms = self._read_form()
        except ValueError as exc:
            messagebox.showerror("Geçersiz bilgi", str(exc), parent=self)
            return

        add_text = ", ".join(add_crns) or "YOK"
        drop_text = ", ".join(drop_crns) or "YOK"
        confirmed = messagebox.askyesno(
            "Ders listesini son kez kontrol edin",
            "ALINACAK / EKLENECEK (ECRN):\n"
            f"{add_text}\n\n"
            "BIRAKILACAK / SİLİNECEK (SCRN):\n"
            f"{drop_text}\n\n"
            f"Hedef: {target_label}\n\n"
            "Bu eşleme doğru mu?",
            icon="warning",
            parent=self,
        )
        if not confirmed:
            return

        self._running = True
        self.var_password.set("")
        self.btn_start.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.NORMAL)
        self.var_status.set("NTP saat farkı ölçülüyor…")

        def worker() -> None:
            automation: BrowserAutomation | None = None
            try:
                estimate = estimate_clock()
                config = BrowserRegistrationConfig(
                    add_crns=add_crns,
                    drop_crns=drop_crns,
                    target_epoch_ms=target_epoch_ms,
                    target_label=target_label,
                    clock_offset_ms=estimate.offset_ms,
                    clock_uncertainty_ms=estimate.uncertainty_ms,
                    send_delay_ms=delay_ms,
                    dry_run=False,
                )
                self._set_status(
                    f"Saat farkı {estimate.offset_ms:+.2f} ms ölçüldü. Tarayıcı hazırlanıyor…"
                )
                automation = BrowserAutomation(self._set_status)
                self._automation = automation
                automation.run(credentials, config)
            except (OSError, BrowserAutomationError) as exc:
                self._show_error(str(exc))
            finally:
                if automation:
                    automation.close()
                self._automation = None
                self.after(0, self._finish_run)

        threading.Thread(target=worker, daemon=True).start()

    def _set_status(self, message: str) -> None:
        self.after(0, lambda: self.var_status.set(message))

    def _show_error(self, message: str) -> None:
        def show() -> None:
            self.var_status.set(message)
            messagebox.showerror("İşlem tamamlanamadı", message, parent=self)
        self.after(0, show)

    def _finish_run(self) -> None:
        self._running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_cancel.config(state=tk.DISABLED)

    def cancel(self) -> None:
        if self._automation:
            self._automation.cancel()
        self.var_status.set("İptal ediliyor…")
        self.btn_cancel.config(state=tk.DISABLED)

    def on_close(self) -> None:
        if self._automation:
            self._automation.cancel()
        self.destroy()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                # "chromium" kanalı normal gömülü Chromium'un yeni headless
                # modunu kullanır; ayrı headless-shell paketine ihtiyaç duymaz.
                browser = playwright.chromium.launch(headless=True, channel="chromium")
                page = browser.new_page()
                page.set_content("<title>OBS Bot Self Test</title><h1>ok</h1>")
                passed = page.title() == "OBS Bot Self Test" and page.locator("h1").inner_text() == "ok"
                browser.close()
            raise SystemExit(0 if passed else 1)
        except Exception:
            raise SystemExit(1)
    App().mainloop()
