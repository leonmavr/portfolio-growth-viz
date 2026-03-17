import json
import csv
import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog
from tkinter import ttk
from urllib.error import URLError
from urllib.request import Request, urlopen


DARK_BG = "#131722"
PANEL_BG = "#1c2130"
TEXT_FG = "#e8edf8"
MUTED_FG = "#8f9bb3"
ACCENT = "#57c7ff"
ENTRY_BG = "#0f1320"
ENTRY_FG = "#d7e3ff"
ERROR_FG = "#ff7b8a"
GRAPH_COLORS = ["#57c7ff", "#ff9f43", "#62d26f", "#ff6f91", "#c792ea", "#f9f871"]

RANGE_CHOICES = [
    ("1M", "1mo"),
    ("3M", "3mo"),
    ("6M", "6mo"),
    ("1Y", "1y"),
    ("2Y", "2y"),
    ("5Y", "5y"),
]


class CircleButton(tk.Canvas):
    def __init__(self, parent, command, glyph):
        super().__init__(
            parent,
            width=24,
            height=24,
            highlightthickness=0,
            bd=0,
            bg=PANEL_BG,
            cursor="hand2",
        )
        self._command = command
        self._glyph = glyph
        self._hover = False
        self._draw()
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _draw(self):
        self.delete("all")
        border = ACCENT if self._hover else TEXT_FG
        self.create_oval(3, 3, 21, 21, outline=border, width=2)
        self.create_line(7, 12, 17, 12, fill=border, width=2)
        if self._glyph == "+":
            self.create_line(12, 7, 12, 17, fill=border, width=2)

    def _on_click(self, _event):
        self._command()

    def _on_enter(self, _event):
        self._hover = True
        self._draw()

    def _on_leave(self, _event):
        self._hover = False
        self._draw()


def fetch_price_history(symbol, data_range="6mo", interval="1d"):
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Ticker is empty")

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={data_range}&interval={interval}"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urlopen(request, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError(chart["error"].get("description", "Ticker request failed"))

    result = chart.get("result")
    if not result:
        raise ValueError("No data available")

    data = result[0]
    timestamps = data.get("timestamp") or []
    quotes = data.get("indicators", {}).get("quote", [{}])[0].get("close") or []

    series = []
    for ts, close in zip(timestamps, quotes):
        if close is None:
            continue
        series.append((datetime.fromtimestamp(ts).date(), float(close)))

    if not series:
        raise ValueError("No valid prices in response")

    return series


def uniform_sample(series, points=100):
    if not series:
        return []
    if len(series) == 1:
        return [series[0] for _ in range(points)]

    sampled = []
    max_index = len(series) - 1
    for i in range(points):
        pos = i * max_index / (points - 1)
        left = int(pos)
        right = min(max_index, left + 1)
        ratio = pos - left

        left_day, left_value = series[left]
        right_day, right_value = series[right]
        value = left_value + (right_value - left_value) * ratio
        day = left_day if ratio < 0.5 else right_day
        sampled.append((day, value))

    return sampled


class PortfolioVisualizerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Portfolio Visualizer")
        self.root.geometry("1100x720")
        self.root.configure(bg=DARK_BG)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background=PANEL_BG)

        self.ticker_rows = []
        self.ticker_series = {}
        self.range_index = 2
        self.portfolio_name = "Current"
        self.compare_enabled = tk.BooleanVar(value=False)
        self.overlay_portfolios = []
        self._pending_history_requests = set()
        self._plot_series = []
        self._hover_items = []

        self._build_layout()
        self._add_placeholder_row()
        self._draw_message("Add tickers on the left to load portfolio history.")

    def _build_layout(self):
        self.main = ttk.Frame(self.root, style="Dark.TFrame", padding=16)
        self.main.pack(fill="both", expand=True)

        self.main.grid_columnconfigure(0, weight=2, uniform="layout")
        self.main.grid_columnconfigure(1, weight=3, uniform="layout")
        self.main.grid_rowconfigure(0, weight=1)

        # Keep left and right panes anchored to a 40:60 split on resize.
        self.main.bind("<Configure>", self._enforce_panel_ratio)

        self.left_panel = tk.Frame(self.main, bg=PANEL_BG)
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        self.right_panel = tk.Frame(self.main, bg=PANEL_BG)
        self.right_panel.grid(row=0, column=1, sticky="nsew")

        self.left_panel.grid_propagate(False)
        self.right_panel.grid_propagate(False)

        self.left_panel.grid_rowconfigure(1, weight=1)
        self.left_panel.grid_columnconfigure(0, weight=1)

        tk.Button(
            self.left_panel,
            text="^",
            bg="#25304a",
            fg=TEXT_FG,
            activebackground="#304062",
            activeforeground=TEXT_FG,
            relief="flat",
            command=lambda: self._scroll_rows(-3),
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self.rows_view = tk.Canvas(
            self.left_panel,
            bg=PANEL_BG,
            highlightthickness=0,
            bd=0,
        )
        self.rows_view.grid(row=1, column=0, sticky="nsew", padx=8)

        self.rows_container = tk.Frame(self.rows_view, bg=PANEL_BG)
        self.rows_window = self.rows_view.create_window((0, 0), window=self.rows_container, anchor="nw")
        self.rows_container.bind("<Configure>", self._update_rows_scrollregion)
        self.rows_view.bind("<Configure>", self._sync_rows_width)

        # Wheel scrolling for the portfolio list area (Linux + Windows/macOS events).
        self.root.bind_all("<MouseWheel>", self._on_rows_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_rows_mousewheel_linux, add="+")
        self.root.bind_all("<Button-5>", self._on_rows_mousewheel_linux, add="+")

        tk.Button(
            self.left_panel,
            text="v",
            bg="#25304a",
            fg=TEXT_FG,
            activebackground="#304062",
            activeforeground=TEXT_FG,
            relief="flat",
            command=lambda: self._scroll_rows(3),
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 8))

        portfolio_controls = tk.Frame(self.left_panel, bg=PANEL_BG)
        portfolio_controls.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 4))

        tk.Button(
            portfolio_controls,
            text="Load",
            bg="#25304a",
            fg=TEXT_FG,
            activebackground="#304062",
            activeforeground=TEXT_FG,
            relief="flat",
            command=self._load_portfolio_csv,
        ).pack(side="left")

        tk.Button(
            portfolio_controls,
            text="Save",
            bg="#25304a",
            fg=TEXT_FG,
            activebackground="#304062",
            activeforeground=TEXT_FG,
            relief="flat",
            command=self._save_portfolio_csv,
        ).pack(side="left", padx=(6, 0))

        tk.Button(
            portfolio_controls,
            text="Reset",
            bg="#25304a",
            fg=TEXT_FG,
            activebackground="#304062",
            activeforeground=TEXT_FG,
            relief="flat",
            command=self._reset_portfolio,
        ).pack(side="left", padx=(6, 0))

        tk.Checkbutton(
            portfolio_controls,
            text="Compare",
            variable=self.compare_enabled,
            bg=PANEL_BG,
            fg=TEXT_FG,
            selectcolor=PANEL_BG,
            activebackground=PANEL_BG,
            activeforeground=TEXT_FG,
            highlightthickness=0,
            bd=0,
        ).pack(side="right")

        amount_holder = tk.Frame(self.left_panel, bg=PANEL_BG)
        amount_holder.grid(row=4, column=0, sticky="sew", padx=8, pady=(4, 8))

        tk.Label(
            amount_holder,
            text="Total to invest",
            bg=PANEL_BG,
            fg=MUTED_FG,
            font=("TkDefaultFont", 10),
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        self.amount_var = tk.StringVar(value="10000")
        self.amount_entry = tk.Entry(
            amount_holder,
            textvariable=self.amount_var,
            bg=ENTRY_BG,
            fg=ENTRY_FG,
            insertbackground=ENTRY_FG,
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#2a3042",
            font=("TkDefaultFont", 13),
        )
        self.amount_entry.pack(fill="x")
        self.amount_entry.bind("<Return>", lambda _e: self.redraw_graph())
        self.amount_entry.bind("<FocusOut>", lambda _e: self.redraw_graph())

        self.graph_canvas = tk.Canvas(
            self.right_panel,
            bg="#101624",
            highlightthickness=1,
            highlightbackground="#2a3042",
        )
        self.graph_canvas.pack(fill="both", expand=False, padx=8, pady=8)
        self.graph_canvas.configure(height=420)
        self.graph_canvas.bind("<Configure>", lambda _e: self.redraw_graph())
        self.graph_canvas.bind("<Motion>", self._on_graph_motion)
        self.graph_canvas.bind("<Leave>", self._on_graph_leave)

        controls = tk.Frame(self.right_panel, bg=PANEL_BG)
        controls.pack(fill="x", padx=8, pady=(0, 4))

        tk.Button(
            controls,
            text="<<",
            bg="#25304a",
            fg=TEXT_FG,
            activebackground="#304062",
            activeforeground=TEXT_FG,
            relief="flat",
            command=self._shrink_duration,
        ).pack(side="left")

        self.range_label = tk.Label(
            controls,
            text=RANGE_CHOICES[self.range_index][0],
            bg=PANEL_BG,
            fg=TEXT_FG,
            width=6,
        )
        self.range_label.pack(side="left", padx=8)

        tk.Button(
            controls,
            text=">>",
            bg="#25304a",
            fg=TEXT_FG,
            activebackground="#304062",
            activeforeground=TEXT_FG,
            relief="flat",
            command=self._widen_duration,
        ).pack(side="left")

        self.cash_label = tk.Label(
            amount_holder,
            text="Cash: $10,000.00 (100.00%)",
            bg=PANEL_BG,
            fg=MUTED_FG,
            anchor="w",
            font=("TkDefaultFont", 10),
        )
        self.cash_label.pack(fill="x", pady=(6, 0))

    def _enforce_panel_ratio(self, event):
        total_width = max(1, event.width)
        left_width = max(260, int(total_width * 0.4))
        right_width = max(520, total_width - left_width)
        self.main.grid_columnconfigure(0, minsize=left_width)
        self.main.grid_columnconfigure(1, minsize=right_width)

    def _update_rows_scrollregion(self, _event=None):
        self.rows_view.configure(scrollregion=self.rows_view.bbox("all"))

    def _sync_rows_width(self, event):
        self.rows_view.itemconfig(self.rows_window, width=event.width)

    def _scroll_rows(self, units):
        self.rows_view.yview_scroll(units, "units")

    def _pointer_in_rows_area(self):
        try:
            px, py = self.root.winfo_pointerxy()
            widget = self.root.winfo_containing(px, py)
        except tk.TclError:
            return False

        while widget is not None:
            if widget == self.rows_view or widget == self.rows_container:
                return True
            widget = widget.master
        return False

    def _on_rows_mousewheel(self, event):
        if not self._pointer_in_rows_area():
            return
        if event.delta == 0:
            return
        units = -1 if event.delta > 0 else 1
        self._scroll_rows(units)

    def _on_rows_mousewheel_linux(self, event):
        if not self._pointer_in_rows_area():
            return
        units = -1 if event.num == 4 else 1
        self._scroll_rows(units)

    def _add_placeholder_row(self):
        row_frame = tk.Frame(self.rows_container, bg=PANEL_BG)
        row_frame.pack(fill="x", pady=6)

        row = {
            "frame": row_frame,
            "symbol_var": None,
            "alloc_var": None,
            "plot_var": None,
            "entry": None,
            "alloc_entry": None,
            "plot_check": None,
            "price_label": None,
        }

        plus = CircleButton(row_frame, command=lambda r=row: self._expand_row(r), glyph="+")
        plus.pack(side="left", padx=(0, 8))
        row["plus"] = plus

        minus = CircleButton(row_frame, command=lambda r=row: self._remove_row(r), glyph="-")
        minus.pack(side="left", padx=(0, 8))
        row["minus"] = minus

        self.ticker_rows.append(row)

    def _clear_rows(self):
        for row in self.ticker_rows:
            row["frame"].destroy()
        self.ticker_rows = []
        self._add_placeholder_row()

    def _extract_current_allocations(self):
        active_rows, _remaining = self._active_rows_with_allocations(update_caps=True)
        allocations = []
        for item in active_rows:
            allocations.append((item["symbol"], item["alloc_pct"]))
        return allocations

    def _save_portfolio_csv(self):
        allocations = self._extract_current_allocations()
        if not allocations:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="portfolio.csv",
        )
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            for ticker, allocation in allocations:
                writer.writerow([ticker, f"{allocation:.2f}"])

    def _reset_portfolio(self):
        self.overlay_portfolios = []
        self.portfolio_name = "Current"
        self._clear_rows()
        self.redraw_graph()

    def _load_portfolio_csv(self):
        path = filedialog.askopenfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        loaded_allocations = []
        with open(path, "r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if len(row) < 2:
                    continue
                ticker = row[0].strip().upper()
                if not ticker:
                    continue
                try:
                    pct = float(row[1])
                except ValueError:
                    continue
                loaded_allocations.append((ticker, max(0.0, pct)))

        if not loaded_allocations:
            return

        if self.compare_enabled.get():
            current_allocations = self._extract_current_allocations()
            if current_allocations:
                self.overlay_portfolios.append(
                    {
                        "name": self.portfolio_name,
                        "color": GRAPH_COLORS[(len(self.overlay_portfolios) + 1) % len(GRAPH_COLORS)],
                        "allocations": current_allocations,
                    }
                )
        else:
            self.overlay_portfolios = []

        self.portfolio_name = os.path.splitext(os.path.basename(path))[0] or "Current"
        self._populate_rows_from_allocations(loaded_allocations)

    def _populate_rows_from_allocations(self, allocations):
        self._clear_rows()
        for ticker, pct in allocations:
            placeholder = next((r for r in self.ticker_rows if r.get("entry") is None), None)
            if placeholder is None:
                self._add_placeholder_row()
                placeholder = self.ticker_rows[-1]
            self._expand_row(placeholder)
            placeholder["symbol_var"].set(ticker)
            placeholder["alloc_var"].set(f"{pct:.2f}")
            self._load_symbol_from_row(placeholder)
        self.redraw_graph()

    def _expand_row(self, row):
        if row["entry"] is not None:
            return

        symbol_var = tk.StringVar()
        alloc_var = tk.StringVar(value="0")
        plot_var = tk.BooleanVar(value=False)

        plot_check = tk.Checkbutton(
            row["frame"],
            variable=plot_var,
            bg=PANEL_BG,
            fg=TEXT_FG,
            activebackground=PANEL_BG,
            activeforeground=TEXT_FG,
            selectcolor=ENTRY_BG,
            highlightthickness=0,
            bd=0,
            command=self.redraw_graph,
        )
        plot_check.pack(side="left", padx=(0, 6))

        entry = tk.Entry(
            row["frame"],
            textvariable=symbol_var,
            bg=ENTRY_BG,
            fg=ENTRY_FG,
            insertbackground=ENTRY_FG,
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#2a3042",
            width=12,
            font=("TkDefaultFont", 11),
        )
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        alloc_entry = tk.Entry(
            row["frame"],
            textvariable=alloc_var,
            bg=ENTRY_BG,
            fg=ENTRY_FG,
            insertbackground=ENTRY_FG,
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#2a3042",
            width=6,
            justify="right",
            font=("TkDefaultFont", 11),
        )
        alloc_entry.pack(side="left", padx=(0, 4))

        tk.Label(
            row["frame"],
            text="%",
            bg=PANEL_BG,
            fg=MUTED_FG,
            width=2,
            anchor="w",
        ).pack(side="left", padx=(0, 8))

        price_label = tk.Label(
            row["frame"],
            text="",
            bg=PANEL_BG,
            fg=MUTED_FG,
            width=11,
            anchor="e",
            font=("TkDefaultFont", 10),
        )
        price_label.pack(side="left", padx=(8, 0))

        row["symbol_var"] = symbol_var
        row["alloc_var"] = alloc_var
        row["plot_var"] = plot_var
        row["entry"] = entry
        row["alloc_entry"] = alloc_entry
        row["plot_check"] = plot_check
        row["price_label"] = price_label

        entry.bind("<Return>", lambda _e, r=row: self._load_symbol_from_row(r))
        entry.bind("<FocusOut>", lambda _e, r=row: self._load_symbol_from_row(r))
        alloc_entry.bind("<Return>", lambda _e: self.redraw_graph())
        alloc_entry.bind("<FocusOut>", lambda _e: self.redraw_graph())

        self._add_placeholder_row()
        entry.focus_set()

    def _remove_row(self, row):
        if row.get("entry") is None:
            # Keep a single placeholder row so users can always add a stock.
            placeholders = [r for r in self.ticker_rows if r.get("entry") is None]
            if len(placeholders) <= 1:
                return
        row["frame"].destroy()
        self.ticker_rows = [r for r in self.ticker_rows if r is not row]
        if not any(r.get("entry") is None for r in self.ticker_rows):
            self._add_placeholder_row()
        self.redraw_graph()

    def _current_range(self):
        return RANGE_CHOICES[self.range_index][1]

    def _shrink_duration(self):
        if self.range_index > 0:
            self.range_index -= 1
            self._on_range_changed()

    def _widen_duration(self):
        if self.range_index < len(RANGE_CHOICES) - 1:
            self.range_index += 1
            self._on_range_changed()

    def _on_range_changed(self):
        self.range_label.config(text=RANGE_CHOICES[self.range_index][0])
        self._reload_active_rows()
        self._reload_overlay_rows()

    def _reload_overlay_rows(self):
        data_range = self._current_range()
        for overlay in self.overlay_portfolios:
            for symbol, _pct in overlay.get("allocations", []):
                self._ensure_history(symbol, data_range)

    def _reload_active_rows(self):
        for row in self.ticker_rows:
            if row.get("entry") is None:
                continue
            symbol = row["symbol_var"].get().strip()
            if symbol:
                self._load_symbol_from_row(row)
        self.redraw_graph()

    def _load_symbol_from_row(self, row):
        symbol = (row["symbol_var"].get() if row["symbol_var"] else "").strip().upper()
        if not symbol:
            if row["price_label"]:
                row["price_label"].config(text="")
            self.redraw_graph()
            return

        row["symbol_var"].set(symbol)
        row["price_label"].config(text="Loading...", fg=MUTED_FG)
        cache_key = (symbol, self._current_range())
        if cache_key in self.ticker_series:
            history = self.ticker_series[cache_key]
            self._on_symbol_loaded(row, symbol, history, history[-1][1])
            return

        worker = threading.Thread(
            target=self._fetch_symbol_worker,
            args=(symbol, row, self._current_range()),
            daemon=True,
        )
        worker.start()

    def _fetch_symbol_worker(self, symbol, row, data_range):
        try:
            history = fetch_price_history(symbol, data_range=data_range)
            sampled_history = uniform_sample(history, points=100)
            latest = history[-1][1]
            self.root.after(
                0,
                lambda: self._on_symbol_loaded(row, symbol, sampled_history, latest),
            )
        except (ValueError, URLError, TimeoutError) as exc:
            self.root.after(0, lambda: self._on_symbol_failed(row, symbol, str(exc)))

    def _ensure_history(self, symbol, data_range):
        key = (symbol, data_range)
        if key in self.ticker_series or key in self._pending_history_requests:
            return
        self._pending_history_requests.add(key)
        worker = threading.Thread(
            target=self._fetch_history_for_cache_worker,
            args=(symbol, data_range),
            daemon=True,
        )
        worker.start()

    def _fetch_history_for_cache_worker(self, symbol, data_range):
        try:
            history = fetch_price_history(symbol, data_range=data_range)
            sampled_history = uniform_sample(history, points=100)
            self.root.after(0, lambda: self._on_cached_history_loaded(symbol, data_range, sampled_history))
        except (ValueError, URLError, TimeoutError):
            self.root.after(0, lambda: self._on_cached_history_failed(symbol, data_range))

    def _on_cached_history_loaded(self, symbol, data_range, history):
        key = (symbol, data_range)
        self._pending_history_requests.discard(key)
        self.ticker_series[key] = history
        self.redraw_graph()

    def _on_cached_history_failed(self, symbol, data_range):
        self._pending_history_requests.discard((symbol, data_range))

    def _on_symbol_loaded(self, row, symbol, history, latest):
        self.ticker_series[(symbol, self._current_range())] = history
        row["price_label"].config(text=f"${latest:,.2f}", fg=TEXT_FG)
        self.redraw_graph()

    def _on_symbol_failed(self, row, symbol, reason):
        row["price_label"].config(text="Invalid", fg=ERROR_FG)
        self._draw_message(f"Could not load {symbol}: {reason}")
        self.redraw_graph()

    def _active_rows_with_allocations(self, update_caps=False):
        active_rows = []
        remaining = 100.0

        for row in self.ticker_rows:
            if not row["symbol_var"]:
                continue
            symbol = row["symbol_var"].get().strip().upper()
            if not symbol:
                continue

            try:
                requested = float(row["alloc_var"].get())
            except (TypeError, ValueError):
                requested = 0.0
            requested = max(0.0, requested)

            accepted = min(requested, remaining)
            remaining = max(0.0, remaining - accepted)

            if update_caps and row.get("alloc_var"):
                if abs(accepted - requested) > 1e-6:
                    row["alloc_var"].set(f"{accepted:.2f}")
                elif row["alloc_var"].get().strip() == "":
                    row["alloc_var"].set("0")

            active_rows.append({
                "row": row,
                "symbol": symbol,
                "alloc_pct": accepted,
            })

        return active_rows, remaining

    def _cash_state(self):
        try:
            total = float(self.amount_var.get())
        except ValueError:
            total = 0.0

        active_rows, remaining = self._active_rows_with_allocations(update_caps=True)
        cash_amount = total * remaining / 100.0 if total > 0 else 0.0
        return total, active_rows, remaining, cash_amount

    def _portfolio_series(self):
        total, active_rows, cash_pct, _cash_amount = self._cash_state()
        if total <= 0:
            return []
        if not active_rows:
            return []

        data_range = self._current_range()
        loaded_rows = []
        pending_pct = cash_pct
        for item in active_rows:
            symbol = item["symbol"]
            series = self.ticker_series.get((symbol, data_range))
            if series:
                loaded_rows.append((item, series))
            else:
                pending_pct += item["alloc_pct"]

        if not loaded_rows:
            return []

        points = 100
        first_series = loaded_rows[0][1]
        values = []

        for i in range(points):
            day = first_series[i][0]
            flat_cash = total * pending_pct / 100.0
            portfolio_value = flat_cash

            for item, series in loaded_rows:
                start_price = series[0][1]
                current_price = series[i][1]
                allocation_amount = total * item["alloc_pct"] / 100.0
                portfolio_value += allocation_amount * (current_price / start_price)

            values.append((day, portfolio_value))

        return values

    def _portfolio_series_from_allocations(self, allocations, total, data_range):
        if total <= 0 or not allocations:
            return []

        remaining = 100.0
        normalized = []
        for symbol, pct in allocations:
            symbol = symbol.strip().upper()
            if not symbol:
                continue
            requested = max(0.0, float(pct))
            accepted = min(requested, remaining)
            remaining = max(0.0, remaining - accepted)
            normalized.append((symbol, accepted))

        if not normalized:
            return []

        loaded_rows = []
        pending_pct = remaining
        for symbol, pct in normalized:
            series = self.ticker_series.get((symbol, data_range))
            if series:
                loaded_rows.append(((symbol, pct), series))
            else:
                pending_pct += pct
                self._ensure_history(symbol, data_range)

        if not loaded_rows:
            return []

        points = len(loaded_rows[0][1])
        values = []
        for i in range(points):
            day = loaded_rows[0][1][i][0]
            portfolio_value = total * pending_pct / 100.0
            for (symbol, pct), series in loaded_rows:
                start_price = series[0][1]
                current_price = series[i][1]
                allocation_amount = total * pct / 100.0
                portfolio_value += allocation_amount * (current_price / start_price)
            values.append((day, portfolio_value))
        return values

    def _single_stock_series(self, symbol, total, data_range):
        series = self.ticker_series.get((symbol, data_range))
        if not series:
            self._ensure_history(symbol, data_range)
            return []
        if total <= 0:
            return []

        start_price = series[0][1]
        if start_price <= 0:
            return []
        return [(day, total * (price / start_price)) for day, price in series]

    def _next_line_color(self, used_colors):
        for color in GRAPH_COLORS:
            if color not in used_colors:
                used_colors.add(color)
                return color
        return GRAPH_COLORS[len(used_colors) % len(GRAPH_COLORS)]

    def _draw_message(self, text):
        self._plot_series = []
        self._clear_hover()
        self.graph_canvas.delete("all")
        w = self.graph_canvas.winfo_width() or 600
        h = self.graph_canvas.winfo_height() or 420
        self.graph_canvas.create_text(
            w // 2,
            h // 2,
            text=text,
            fill=MUTED_FG,
            font=("TkDefaultFont", 11),
        )
        self._draw_legend(w, [{"name": self.portfolio_name, "color": ACCENT}])

    def _draw_legend(self, width, legend_items):
        x_right = width - 16
        y_top = 14
        for i, item in enumerate(legend_items):
            y = y_top + i * 16
            self.graph_canvas.create_line(
                x_right - 130,
                y,
                x_right - 98,
                y,
                fill=item["color"],
                width=3,
            )
            self.graph_canvas.create_text(
                x_right,
                y,
                text=item["name"],
                anchor="e",
                fill=TEXT_FG,
                font=("TkDefaultFont", 10),
            )

    def _clear_hover(self):
        for item_id in self._hover_items:
            self.graph_canvas.delete(item_id)
        self._hover_items = []

    def _on_graph_leave(self, _event):
        self._clear_hover()

    def _on_graph_motion(self, event):
        if not self._plot_series:
            self._clear_hover()
            return

        x_first = self._plot_series[0]["samples"][0]["x"]
        x_last = self._plot_series[0]["samples"][-1]["x"]
        if event.x < x_first or event.x > x_last:
            self._clear_hover()
            return

        if x_last == x_first:
            idx = 0
        else:
            ratio = (event.x - x_first) / (x_last - x_first)
            size = len(self._plot_series[0]["samples"])
            idx = int(round(ratio * (size - 1)))
            idx = max(0, min(size - 1, idx))

        self._clear_hover()
        for line_index, line in enumerate(self._plot_series):
            sample = line["samples"][idx]
            x = sample["x"]
            y = sample["y"]
            value = sample["value"]

            self._hover_items.append(
                self.graph_canvas.create_oval(
                    x - 6,
                    y - 6,
                    x + 6,
                    y + 6,
                    fill=line["color"],
                    outline="#000000",
                    width=1,
                )
            )
            self._hover_items.append(
                self.graph_canvas.create_text(
                    x,
                    y - 12 - (line_index * 12),
                    text=f"{line['name']}: ${value:,.2f}",
                    anchor="s",
                    fill=line["color"],
                    font=("TkDefaultFont", 9),
                )
            )

    def redraw_graph(self):
        total, active_rows, cash_pct, cash_amount = self._cash_state()
        self.cash_label.config(text=f"Cash: ${cash_amount:,.2f} ({cash_pct:.2f}%)")

        current_data = self._portfolio_series()
        self._plot_series = []
        self._clear_hover()
        self.graph_canvas.delete("all")

        if total <= 0:
            self._draw_message("Enter a valid investment amount above 0.")
            return

        if not active_rows:
            self._draw_message("Add one or more tickers to build the graph.")
            return

        if not current_data:
            self._draw_message("Loading prices... Add valid tickers to render the graph.")
            return

        all_lines = []
        data_range = self._current_range()
        for overlay in self.overlay_portfolios:
            overlay_data = self._portfolio_series_from_allocations(
                overlay.get("allocations", []),
                total,
                data_range,
            )
            if overlay_data:
                all_lines.append(
                    {
                        "name": overlay["name"],
                        "color": overlay["color"],
                        "data": overlay_data,
                    }
                )
        all_lines.append({"name": self.portfolio_name, "color": ACCENT, "data": current_data})

        used_colors = {line["color"] for line in all_lines}
        for row in self.ticker_rows:
            if not row.get("plot_var") or not row["plot_var"].get():
                continue
            symbol = row["symbol_var"].get().strip().upper() if row.get("symbol_var") else ""
            if not symbol:
                continue
            stock_data = self._single_stock_series(symbol, total, data_range)
            if not stock_data:
                continue
            all_lines.append(
                {
                    "name": symbol,
                    "color": self._next_line_color(used_colors),
                    "data": stock_data,
                }
            )

        w = self.graph_canvas.winfo_width() or 600
        h = self.graph_canvas.winfo_height() or 420
        pad_left = 56
        pad_right = 18
        pad_top = 24
        pad_bottom = 44

        values = []
        for line in all_lines:
            values.extend(v for _, v in line["data"])
        min_v = min(values)
        max_v = max(values)

        span = max_v - min_v
        if span == 0:
            span = 1.0

        for i in range(5):
            y = pad_top + i * (h - pad_top - pad_bottom) / 4
            self.graph_canvas.create_line(
                pad_left,
                y,
                w - pad_right,
                y,
                fill="#263044",
                width=1,
            )

        tick_count = 6
        for i in range(tick_count):
            ratio = i / (tick_count - 1)
            x = pad_left + ratio * (w - pad_left - pad_right)
            idx = int(ratio * (len(current_data) - 1))
            tick_day = current_data[idx][0].strftime("%m-%y")

            self.graph_canvas.create_line(
                x,
                pad_top,
                x,
                h - pad_bottom,
                fill="#2e3850",
                width=1,
            )
            self.graph_canvas.create_text(
                x,
                h - pad_bottom + 14,
                text=tick_day,
                fill=MUTED_FG,
                font=("TkDefaultFont", 9),
            )

        self.graph_canvas.create_line(pad_left, pad_top, pad_left, h - pad_bottom, fill="#4a5674")
        self.graph_canvas.create_line(
            pad_left,
            h - pad_bottom,
            w - pad_right,
            h - pad_bottom,
            fill="#4a5674",
        )

        plot_series = []
        for line in all_lines:
            points = []
            samples = []
            for i, (_, value) in enumerate(line["data"]):
                x = pad_left + i * (w - pad_left - pad_right) / max(1, len(line["data"]) - 1)
                y = h - pad_bottom - ((value - min_v) / span) * (h - pad_top - pad_bottom)
                points.extend([x, y])
                samples.append({"x": x, "y": y, "value": value})
            self.graph_canvas.create_line(points, fill=line["color"], width=2.5, smooth=True)
            plot_series.append({"name": line["name"], "color": line["color"], "samples": samples})

        self.graph_canvas.create_text(
            8,
            pad_top,
            text=f"${max_v:,.2f}",
            anchor="w",
            fill=MUTED_FG,
            font=("TkDefaultFont", 9),
        )
        self.graph_canvas.create_text(
            8,
            h - pad_bottom,
            text=f"${min_v:,.2f}",
            anchor="w",
            fill=MUTED_FG,
            font=("TkDefaultFont", 9),
        )
        self._draw_legend(w, [{"name": line["name"], "color": line["color"]} for line in all_lines])
        self._plot_series = plot_series


def main():
    root = tk.Tk()
    app = PortfolioVisualizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
