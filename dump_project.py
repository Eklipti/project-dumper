# Prject Dumper GUI
# Copyright (C) 2025 Eklipti
#
# Этот проект — свободное программное обеспечение: вы можете
# распространять и/или изменять его на условиях
# Стандартной общественной лицензии GNU (GNU GPL)
# третьей версии, опубликованной Фондом свободного ПО.
#
# Программа распространяется в надежде, что она будет полезной,
# но БЕЗ КАКИХ-ЛИБО ГАРАНТИЙ; даже без подразумеваемой гарантии
# ТОВАРНОГО СОСТОЯНИЯ или ПРИГОДНОСТИ ДЛЯ КОНКРЕТНОЙ ЦЕЛИ.
# Подробности см. в Стандартной общественной лицензии GNU.
#
# Вы должны были получить копию Стандартной общественной
# лицензии GNU вместе с этой программой. Если это не так,
# см. <https://www.gnu.org/licenses/>.

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from fnmatch import fnmatch

# ------------------ Настройки ------------------
DEFAULT_MAX_SIZE = 1_000_000  # 1 МБ
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".svn", ".hg", ".idea", ".vscode",
    "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", ".venv", "venv", "build", "dist",
}
DEFAULT_EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}
IGNORE_FILES = [".gitignore", ".dockerignore"]

DEFAULT_TREE_HIDE_LIST = """# Папки (должны заканчиваться на /)
.venv/
__pycache__/
.git/
node_modules/

# Файлы
.DS_Store
Thumbs.db
"""

# ------------------ Утилиты ------------------
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def is_probably_text(path: Path, sample_size: int = 4096) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
    except OSError:
        return False
    if not chunk:
        return True
    if b"\x00" in chunk:
        return False
    text_bytes = set(range(32, 256)) | {9, 10, 13}
    nontext = sum(b not in text_bytes for b in chunk)
    return (nontext / len(chunk)) < 0.30

def try_read_text(path: Path):
    encodings = ("utf-8", "utf-8-sig", "cp1251", "latin-1")
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return True, f.read()
        except Exception:
            continue
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return True, f.read()
    except Exception:
        return False, ""

def read_ignore_patterns(base: Path):
    patterns = []
    found = []
    for name in IGNORE_FILES:
        p = base / name
        if p.exists():
            found.append(name)
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                patterns.append(s)
    return patterns, found

def match_ignore(rel_posix: str, patterns: list[str]) -> bool:
    # - пустые/комментарии пропущены заранее
    # - ведущий "/" = от корня (сравниваем как есть)
    # - иначе - сопоставляем именем файла и путём
    # - "!" (negation) поддержим базово: последний победитель
    matched = False
    name = Path(rel_posix).name
    for pat in patterns:
        neg = pat.startswith("!")
        pat_eff = pat[1:] if neg else pat
        pat_clean = pat_eff.strip('/')
        if pat_eff.startswith("/"):
            # fnmatch('venv', 'venv')
            ok = fnmatch(rel_posix, pat_clean)
        else:
            # fnmatch('scripts/__pycache__', '**/__pycache__') -> True
            # fnmatch('__pycache__', '**/__pycache__') -> False
            # fnmatch('venv', 'venv') -> True
            ok = fnmatch(rel_posix, pat_clean) or fnmatch(name, pat_clean)
        if ok:
            matched = not neg    
    return matched

def build_ascii_tree(root: Path, filter_func=None) -> str:
    """
    Функция, принимающая Path. Если возвращает True, путь пропускается.
    """
    lines = [f"{root.name}/"]

    def list_dir(path: Path):
        try:
            items = list(path.iterdir())
        except OSError:
            return [], []
        
        if filter_func:
            items = [p for p in items if not filter_func(p)]

        dirs = [p.name for p in items if p.is_dir()]
        files = [p.name for p in items if p.is_file()]
        return sorted(dirs), sorted(files)

    def walk(path: Path, prefix: str):
        dirs, files = list_dir(path)
        entries = [("d", d) for d in dirs] + [("f", f) for f in files]
        for i, (kind, name) in enumerate(entries):
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(prefix + connector + (name + ("/" if kind == "d" else "")))
            if kind == "d":
                extension = "    " if i == len(entries) - 1 else "│   "
                walk(path / name, prefix + extension)
    walk(root, "")
    return "\n".join(lines)

