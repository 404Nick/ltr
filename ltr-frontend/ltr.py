#!/usr/bin/env python3
"""ltr — локальный переводчик с TUI-интерфейсом (Textual + Argos Translate).

Установка на Arch Linux:
    sudo pacman -S --needed python wl-clipboard      # wl-clipboard или xclip — для копирования/вставки
    python -m venv ~/.local/share/ltr-venv
    ~/.local/share/ltr-venv/bin/pip install argostranslate textual
    install -Dm755 ltr ~/.local/bin/ltr
    sed -i '1s|.*|#!'"$HOME"'/.local/share/ltr-venv/bin/python|' ~/.local/bin/ltr

Запуск:
    ltr                 # открыть интерфейс
    ltr "Hello world"   # открыть с готовым текстом

Клавиши:
    Ctrl+T  перевести сейчас (обычно перевод идёт сам, пока вы печатаете)
    F2      поменять языки местами
    F3      скопировать перевод
    F4      скачать / управлять языковыми пакетами
    F5      очистить
    F6      вставить текст из буфера обмена
    Ctrl+Q  выход
"""
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

import argostranslate.package as pkg
import argostranslate.translate as tr
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (Button, Footer, Header, Input, OptionList, Select,
                             Static, TextArea)
from textual.widgets.option_list import Option

CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
DEBOUNCE = 0.6  # секунд после последнего нажатия клавиши
LOG_PATH = os.path.expanduser("~/.cache/ltr-error.log")
NO_LANGS = [("нет языков — нажмите F4", "none")]


class UserError(Exception):
    """Ожидаемая ошибка (нет языка/пакета): показываем в статусе, в лог не пишем."""


def log_exception():
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(time.strftime("%F %T\n") + traceback.format_exc() + "\n")
    except OSError:
        pass


def call_ui(app, fn, *args):
    """call_from_thread, не роняющий рабочий поток, если окно или приложение
    уже закрыты (например, диалог закрыли посреди загрузки)."""
    try:
        app.call_from_thread(fn, *args)
    except Exception:  # noqa: BLE001
        log_exception()


# ---------- работа с Argos ----------

def installed_languages(refresh=False):
    # Кэш сбрасываем ТОЛЬКО после установки пакетов. Если сбрасывать его на каждый
    # перевод, Argos заново читает и загружает модели с диска при каждом вызове.
    if refresh:
        clear = getattr(tr.get_installed_languages, "cache_clear", None)
        if clear:
            clear()
    return tr.get_installed_languages()


def lang_options(refresh=False):
    opts = [(f"{l.name} ({l.code})", l.code) for l in installed_languages(refresh)]
    return sorted(opts) or list(NO_LANGS)


def has_langs(opts):
    return opts != NO_LANGS


def pick_dst(values, prev=None):
    if prev in values:
        return prev
    for code in ("ru", "en"):
        if code in values:
            return code
    return next(iter(values))


def translate_text(text, src, dst):
    if src == "auto":
        src = "ru" if CYRILLIC.search(text) else "en"
    if src == dst:
        return text, src
    langs = {l.code: l for l in installed_languages()}
    if src not in langs or dst not in langs:
        missing = src if src not in langs else dst
        raise UserError(f"язык «{missing}» не установлен — нажмите F4")
    t = langs[src].get_translation(langs[dst])
    if t is None:
        raise UserError(f"нет пакета для {src} → {dst} — нажмите F4")
    lines = [t.translate(ln) if ln.strip() else ln for ln in text.split("\n")]
    return "\n".join(lines), src


# ---------- окно установки языков ----------

class InstallScreen(ModalScreen):
    BINDINGS = [Binding("escape", "close", "Закрыть")]

    def __init__(self):
        super().__init__()
        self.packages = []
        self.installed = set()
        self._install_running = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Языковые пакеты", id="dlg-title")
            yield Input(placeholder="Фильтр: ru, english, german…", id="filter")
            yield OptionList(id="list")
            yield Static("Загружаю список… (нужен интернет)", id="info")

    def on_mount(self):
        self.query_one("#filter").focus()
        self.load_index()

    @work(thread=True, exit_on_error=False)
    def load_index(self):
        app = self.app  # берём заранее: у закрытого экрана .app уже недоступен
        try:
            pkg.update_package_index()
            unique = {(p.from_code, p.to_code): p for p in pkg.get_available_packages()}
            self.packages = [unique[k] for k in sorted(unique)]
            self.installed = {(p.from_code, p.to_code) for p in pkg.get_installed_packages()}
        except Exception as e:  # noqa: BLE001
            call_ui(app, self.set_info, f"Не удалось получить список: {e}")
            return
        call_ui(app, self.fill)

    def set_info(self, msg):
        self.query_one("#info", Static).update(Text(msg))

    def fill(self):
        q = self.query_one("#filter", Input).value.lower().strip()
        lst = self.query_one("#list", OptionList)

        keep = None
        if lst.highlighted is not None:
            try:
                keep = lst.get_option_at_index(lst.highlighted).id
            except Exception:  # noqa: BLE001
                keep = None

        options = []
        for p in self.packages:
            label = f"{p.from_name} → {p.to_name}  ({p.from_code}→{p.to_code})"
            if q and q not in label.lower():
                continue
            mark = "✔" if (p.from_code, p.to_code) in self.installed else " "
            options.append(Option(f"{mark} {label}", id=f"{p.from_code}|{p.to_code}"))

        lst.clear_options()
        lst.add_options(options)
        if keep is not None:
            for i, o in enumerate(options):
                if o.id == keep:
                    lst.highlighted = i
                    break
        if options:
            self.set_info("Enter — установить пакет · Esc — закрыть · ✔ — уже установлен")
        else:
            self.set_info("Ничего не найдено")

    @on(Input.Changed, "#filter")
    def _filter(self):
        if self.packages:
            self.fill()

    @on(Input.Submitted, "#filter")
    def _to_list(self):
        lst = self.query_one("#list", OptionList)
        lst.focus()
        if lst.option_count and lst.highlighted is None:
            lst.highlighted = 0

    @on(OptionList.OptionSelected)
    def _pick(self, event: OptionList.OptionSelected):
        src, dst = event.option.id.split("|")
        if (src, dst) in self.installed:
            self.set_info("Уже установлен")
            return
        if self._install_running:
            self.set_info("Дождитесь окончания текущей установки…")
            return
        pkg_ = next(p for p in self.packages if p.from_code == src and p.to_code == dst)
        self._install_running = True
        self.set_info(f"Скачиваю {src}→{dst}…")
        self.install(pkg_)

    @work(thread=True, exit_on_error=False)
    def install(self, p):
        app = self.app
        try:
            pkg.install_from_path(p.download())
        except Exception as e:  # noqa: BLE001
            self._install_running = False
            call_ui(app, self.set_info, f"Ошибка установки: {e}")
            return
        self._install_running = False
        self.installed.add((p.from_code, p.to_code))
        call_ui(app, self.fill)
        call_ui(app, self.set_info, f"✔ Установлено {p.from_code}→{p.to_code}")
        call_ui(app, app.refresh_langs)

    def action_close(self):
        self.dismiss(True)


# ---------- основное приложение ----------