# ------------------ GUI ------------------
class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=5)
        self.master.title("Project Dumper GUI")
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        # Состояние
        self.program_dir = app_dir()
        self.project_dir = tk.StringVar(value=str(self.program_dir))
        default_out = self.program_dir / "dump.txt"
        self.output_path = tk.StringVar(value=str(default_out))
        self.max_bytes = tk.IntVar(value=DEFAULT_MAX_SIZE)
        self.ignore_patterns: list[str] = []
        self.ignored_paths: set[str] = set()
        self.found_ignore_files: list[str] = []
        self.tree_built_for: Path | None = None

        # Режим отображения дерева
        self.tree_mode = tk.StringVar(value="show_all") # show_all, hide_ignored, hide_custom

        # Создаем вкладки
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Вкладка 1: Главная
        self.tab_main = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(self.tab_main, text="Главная")

        # Вкладка 2: Настройки дерева (скрытие)
        self.tab_tree_settings = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(self.tab_tree_settings, text="Скрытые в дереве")

        # Строим UI
        self._build_main_tab()
        self._build_settings_tab()

        # Привязка смены вкладок: при переходе на "Скрытые в дереве"
        # принудительно даём окну и текстовому полю клавиатурный фокус
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Инициализация
        self._build_tree_view()

    def _on_tab_changed(self, event):
        """Обработчик смены вкладки: управляем фокусом."""
        try:
            current_tab_index = self.notebook.index(self.notebook.select())
            if current_tab_index == 1:
                # Сначала фокус на окно, затем на текст
                self.master.after(50, self.master.focus_force)
                self.master.after(80, lambda: self.txt_custom_ignore.focus_set())
        except Exception:
            pass

        self.master.bind("<F12>", self._debug_focus)

    def _debug_focus(self, event=None):
        w = self.master.focus_get()
        messagebox.showinfo("Focus debug", repr(w))

    def _build_main_tab(self):
        # Верхняя панель (выбор пути)
        top = ttk.Frame(self.tab_main)
        top.grid(row=0, column=0, sticky="we", pady=(0, 8))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Папка проекта:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(top, textvariable=self.project_dir)
        entry.grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(top, text="Выбрать…", command=self._select_dir).grid(row=0, column=2)

        ttk.Label(top, text="Файл вывода:").grid(row=1, column=0, sticky="w", pady=(5,0))
        entry_out = ttk.Entry(top, textvariable=self.output_path)
        entry_out.grid(row=1, column=1, sticky="we", padx=6, pady=(5,0))
        ttk.Button(top, text="Сохранить как…", command=self._select_out_file).grid(row=1, column=2, pady=(5,0))

        # Настройки дампа
        opts = ttk.Frame(self.tab_main)
        opts.grid(row=1, column=0, sticky="we", pady=(0, 8))
        ttk.Label(opts, text="Макс. размер (байт):").pack(side="left")
        ttk.Spinbox(opts, from_=0, to=10_000_000_000, textvariable=self.max_bytes, width=10).pack(side="left", padx=6)
        
        ttk.Button(opts, text="Анализ (обновить)", command=self._build_tree_view).pack(side="right", padx=6)

        # Настройки отображения дерева
        tree_opts_frame = ttk.LabelFrame(self.tab_main, text="Отображение дерева в дампе", padding=5)
        tree_opts_frame.grid(row=2, column=0, sticky="we", pady=(0, 8))
        
        ttk.Radiobutton(tree_opts_frame, text="Показывать всё (даже игнорируемые)", 
                        variable=self.tree_mode, value="show_all").pack(side="left", padx=5)
        ttk.Radiobutton(tree_opts_frame, text="Скрывать игнорируемые файлы", 
                        variable=self.tree_mode, value="hide_ignored").pack(side="left", padx=5)
        ttk.Radiobutton(tree_opts_frame, text="Скрывать только из списка (см. вкладку)", 
                        variable=self.tree_mode, value="hide_custom").pack(side="left", padx=5)

        # Дерево файлов
        mid = ttk.Frame(self.tab_main)
        mid.grid(row=3, column=0, sticky="nsew")
        self.tab_main.columnconfigure(0, weight=1)
        self.tab_main.rowconfigure(3, weight=1)

        self.tree = ttk.Treeview(mid, columns=("rel", "ignored"), show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="Имя")
        self.tree.heading("rel", text="Отн. путь")
        self.tree.heading("ignored", text="Игнор?")
        self.tree.column("#0", width=300, stretch=True)
        self.tree.column("rel", width=300, stretch=True)
        self.tree.column("ignored", width=60, anchor="center", stretch=False)
        
        # Скроллбар
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._toggle_ignore_on_event)
        self.tree.bind("<space>", self._toggle_ignore_on_event)
        self._make_context_menu()

        # Кнопки управления игнором
        btns = ttk.Frame(self.tab_main)
        btns.grid(row=4, column=0, sticky="we", pady=8)
        ttk.Button(btns, text="Игнорировать", command=lambda: self._set_ignore_selected(True)).pack(side="left", padx=4)
        ttk.Button(btns, text="Снять игнор", command=lambda: self._set_ignore_selected(False)).pack(side="left", padx=4)
        self.btn_apply_ignores = ttk.Button(btns, text="Применить .gitignore", command=self._apply_ignore_files)
        self.btn_apply_ignores.pack(side="left", padx=12)

        # Дамп
        bottom = ttk.Frame(self.tab_main)
        bottom.grid(row=5, column=0, sticky="we")
        ttk.Button(bottom, text="СДЕЛАТЬ ДАМП", command=self._dump).pack(side="left", padx=4)
        ttk.Button(bottom, text="Выход", command=self.master.destroy).pack(side="right", padx=4)

    def _build_settings_tab(self):
        lbl = ttk.Label(self.tab_tree_settings, 
                        text="""Список файлов и папок, которые будут скрыты из ASCII-дерева,
если выбран режим "Скрывать только из списка".
Один путь на строку. Папки должны заканчиваться на '/'.
Не поддерживается формат .gitignore.""")
        lbl.pack(anchor="w", pady=(0, 5))

        self.txt_custom_ignore = tk.Text(
            self.tab_tree_settings,
            height=15,
            width=60,
            undo=True,
            exportselection=0,
        )
        # Поле должно быть полностью редактируемым
        self.txt_custom_ignore.configure(state="normal")
        self.txt_custom_ignore.pack(fill="both", expand=True)
        self.txt_custom_ignore.insert("1.0", DEFAULT_TREE_HIDE_LIST)
        self.txt_custom_ignore.bind(
            "<Button-1>",
            lambda event: self.txt_custom_ignore.focus_set(),
            add="+",
        )

    # -------- Контекстное меню --------
    def _make_context_menu(self):
        self.ctx = tk.Menu(self.tree, tearoff=0)
        self.ctx.add_command(label="Игнорировать", command=lambda: self._set_ignore_selected(True))
        self.ctx.add_command(label="Снять игнор", command=lambda: self._set_ignore_selected(False))
        self.tree.bind("<Button-3>", self._popup_ctx)

    def _popup_ctx(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.ctx.tk_popup(event.x_root, event.y_root)

    # -------- Логика дерева (GUI) --------
    def _select_dir(self):
        path = filedialog.askdirectory(initialdir=self.project_dir.get())
        if path:
            self.project_dir.set(path)
            self._build_tree_view()

    def _select_out_file(self):
        # Берем текущий путь из переменной
        current_val = self.output_path.get()
        if current_val:
            initial_dir = os.path.dirname(current_val)
            initial_file = os.path.basename(current_val)
        else:
            initial_dir = str(self.program_dir)
            initial_file = "dump.txt"
        
        path = filedialog.asksaveasfilename(
            initialdir=initial_dir,
            initialfile=initial_file,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("Markdown files", "*.md"), ("All files", "*.*")]
        )
        if path:
            self.output_path.set(path)
    def _build_tree_view(self):
        base = Path(self.project_dir.get() or ".").resolve()
        if not base.is_dir():
            messagebox.showerror("Ошибка", "Выберите корректную директорию проекта.")
            return

        out_path_str = self.output_path.get()
        if not out_path_str:
            messagebox.showerror("Ошибка", "Не указан путь для сохранения.")
            return

        out_path = Path(out_path_str).resolve()
        if not out_path.parent.exists():
             messagebox.showerror("Ошибка", f"Папка не существует:\n{out_path.parent}")
             return

        # Очистка
        self.tree.delete(*self.tree.get_children())
        self.ignored_paths.clear()
        
        # Дефолтные игноры
        for d in DEFAULT_EXCLUDE_DIRS:
            self.ignored_paths.add(d) 
        for f in DEFAULT_EXCLUDE_FILES:
            self.ignored_paths.add(f)
        self.tree_built_for = base
        
        # Проверка .gitignore
        self.ignore_patterns, self.found_ignore_files = read_ignore_patterns(base)
        if self.found_ignore_files:
            if messagebox.askyesno(
                "Найдены ignore-файлы",
                f"Обнаружены: {', '.join(self.found_ignore_files)}.\n"
                f"Применить их правила к списку игнорирования?"
            ):
                pass 
            else:
                self.ignore_patterns = []

        # Рекурсивная вставка в Treeview
        def insert_dir(parent_iid, dir_path: Path):
            # Сортировка: папки сверху
            for child in sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                rel = child.relative_to(base).as_posix()
                iid = self.tree.insert(parent_iid, "end", text=child.name, values=(rel, "—"))
                if child.is_dir():
                    insert_dir(iid, child)

        root_iid = self.tree.insert("", "end", text=base.name + "/", values=(".", "—"), open=True)
        insert_dir(root_iid, base)
        self.tree.item(root_iid, open=True)

        # Применение игноров
        if self.ignore_patterns or DEFAULT_EXCLUDE_DIRS:
            self._apply_ignore_files(mark_only=True)
            
            def update_gui_for_defaults(iid):
                rel = self.tree.set(iid, "rel")
                if rel and (rel in DEFAULT_EXCLUDE_DIRS or rel in DEFAULT_EXCLUDE_FILES):
                    self._set_ignore_recursive(iid, True)
                for child in self.tree.get_children(iid):
                    update_gui_for_defaults(child)
            
            for root in self.tree.get_children(""):
                update_gui_for_defaults(root)

    # -------- Игнор (GUI методы) --------
    def _toggle_ignore_on_event(self, event=None):
        for iid in self.tree.selection():
            rel = self.tree.set(iid, "rel")
            if not rel:
                continue
            make_ignored = rel not in self.ignored_paths
            self._set_ignore_recursive(iid, make_ignored)

    def _set_ignore_selected(self, value: bool):
        for iid in self.tree.selection():
            self._set_ignore_recursive(iid, value)

    def _set_ignore_recursive(self, iid, value: bool):
        rel = self.tree.set(iid, "rel")
        if rel:
            if value:
                self.ignored_paths.add(rel)
                self.tree.set(iid, "ignored", "✓")
            else:
                self.ignored_paths.discard(rel)
                self.tree.set(iid, "ignored", "—")
        for child in self.tree.get_children(iid):
            self._set_ignore_recursive(child, value)

    def _apply_ignore_files(self, mark_only: bool = False):
        if not self.tree_built_for:
            return
        if not self.ignore_patterns:
            if not mark_only:
                messagebox.showinfo("Нет правил", "Ignore-файлы не найдены или правила пусты.")
            return 

        def walk(iid):
            rel = self.tree.set(iid, "rel")
            if rel and match_ignore(rel if rel != "." else "", self.ignore_patterns):
                self.ignored_paths.add(rel)
                self.tree.set(iid, "ignored", "✓")
            for child in self.tree.get_children(iid):
                walk(child)

        for root in self.tree.get_children(""):
            walk(root)

        # Каскадное применение
        def propagate(iid, parent_ignored: bool):
            rel = self.tree.set(iid, "rel")
            curr_ignored = parent_ignored or (rel in self.ignored_paths if rel else False)
            if rel:
                if curr_ignored:
                    self.ignored_paths.add(rel)
                    self.tree.set(iid, "ignored", "✓")
                else:
                    self.tree.set(iid, "ignored", "—")
            for child in self.tree.get_children(iid):
                propagate(child, curr_ignored)

        for root in self.tree.get_children(""):
            propagate(root, False)

        if not mark_only:
            messagebox.showinfo("Готово", "Правила .gitignore/.dockerignore применены.")

    def _is_path_ignored(self, rel_path: str) -> bool:
        if rel_path in self.ignored_paths:
            return True
        # Проверяем, не игнорируется ли один из его родителей
        parts = rel_path.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if parent in self.ignored_paths:
                return True
        return False

    # -------- Дамп --------
    def _dump(self):
        base = Path(self.project_dir.get()).resolve()
        if not base.is_dir():
            messagebox.showerror("Ошибка", "Выберите корректную директорию проекта.")
            return

        out_path_str = self.output_path.get()
        if not out_path_str:
            messagebox.showerror("Ошибка", "Не указан путь для сохранения.")
            return

        out_path = Path(out_path_str).resolve()
        
        if not out_path.parent.exists():
             messagebox.showerror("Ошибка", f"Папка не существует:\n{out_path.parent}")
             return

        max_bytes = max(0, int(self.max_bytes.get()))
        mode = self.tree_mode.get()

        custom_hide_dirs = set()
        custom_hide_files = set()
        if mode == "hide_custom":
            raw_lines = self.txt_custom_ignore.get("1.0", "end").splitlines()
            for line in raw_lines:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.endswith("/"):
                    custom_hide_dirs.add(s.rstrip("/"))
                else:
                    custom_hide_files.add(s)

        def tree_filter(path: Path) -> bool:
            try:
                rel = path.relative_to(base).as_posix()
            except ValueError:
                return True
            
            if mode == "show_all":
                return False
            elif mode == "hide_ignored":
                return self._is_path_ignored(rel)
            elif mode == "hide_custom":
                if path.is_dir():
                    return path.name in custom_hide_dirs
                else:
                    return path.name in custom_hide_files
            return False

        def is_ignored_for_content(path: Path, is_dir: bool) -> bool:
            try:
                rel = path.relative_to(base).as_posix()
            except ValueError:
                return True

            # БАЗОВОЕ ПРАВИЛО: Стандартные игноры (.git, .venv) ВСЕГДА скрываем из контента,
            if self._is_path_ignored(rel):
                return True

            if mode == "hide_custom":
                name = path.name
                if is_dir:
                    return name in custom_hide_dirs
                else:
                    return name in custom_hide_files
            
            return False

        tree_txt = build_ascii_tree(base, filter_func=tree_filter)

        try:
            with open(out_path, "w", encoding="utf-8", newline="\n") as out:
                out.write(f"# Project dump\n")
                out.write(f"Root: {base}\n\n")
                out.write("## Directory tree\n\n")
                out.write(tree_txt)
                out.write("\n\n## Files by directory\n")

                for dirpath, dirnames, filenames in os.walk(base, topdown=True):
                    rel_dir = Path(dirpath).relative_to(base)
                    rel_dir_posix = rel_dir.as_posix() if str(rel_dir) != "." else "."

                    # Оставляем только те папки, которые НЕ игнорируются
                    dirnames[:] = [d for d in sorted(dirnames) 
                                   if not is_ignored_for_content(Path(dirpath) / d, is_dir=True)]

                    if rel_dir_posix != "." and is_ignored_for_content(Path(dirpath), is_dir=True):
                        continue

                    files_buffer = []

                    for fname in sorted(filenames):
                        fpath = Path(dirpath) / fname
                        
                        if fpath.resolve() == out_path:
                            continue

                        if is_ignored_for_content(fpath, is_dir=False):
                            continue

                        # Получаем относительный путь для заголовка файла
                        try:
                            rel_file = fpath.relative_to(base).as_posix()
                        except ValueError:
                            continue
                        
                        # Decided to decline in order to save context
                        # uncomment to make it work
                        # if self._is_path_ignored(rel_file):
                        #     out.write(f"\n--- FILE: {rel_file} (SKIPPED: ignored)\n")
                        #     continue
                        
                        try:
                            size = fpath.stat().st_size
                        except OSError:
                            continue
                        
                        if max_bytes and size > max_bytes:
                            files_buffer.append(f"\n--- FILE: {rel_file} (SKIPPED: size {size} > {max_bytes})\n")
                            continue

                        if not is_probably_text(fpath):
                            files_buffer.append(f"\n--- FILE: {rel_file} (SKIPPED: binary)\n")
                            continue

                        ok, content = try_read_text(fpath)
                        if not ok:
                            files_buffer.append(f"\n--- FILE: {rel_file} (SKIPPED: unreadable as text)\n")
                            continue

                        chunk = []
                        chunk.append(f"\n--- FILE: {rel_file} (size: {size} bytes)\n")
                        chunk.append("```\n")
                        chunk.append(content)
                        if not content.endswith("\n"):
                            chunk.append("\n")
                        chunk.append("```\n")
                        files_buffer.append("".join(chunk))

                    if files_buffer:
                        out.write("\n\n" + "=" * 80 + "\n")
                        out.write(f"DIR: {rel_dir_posix if rel_dir_posix != '.' else '/'}\n")
                        out.write("=" * 80 + "\n")
                        for block in files_buffer:
                            out.write(block)

        except Exception as e:
            messagebox.showerror("Ошибка записи", str(e))
            return

        messagebox.showinfo("Готово", f"Дамп создан:\n{out_path}")

# ------------------ Запуск ------------------
if __name__ == "__main__":
    tk_root = tk.Tk()
    try:
        tk_root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass
    style = ttk.Style(tk_root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    App(tk_root)

    # Стартовое принудительное получение фокуса окном,
    # чтобы не требовалось Alt+Tab для начала ввода
    try:
        tk_root.after(100, tk_root.focus_force)
    except Exception:
        pass

    tk_root.mainloop()