class LtrApp(App):
    TITLE = "ltr"
    SUB_TITLE = "локальный переводчик"

    CSS = """
    #langs { height: 3; padding: 0 1; margin-top: 1; }
    #langs Select { width: 1fr; }
    #swap { min-width: 7; width: 7; margin: 0 1; }
    #panes { height: 1fr; padding: 0 1; }
    #panes TextArea { width: 1fr; height: 100%; border: round $primary; }
    #panes #input:focus { border: round $accent; }
    #panes #output { border: round $success; }
    #status { height: 1; padding: 0 2; color: $text-muted; }
    InstallScreen { align: center middle; }
    #dialog { width: 72; height: 28; max-width: 100%; max-height: 100%; padding: 1 2; border: thick $primary; background: $surface; }
    #dlg-title { text-style: bold; margin-bottom: 1; }
    #list { height: 1fr; margin: 1 0; border: round $primary-darken-2; }
    """

    BINDINGS = [
        Binding("ctrl+t", "translate", "Перевести"),
        Binding("f2", "swap", "⇄ Языки"),
        Binding("f3", "copy", "Копировать"),
        Binding("f4", "install", "Пакеты"),
        Binding("f5", "clear", "Очистить"),
        Binding("f6", "paste", "Вставить"),
        Binding("ctrl+q", "quit", "Выход"),
    ]

    def __init__(self, initial=""):
        super().__init__()
        self.initial = initial
        self._timer = None
        self._seq = 0
        self._last_src = "en"
        self._lang_opts = None

    def compose(self) -> ComposeResult:
        opts = lang_options()
        self._lang_opts = opts
        codes = [c for _, c in opts]
        yield Header()
        with Horizontal(id="langs"):
            yield Select([("Авто", "auto")] + (opts if has_langs(opts) else []),
                         value="auto", allow_blank=False, id="src")
            yield Button("⇄", id="swap", variant="primary")
            yield Select(opts, value=pick_dst(codes), allow_blank=False, id="dst")
        with Horizontal(id="panes"):
            yield TextArea(id="input")
            yield TextArea(id="output", read_only=True)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self):
        self.query_one("#input").border_title = "Оригинал"
        self.query_one("#output").border_title = "Перевод"
        self.query_one("#input").focus()
        if self.initial:
            self.query_one("#input", TextArea).load_text(self.initial)
        if not has_langs(self._lang_opts):
            self.set_status("Языки не установлены. Открываю менеджер пакетов…")
            self.action_install()
        else:
            self.set_status("Начните печатать — перевод появится автоматически")

    def check_action(self, action, parameters):
        stack = self.screen_stack
        if action != "quit" and stack and isinstance(stack[-1], ModalScreen):
            return False
        return True

    # --- служебное ---
    def set_status(self, msg, error=False):
        s = self.query_one("#status", Static)
        s.update(Text(msg, style="bold red" if error else ""))

    def installed_codes(self):
        return {c for _, c in (self._lang_opts or []) if c != "none"}

    def refresh_langs(self):
        opts = lang_options(refresh=True)
        if opts == self._lang_opts:
            return
        self._lang_opts = opts
        src = self.query_one("#src", Select)
        dst = self.query_one("#dst", Select)
        prev_s, prev_d = src.value, dst.value
        codes = [c for _, c in opts]
        real = opts if has_langs(opts) else []
        src.set_options([("Авто", "auto")] + real)
        dst.set_options(opts)
        src.value = prev_s if prev_s in ["auto"] + codes else "auto"
        dst.value = pick_dst(codes, prev_d)

    def stop_timer(self):
        if self._timer:
            self._timer.stop()
            self._timer = None

    def schedule(self):
        self.stop_timer()
        self._timer = self.set_timer(DEBOUNCE, self.action_translate)

    # --- события ---
    @on(TextArea.Changed, "#input")
    def _text_changed(self):
        self.schedule()

    @on(Select.Changed)
    def _select_changed(self):
        self.schedule()

    @on(Button.Pressed, "#swap")
    def _swap_pressed(self):
        self.action_swap()

    # --- действия ---
    def action_translate(self):
        self.stop_timer()
        self._seq += 1
        text = self.query_one("#input", TextArea).text.strip()
        out = self.query_one("#output", TextArea)
        if not text:
            out.load_text("")
            self.set_status("Введите текст слева")
            return
        self.set_status("Перевожу…")
        self.run_translation(self._seq, text,
                             self.query_one("#src", Select).value,
                             self.query_one("#dst", Select).value)

    @work(thread=True, exclusive=True, exit_on_error=False)
    def run_translation(self, seq, text, src, dst):
        t0 = time.monotonic()
        try:
            result, used = translate_text(text, src, dst)
        except UserError as e:
            call_ui(self, self._show_error, seq, str(e))
            return
        except Exception as e:  # noqa: BLE001
            log_exception()
            call_ui(self, self._show_error, seq, f"{e}  (подробности: {LOG_PATH})")
            return
        call_ui(self, self._show_result, seq, result, used, dst, time.monotonic() - t0)

    def _show_result(self, seq, result, used, dst, dt):
        if seq != self._seq:
            return
        self._last_src = used
        self.query_one("#output", TextArea).load_text(result)
        auto = " (определён автоматически)" if self.query_one("#src", Select).value == "auto" else ""
        self.set_status(f"{used} → {dst}{auto} · {dt:.1f} с")

    def _show_error(self, seq, msg):
        if seq == self._seq:
            self.set_status(msg, error=True)

    def action_swap(self):
        src = self.query_one("#src", Select)
        dst = self.query_one("#dst", Select)
        inp = self.query_one("#input", TextArea)
        out = self.query_one("#output", TextArea)
        new_src = dst.value
        new_dst = src.value if src.value != "auto" else self._last_src
        if new_dst not in self.installed_codes() or new_src not in self.installed_codes():
            self.set_status("Для обмена нужны оба языка — установите их (F4)", error=True)
            return
        translated = out.text
        src.value, dst.value = new_src, new_dst
        if translated:
            inp.load_text(translated)

    def action_copy(self):
        text = self.query_one("#output", TextArea).text
        if not text:
            self.notify("Нечего копировать", severity="warning")
            return
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
            if not shutil.which(cmd[0]):
                continue
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=3,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               close_fds=False)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            self.notify("Перевод скопирован")
            return
        try:
            self.copy_to_clipboard(text)
            self.notify("Перевод скопирован (через терминал)")
        except Exception as e:  # noqa: BLE001
            self.notify(f"Не удалось скопировать: {e}", severity="error")

    def action_paste(self):
        """Вставляет текст из буфера обмена (заменяя текущий текст)"""
        text = ""
        # Пытаемся получить текст через wl-paste (Wayland) или xclip (X11)
        for cmd in (["wl-paste"], ["xclip", "-o", "-selection", "clipboard"]):
            if not shutil.which(cmd[0]):
                continue
            try:
                text = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                break
            except (OSError, ValueError, subprocess.SubprocessError):
                continue

        if not text:
            self.notify("Буфер обмена пуст или недоступен", severity="warning")
            return

        inp = self.query_one("#input", TextArea)
        inp.load_text(text.strip())  # Полностью заменяем текущий текст на новый
        self.notify("Текст вставлен")
        inp.focus()
        self.schedule()  # Запускаем таймер перевода

    def action_clear(self):
        self.stop_timer()
        self._seq += 1  # отбросить перевод, который ещё считается
        self.query_one("#input", TextArea).load_text("")
        self.query_one("#output", TextArea).load_text("")
        self.set_status("Введите текст слева")
        self.query_one("#input").focus()

    def action_install(self):
        self.push_screen(InstallScreen(), lambda _: (self.refresh_langs(), self.schedule()))


def prestart_mp_helpers():
    try:
        from multiprocessing import resource_tracker
        resource_tracker.ensure_running()
    except Exception:  # noqa: BLE001
        pass
    try:
        import multiprocessing
        if multiprocessing.get_start_method() == "forkserver":
            from multiprocessing import forkserver
            forkserver.ensure_running()
    except Exception:  # noqa: BLE001
        pass


def patch_os_inheritable():
    import os
    orig_set_inheritable = os.set_inheritable

    def safe_set_inheritable(fd, inheritable):
        if fd >= 0:
            orig_set_inheritable(fd, inheritable)

    os.set_inheritable = safe_set_inheritable


patch_os_inheritable()

if __name__ == "__main__":
    prestart_mp_helpers()
    LtrApp(" ".join(sys.argv[1:])).run()
