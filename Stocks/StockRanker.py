from io import StringIO
import threading
import warnings
import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

TIMEFRAMES = {
    "1D": 1,
    "1W": 5,
    "1M": 21,
    "3M": 63,
    "6M": 126,
    "1Y": 252,
}

FALLBACK_CONSTITUENTS = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corporation"),
    ("NVDA", "NVIDIA Corporation"),
    ("GOOGL", "Alphabet Inc. (Class A)"),
    ("GOOG", "Alphabet Inc. (Class C)"),
    ("AMZN", "Amazon.com, Inc."),
    ("META", "Meta Platforms, Inc."),
    ("TSLA", "Tesla, Inc."),
    ("BRK-B", "Berkshire Hathaway Inc. (Class B)"),
    ("JPM", "JPMorgan Chase & Co."),
    ("V", "Visa Inc."),
    ("UNH", "UnitedHealth Group Incorporated"),
    ("XOM", "Exxon Mobil Corporation"),
    ("MA", "Mastercard Incorporated"),
    ("PG", "The Procter & Gamble Company"),
    ("HD", "The Home Depot, Inc."),
    ("COST", "Costco Wholesale Corporation"),
    ("MRK", "Merck & Co., Inc."),
    ("ABBV", "AbbVie Inc."),
    ("LLY", "Eli Lilly and Company"),
]

# Assumption: US large caps are generally available via EasyEquities US market.
# Add symbols here if you want to force specific stocks to "No".
EASYEQUITIES_UNAVAILABLE = set()

BG = "#111827"
CARD = "#1F2937"
CARD_ALT = "#0F172A"
TEXT = "#E5E7EB"
MUTED = "#9CA3AF"
ACCENT = "#38BDF8"
GREEN = "#22C55E"
RED = "#EF4444"


def get_sp500_constituents():
    """Return (tickers, company_name_by_ticker) from Wikipedia with fallback."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        table = pd.read_html(StringIO(response.text))[0]
        symbols = [str(s).replace(".", "-") for s in table["Symbol"].tolist()]
        names = [str(n) for n in table["Security"].tolist()]
        name_map = dict(zip(symbols, names))
        return symbols, name_map
    except Exception:
        tickers = [t for t, _ in FALLBACK_CONSTITUENTS]
        name_map = {t: n for t, n in FALLBACK_CONSTITUENTS}
        return tickers, name_map


def download_data(tickers):
    """Download 1 year of close prices for all symbols."""
    data = yf.download(tickers, period="1y", progress=False)["Close"]
    if data is None or data.empty:
        raise RuntimeError("No price data returned from Yahoo Finance.")
    return data


def rank_stocks(data, timeframe):
    """Return top 10 gainers and top 10 losers by largest drop."""
    days_back = TIMEFRAMES[timeframe]
    if len(data) <= days_back:
        raise ValueError(f"Not enough data to calculate {timeframe}.")

    latest_prices = data.iloc[-1]
    past_prices = data.iloc[-(days_back + 1)]
    pct_change = ((latest_prices - past_prices) / past_prices) * 100
    pct_change = pct_change.dropna()

    gainers = pct_change.sort_values(ascending=False).head(10)
    losers = pct_change.sort_values(ascending=True).head(10)
    return gainers, losers


def easy_equities_status(ticker):
    """Yes/No availability flag for EasyEquities."""
    return "No" if ticker in EASYEQUITIES_UNAVAILABLE else "Yes"


class StockRankerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Stock Ranker")
        self.root.geometry("1280x760")
        self.root.minsize(1080, 660)
        self.root.configure(bg=BG)

        self.historical_data = None
        self.company_names = {}
        self.tickers_count = 0
        self.loading = False

        self._configure_style()
        self._build_ui()
        self.root.after(200, self.load_market_data)

    def _configure_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure("Root.TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 20, "bold"))
        style.configure("SubTitle.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Status.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        style.configure("Meta.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))

        style.configure(
            "Dark.TButton",
            font=("Segoe UI", 10, "bold"),
            foreground=TEXT,
            background="#334155",
            borderwidth=0,
            padding=(12, 8),
        )
        style.map(
            "Dark.TButton",
            background=[("active", "#475569"), ("disabled", "#1E293B")],
            foreground=[("disabled", "#6B7280")],
        )

        style.configure(
            "Dark.TCombobox",
            fieldbackground="#0B1220",
            background="#0B1220",
            foreground=TEXT,
            bordercolor="#334155",
            arrowsize=14,
        )

        style.configure(
            "Dark.Horizontal.TProgressbar",
            troughcolor="#0B1220",
            background=ACCENT,
            bordercolor="#0B1220",
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

        style.configure(
            "Dark.Treeview",
            background=CARD_ALT,
            foreground=TEXT,
            fieldbackground=CARD_ALT,
            bordercolor="#1E293B",
            rowheight=28,
            font=("Segoe UI", 10),
        )
        style.map("Dark.Treeview", background=[("selected", "#334155")], foreground=[("selected", TEXT)])
        style.configure(
            "Dark.Treeview.Heading",
            background="#0B1220",
            foreground=TEXT,
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        )
        style.map("Dark.Treeview.Heading", background=[("active", "#1E293B")])

    def _build_ui(self):
        root_frame = ttk.Frame(self.root, style="Root.TFrame", padding=16)
        root_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root_frame, text="Stock Ranker", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            root_frame,
            text="Left = Gainers, Right = Losers (largest drop first). Auto-loads on startup.",
            style="SubTitle.TLabel",
        ).pack(anchor=tk.W, pady=(2, 14))

        controls = ttk.Frame(root_frame, style="Root.TFrame")
        controls.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(controls, text="Timeframe", style="SubTitle.TLabel").pack(side=tk.LEFT)
        self.timeframe_var = tk.StringVar(value="1D")
        self.timeframe_combo = ttk.Combobox(
            controls,
            textvariable=self.timeframe_var,
            values=list(TIMEFRAMES.keys()),
            state="readonly",
            width=8,
            style="Dark.TCombobox",
        )
        self.timeframe_combo.pack(side=tk.LEFT, padx=(8, 10))
        self.timeframe_combo.bind("<<ComboboxSelected>>", lambda _e: self.run_ranking())

        self.refresh_button = ttk.Button(
            controls,
            text="Refresh Market Data",
            style="Dark.TButton",
            command=self.load_market_data,
        )
        self.refresh_button.pack(side=tk.LEFT, padx=(0, 10))

        self.rank_button = ttk.Button(
            controls,
            text="Recalculate",
            style="Dark.TButton",
            command=self.run_ranking,
            state=tk.DISABLED,
        )
        self.rank_button.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Starting data load...")
        ttk.Label(root_frame, textvariable=self.status_var, style="Status.TLabel").pack(anchor=tk.W, pady=(0, 6))

        self.progress = ttk.Progressbar(root_frame, mode="indeterminate", style="Dark.Horizontal.TProgressbar")
        self.progress.pack(fill=tk.X, pady=(0, 12))

        body = ttk.Frame(root_frame, style="Root.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        self.gainers_tree = self._build_table_panel(body, 0, "Top 10 Gainers", GREEN)
        self.losers_tree = self._build_table_panel(body, 1, "Top 10 Losers", RED)

        self.meta_var = tk.StringVar(value="No data loaded yet.")
        ttk.Label(root_frame, textvariable=self.meta_var, style="Meta.TLabel").pack(anchor=tk.W, pady=(8, 0))

    def _build_table_panel(self, parent, col, title, color):
        panel = ttk.Frame(parent, style="Card.TFrame", padding=10)
        panel.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))

        title_label = tk.Label(panel, text=title, bg=CARD, fg=color, font=("Segoe UI", 12, "bold"), anchor="w")
        title_label.pack(fill=tk.X, pady=(0, 8))

        columns = ("rank", "ticker", "company", "pct", "easy")
        tree = ttk.Treeview(panel, columns=columns, show="headings", style="Dark.Treeview", selectmode="browse")
        tree.heading("rank", text="#")
        tree.heading("ticker", text="Ticker")
        tree.heading("company", text="Company")
        tree.heading("pct", text="Change %")
        tree.heading("easy", text="EasyEquities")

        tree.column("rank", width=45, minwidth=40, anchor=tk.CENTER)
        tree.column("ticker", width=85, minwidth=70, anchor=tk.CENTER)
        tree.column("company", width=290, minwidth=200, anchor=tk.W)
        tree.column("pct", width=95, minwidth=80, anchor=tk.E)
        tree.column("easy", width=100, minwidth=90, anchor=tk.CENTER)

        scroll = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _set_loading_state(self, is_loading):
        self.loading = is_loading
        if is_loading:
            self.refresh_button.configure(state=tk.DISABLED)
            self.rank_button.configure(state=tk.DISABLED)
            self.progress.start(12)
        else:
            self.refresh_button.configure(state=tk.NORMAL)
            self.rank_button.configure(state=(tk.NORMAL if self.historical_data is not None else tk.DISABLED))
            self.progress.stop()

    def _set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def load_market_data(self):
        if self.loading:
            return
        self._set_loading_state(True)
        self._set_status("Loading tickers and price history...")
        self.meta_var.set("Fetching latest market data from Wikipedia and Yahoo Finance...")
        thread = threading.Thread(target=self._load_market_data_worker, daemon=True)
        thread.start()

    def _load_market_data_worker(self):
        try:
            tickers, company_names = get_sp500_constituents()
            data = download_data(tickers)
            self.root.after(0, lambda: self._on_load_success(data, company_names, len(tickers)))
        except Exception as exc:
            self.root.after(0, lambda: self._on_load_failure(exc))

    def _on_load_success(self, data, company_names, ticker_count):
        self.historical_data = data
        self.company_names = company_names
        self.tickers_count = ticker_count
        self._set_loading_state(False)
        self._set_status(f"Loaded data for {self.tickers_count} tickers.")
        self.meta_var.set("Data loaded. Company names and EasyEquities flags included.")
        self.run_ranking()

    def _on_load_failure(self, exc):
        self._set_loading_state(False)
        self._set_status("Data load failed.")
        self.meta_var.set("Unable to load market data.")
        messagebox.showerror("Load Failed", f"Could not load market data:\n{exc}")

    def _fill_tree(self, tree, series, include_plus):
        for item in tree.get_children():
            tree.delete(item)

        for idx, (ticker, pct) in enumerate(series.items(), 1):
            company = self.company_names.get(ticker, ticker)
            pct_text = f"{pct:+.2f}%" if include_plus else f"{pct:.2f}%"
            easy = easy_equities_status(ticker)
            tree.insert("", tk.END, values=(idx, ticker, company, pct_text, easy))

    def run_ranking(self):
        if self.loading or self.historical_data is None:
            return

        timeframe = self.timeframe_var.get()
        try:
            gainers, losers = rank_stocks(self.historical_data, timeframe)
            self._fill_tree(self.gainers_tree, gainers, include_plus=True)
            self._fill_tree(self.losers_tree, losers, include_plus=False)
            self._set_status(f"Showing {timeframe} movers.")
            self.meta_var.set("Losers are sorted from highest drop to lowest drop.")
        except Exception as exc:
            self._set_status("Ranking failed.")
            messagebox.showerror("Ranking Error", str(exc))


def main():
    root = tk.Tk()
    StockRankerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
from io import StringIO
import threading
import warnings
import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

FALLBACK_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "GOOG",
    "AMZN",
    "META",
    "TSLA",
    "BRK-B",
    "JPM",
    "V",
    "UNH",
    "XOM",
    "MA",
    "PG",
    "HD",
    "COST",
    "MRK",
    "ABBV",
    "LLY",
]

TIMEFRAMES = {
    "1D": 1,
    "1W": 5,
    "1M": 21,
    "3M": 63,
    "6M": 126,
    "1Y": 252,
}

BG = "#111827"
CARD = "#1F2937"
CARD_ALT = "#0F172A"
TEXT = "#E5E7EB"
MUTED = "#9CA3AF"
ACCENT = "#38BDF8"
GREEN = "#22C55E"
RED = "#EF4444"


def get_sp500_tickers():
    """Fetch S&P 500 tickers from Wikipedia, fallback when blocked."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        table = pd.read_html(StringIO(response.text))[0]
        return [ticker.replace(".", "-") for ticker in table["Symbol"].tolist()]
    except Exception:
        return FALLBACK_TICKERS


def download_data(tickers):
    """Download 1 year of close prices for all symbols."""
    data = yf.download(tickers, period="1y", progress=False)["Close"]
    if data is None or data.empty:
        raise RuntimeError("No price data returned from Yahoo Finance.")
    return data


def rank_stocks(data, timeframe):
    """Return top 10 gainers and top 10 losers by drop magnitude."""
    days_back = TIMEFRAMES[timeframe]
    if len(data) <= days_back:
        raise ValueError(f"Not enough data to calculate {timeframe}.")

    latest_prices = data.iloc[-1]
    past_prices = data.iloc[-(days_back + 1)]
    pct_change = ((latest_prices - past_prices) / past_prices) * 100
    pct_change = pct_change.dropna()

    gainers = pct_change.sort_values(ascending=False).head(10)
    # Most negative first: biggest drop to smallest drop
    losers = pct_change.sort_values(ascending=True).head(10)
    return gainers, losers


class StockRankerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Stock Ranker")
        self.root.geometry("1080x700")
        self.root.minsize(920, 620)
        self.root.configure(bg=BG)

        self.historical_data = None
        self.tickers_count = 0
        self.loading = False

        self._configure_style()
        self._build_ui()

        # Auto-load data on startup to reduce required clicks.
        self.root.after(200, self.load_market_data)

    def _configure_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure("Root.TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("CardAlt.TFrame", background=CARD_ALT)

        style.configure(
            "Title.TLabel",
            background=BG,
            foreground=TEXT,
            font=("Segoe UI", 20, "bold"),
        )
        style.configure(
            "SubTitle.TLabel",
            background=BG,
            foreground=MUTED,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Status.TLabel",
            background=BG,
            foreground=ACCENT,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TableTitle.TLabel",
            background=CARD,
            foreground=TEXT,
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Meta.TLabel",
            background=BG,
            foreground=MUTED,
            font=("Segoe UI", 9),
        )

        style.configure(
            "Dark.TButton",
            font=("Segoe UI", 10, "bold"),
            foreground=TEXT,
            background="#334155",
            borderwidth=0,
            padding=(12, 8),
        )
        style.map(
            "Dark.TButton",
            background=[("active", "#475569"), ("disabled", "#1E293B")],
            foreground=[("disabled", "#6B7280")],
        )

        style.configure(
            "Dark.TCombobox",
            fieldbackground="#0B1220",
            background="#0B1220",
            foreground=TEXT,
            bordercolor="#334155",
            arrowsize=14,
        )

        style.configure(
            "Dark.Horizontal.TProgressbar",
            troughcolor="#0B1220",
            background=ACCENT,
            bordercolor="#0B1220",
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

        style.configure(
            "Dark.Treeview",
            background=CARD_ALT,
            foreground=TEXT,
            fieldbackground=CARD_ALT,
            bordercolor="#1E293B",
            rowheight=28,
            font=("Segoe UI", 10),
        )
        style.map(
            "Dark.Treeview",
            background=[("selected", "#334155")],
            foreground=[("selected", TEXT)],
        )
        style.configure(
            "Dark.Treeview.Heading",
            background="#0B1220",
            foreground=TEXT,
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        )
        style.map("Dark.Treeview.Heading", background=[("active", "#1E293B")])

    def _build_ui(self):
        root_frame = ttk.Frame(self.root, style="Root.TFrame", padding=16)
        root_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root_frame, text="Stock Ranker", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            root_frame,
            text="Top gainers (left) and top losers (right), auto-loaded on startup.",
            style="SubTitle.TLabel",
        ).pack(anchor=tk.W, pady=(2, 14))

        controls = ttk.Frame(root_frame, style="Root.TFrame")
        controls.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(controls, text="Timeframe", style="SubTitle.TLabel").pack(side=tk.LEFT)

        self.timeframe_var = tk.StringVar(value="1D")
        self.timeframe_combo = ttk.Combobox(
            controls,
            textvariable=self.timeframe_var,
            values=list(TIMEFRAMES.keys()),
            state="readonly",
            width=8,
            style="Dark.TCombobox",
        )
        self.timeframe_combo.pack(side=tk.LEFT, padx=(8, 10))
        self.timeframe_combo.bind("<<ComboboxSelected>>", lambda _e: self.run_ranking())

        self.refresh_button = ttk.Button(
            controls,
            text="Refresh Market Data",
            style="Dark.TButton",
            command=self.load_market_data,
        )
        self.refresh_button.pack(side=tk.LEFT, padx=(0, 10))

        self.rank_button = ttk.Button(
            controls,
            text="Recalculate",
            style="Dark.TButton",
            command=self.run_ranking,
            state=tk.DISABLED,
        )
        self.rank_button.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Starting data load...")
        ttk.Label(root_frame, textvariable=self.status_var, style="Status.TLabel").pack(
            anchor=tk.W, pady=(0, 6)
        )

        self.progress = ttk.Progressbar(
            root_frame,
            mode="indeterminate",
            style="Dark.Horizontal.TProgressbar",
        )
        self.progress.pack(fill=tk.X, pady=(0, 12))

        body = ttk.Frame(root_frame, style="Root.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        self.gainers_tree = self._build_table_panel(body, 0, "Top 10 Gainers", GREEN)
        self.losers_tree = self._build_table_panel(body, 1, "Top 10 Losers", RED)

        self.meta_var = tk.StringVar(value="No data loaded yet.")
        ttk.Label(root_frame, textvariable=self.meta_var, style="Meta.TLabel").pack(
            anchor=tk.W, pady=(8, 0)
        )

    def _build_table_panel(self, parent, column_index, title, title_color):
        panel = ttk.Frame(parent, style="Card.TFrame", padding=10)
        panel.grid(row=0, column=column_index, sticky="nsew", padx=(0 if column_index == 0 else 8, 0))

        title_label = tk.Label(
            panel,
            text=title,
            bg=CARD,
            fg=title_color,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        )
        title_label.pack(fill=tk.X, pady=(0, 8))

        columns = ("rank", "ticker", "pct")
        tree = ttk.Treeview(
            panel,
            columns=columns,
            show="headings",
            style="Dark.Treeview",
            selectmode="browse",
        )
        tree.heading("rank", text="#")
        tree.heading("ticker", text="Ticker")
        tree.heading("pct", text="Change %")

        tree.column("rank", width=55, minwidth=45, anchor=tk.CENTER)
        tree.column("ticker", width=100, minwidth=80, anchor=tk.CENTER)
        tree.column("pct", width=130, minwidth=100, anchor=tk.E)

        scroll = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _set_loading_state(self, loading):
        self.loading = loading
        if loading:
            self.refresh_button.configure(state=tk.DISABLED)
            self.rank_button.configure(state=tk.DISABLED)
            self.progress.start(12)
        else:
            self.refresh_button.configure(state=tk.NORMAL)
            self.rank_button.configure(
                state=(tk.NORMAL if self.historical_data is not None else tk.DISABLED)
            )
            self.progress.stop()

    def _set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def load_market_data(self):
        if self.loading:
            return

        self._set_loading_state(True)
        self._set_status("Loading tickers and price history...")
        self.meta_var.set("Fetching latest market data from Wikipedia/Yahoo...")

        worker = threading.Thread(target=self._load_market_data_worker, daemon=True)
        worker.start()

    def _load_market_data_worker(self):
        try:
            tickers = get_sp500_tickers()
            data = download_data(tickers)
            self.root.after(0, lambda: self._on_load_success(data, len(tickers)))
        except Exception as exc:
            self.root.after(0, lambda: self._on_load_failure(exc))

    def _on_load_success(self, data, ticker_count):
        self.historical_data = data
        self.tickers_count = ticker_count
        self._set_loading_state(False)
        self._set_status(f"Loaded data for {self.tickers_count} tickers.")
        self.meta_var.set("Data loaded. Showing latest ranking.")
        self.run_ranking()

    def _on_load_failure(self, exc):
        self._set_loading_state(False)
        self._set_status("Data load failed.")
        self.meta_var.set("Unable to load market data.")
        messagebox.showerror("Load Failed", f"Could not load market data:\n{exc}")

    def _populate_tree(self, tree, series, positive_sign):
        for row_id in tree.get_children():
            tree.delete(row_id)

        for index, (ticker, pct) in enumerate(series.items(), start=1):
            pct_text = f"{pct:+.2f}%" if positive_sign else f"{pct:.2f}%"
            tree.insert("", tk.END, values=(index, ticker, pct_text))

    def run_ranking(self):
        if self.loading or self.historical_data is None:
            return

        timeframe = self.timeframe_var.get()
        try:
            gainers, losers = rank_stocks(self.historical_data, timeframe)
            self._populate_tree(self.gainers_tree, gainers, positive_sign=True)
            self._populate_tree(self.losers_tree, losers, positive_sign=False)
            self._set_status(f"Showing {timeframe} movers.")
            self.meta_var.set(
                f"Rows are ranked 1-10. Losers are ordered by largest drop to smaller drop."
            )
        except Exception as exc:
            self._set_status("Ranking failed.")
            messagebox.showerror("Ranking Error", str(exc))


def main():
    root = tk.Tk()
    StockRankerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
from io import StringIO
import threading
import warnings
import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

FALLBACK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "XOM", "MA", "PG", "HD", "COST", "MRK", "ABBV", "LLY",
]
TIMEFRAMES = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252}


def get_sp500_tickers():
    """Fetch the current S&P 500 tickers from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        table = pd.read_html(StringIO(response.text))[0]
        return [ticker.replace(".", "-") for ticker in table["Symbol"].tolist()]
    except Exception:
        return FALLBACK_TICKERS


def download_data(tickers):
    """Download 1 year of daily close prices for all tickers."""
    data = yf.download(tickers, period="1y", progress=False)["Close"]
    if data is None or data.empty:
        raise RuntimeError("No price data returned from Yahoo Finance.")
    return data


def rank_stocks(data, timeframe):
    """Calculate percentage change and return top and bottom 10."""
    days_back = TIMEFRAMES[timeframe]
    if len(data) <= days_back:
        raise ValueError(f"Not enough data to calculate {timeframe}.")

    latest_prices = data.iloc[-1]
    past_prices = data.iloc[-(days_back + 1)]
    pct_change = ((latest_prices - past_prices) / past_prices) * 100
    pct_change = pct_change.dropna()
    sorted_changes = pct_change.sort_values(ascending=False)
    return sorted_changes.head(10), sorted_changes.tail(10)


def format_results(top_10, bottom_10, timeframe):
    lines = []
    lines.append("=" * 44)
    lines.append(f" MARKET MOVERS: {timeframe} TIMEFRAME")
    lines.append("=" * 44)
    lines.append("")
    lines.append("TOP 10 GAINERS")
    lines.append("-" * 44)
    for i, (ticker, pct) in enumerate(top_10.items(), 1):
        lines.append(f"{i:>2}. {ticker:<6} | +{pct:>7.2f}%")
    lines.append("")
    lines.append("BOTTOM 10 LOSERS")
    lines.append("-" * 44)
    for i, (ticker, pct) in enumerate(bottom_10.items(), 1):
        lines.append(f"{i:>2}. {ticker:<6} | {pct:>8.2f}%")
    lines.append("=" * 44)
    return "\n".join(lines)


class StockRankerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Stock Ranker")
        self.root.geometry("860x620")
        self.root.minsize(760, 540)

        self.historical_data = None
        self.tickers_count = 0
        self.loading = False

        self._build_ui()

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            frame,
            text="Python Stock Ranker",
            font=("Segoe UI", 18, "bold"),
        )
        title.pack(anchor=tk.W)

        subtitle = ttk.Label(
            frame,
            text="Load data once, then rank top/bottom movers by timeframe.",
            font=("Segoe UI", 10),
        )
        subtitle.pack(anchor=tk.W, pady=(0, 12))

        controls = ttk.Frame(frame)
        controls.pack(fill=tk.X, pady=(0, 10))

        self.load_button = ttk.Button(
            controls,
            text="Load Market Data",
            command=self.load_market_data,
        )
        self.load_button.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(controls, text="Timeframe:").pack(side=tk.LEFT)
        self.timeframe_var = tk.StringVar(value="1D")
        self.timeframe_combo = ttk.Combobox(
            controls,
            textvariable=self.timeframe_var,
            values=list(TIMEFRAMES.keys()),
            state="readonly",
            width=8,
        )
        self.timeframe_combo.pack(side=tk.LEFT, padx=(8, 10))

        self.rank_button = ttk.Button(
            controls,
            text="Run Ranking",
            command=self.run_ranking,
            state=tk.DISABLED,
        )
        self.rank_button.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Ready. Click 'Load Market Data' to begin.")
        status = ttk.Label(
            frame,
            textvariable=self.status_var,
            foreground="#1f4e79",
        )
        status.pack(anchor=tk.W, pady=(0, 10))

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.output = tk.Text(
            text_frame,
            wrap=tk.NONE,
            font=("Consolas", 11),
            padx=10,
            pady=10,
        )
        self.output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.output.insert(
            tk.END,
            "Results will appear here after loading data and running a ranking.\n",
        )
        self.output.configure(state=tk.DISABLED)

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.output.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.output.configure(yscrollcommand=scrollbar.set)

    def set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def set_output(self, text):
        self.output.configure(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, text)
        self.output.configure(state=tk.DISABLED)

    def load_market_data(self):
        if self.loading:
            return

        self.loading = True
        self.load_button.configure(state=tk.DISABLED)
        self.rank_button.configure(state=tk.DISABLED)
        self.set_status("Loading tickers and price history... please wait.")
        self.set_output("Loading data from Wikipedia and Yahoo Finance...\n")

        worker = threading.Thread(target=self._load_market_data_worker, daemon=True)
        worker.start()

    def _load_market_data_worker(self):
        try:
            tickers = get_sp500_tickers()
            data = download_data(tickers)
            self.tickers_count = len(tickers)
            self.historical_data = data
            self.root.after(0, self._on_load_success)
        except Exception as exc:
            self.root.after(0, lambda: self._on_load_failure(exc))

    def _on_load_success(self):
        self.loading = False
        self.load_button.configure(state=tk.NORMAL)
        self.rank_button.configure(state=tk.NORMAL)
        self.set_status(f"Loaded data for {self.tickers_count} tickers. Select timeframe and run.")
        self.set_output(
            "Data loaded successfully.\n\n"
            "Choose a timeframe from the dropdown and click 'Run Ranking'."
        )

    def _on_load_failure(self, exc):
        self.loading = False
        self.load_button.configure(state=tk.NORMAL)
        self.rank_button.configure(state=tk.DISABLED)
        self.set_status("Load failed. See error details.")
        self.set_output(f"Failed to load market data:\n{exc}")
        messagebox.showerror("Load Failed", f"Could not load market data:\n{exc}")

    def run_ranking(self):
        if self.historical_data is None:
            messagebox.showwarning("Data Not Loaded", "Please click 'Load Market Data' first.")
            return

        timeframe = self.timeframe_var.get()
        try:
            top_10, bottom_10 = rank_stocks(self.historical_data, timeframe)
            results = format_results(top_10, bottom_10, timeframe)
            self.set_output(results)
            self.set_status(f"Showing {timeframe} ranking.")
        except Exception as exc:
            self.set_status("Ranking failed.")
            messagebox.showerror("Ranking Error", str(exc))


def main():
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except Exception:
        pass
    app = StockRankerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
from io import StringIO
import warnings

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

FALLBACK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "BRK-B",
    "JPM", "V", "UNH", "XOM", "MA", "PG", "HD", "COST", "MRK", "ABBV", "LLY",
]


def get_sp500_tickers():
    """Fetch the current S&P 500 tickers from Wikipedia."""
    print("Fetching S&P 500 tickers from Wikipedia...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        table = pd.read_html(StringIO(response.text))[0]
        tickers = [ticker.replace(".", "-") for ticker in table["Symbol"].tolist()]
        print(f"Loaded {len(tickers)} tickers.\n")
        return tickers
    except Exception as exc:
        print(f"Wikipedia fetch failed ({exc}).")
        print(f"Using fallback list of {len(FALLBACK_TICKERS)} large-cap tickers.\n")
        return FALLBACK_TICKERS


def download_data(tickers):
    """Download 1 year of daily closing prices for all tickers."""
    print(f"Downloading 1-year historical data for {len(tickers)} stocks...")
    print("This might take a minute, please wait...\n")

    data = yf.download(tickers, period="1y", progress=False)["Close"]
    if data is None or data.empty:
        raise RuntimeError("No price data returned from Yahoo Finance.")
    return data


def rank_stocks(data, timeframe):
    """Calculate percentage change and return top and bottom 10."""
    timeframes = {
        "1D": 1,
        "1W": 5,
        "1M": 21,
        "3M": 63,
        "6M": 126,
        "1Y": 252,
    }
    days_back = timeframes[timeframe]

    if len(data) <= days_back:
        print(f"Not enough data to calculate {timeframe}.")
        return None, None

    latest_prices = data.iloc[-1]
    past_prices = data.iloc[-(days_back + 1)]
    pct_change = ((latest_prices - past_prices) / past_prices) * 100
    pct_change = pct_change.dropna()

    sorted_changes = pct_change.sort_values(ascending=False)
    return sorted_changes.head(10), sorted_changes.tail(10)


def display_results(top_10, bottom_10, timeframe):
    """Print the ranking results."""
    print(f"\n{'=' * 40}")
    print(f" MARKET MOVERS: {timeframe} TIMEFRAME")
    print(f"{'=' * 40}")

    print("\nTOP 10 GAINERS:")
    print("-" * 25)
    for i, (ticker, pct) in enumerate(top_10.items(), 1):
        print(f"{i:>2}. {ticker:<6} | +{pct:.2f}%")

    print("\nBOTTOM 10 LOSERS:")
    print("-" * 25)
    for i, (ticker, pct) in enumerate(bottom_10.items(), 1):
        print(f"{i:>2}. {ticker:<6} | {pct:.2f}%")

    print(f"{'=' * 40}\n")


def main():
    print("Welcome to the Python Stock Ranker!")
    try:
        tickers = get_sp500_tickers()
        historical_data = download_data(tickers)
    except Exception as exc:
        print(f"Startup failed: {exc}")
        return

    while True:
        print("Available timeframes: 1D, 1W, 1M, 3M, 6M, 1Y")
        user_input = input("Enter a timeframe (or type 'exit' to quit): ").strip().upper()

        if user_input == "EXIT":
            print("Exiting the program. Happy trading!")
            break

        if user_input not in {"1D", "1W", "1M", "3M", "6M", "1Y"}:
            print("Invalid input. Please choose from the list.\n")
            continue

        top_10, bottom_10 = rank_stocks(historical_data, user_input)
        if top_10 is not None and bottom_10 is not None:
            display_results(top_10, bottom_10, user_input)


if __name__ == "__main__":
    main()
import warnings

import pandas as pd
import yfinance as yf

# Suppress warnings for cleaner console output
warnings.filterwarnings("ignore")


def get_sp500_tickers():
    """Fetches the current S&P 500 tickers from Wikipedia."""
    print("Fetching S&P 500 tickers from Wikipedia...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    table = pd.read_html(url)[0]
    tickers = table["Symbol"].tolist()

    # yfinance uses dashes instead of dots for tickers like BRK.B
    return [ticker.replace(".", "-") for ticker in tickers]


def download_data(tickers):
    """Downloads 1 year of daily closing prices for the given tickers."""
    print(f"Downloading 1-year historical data for {len(tickers)} stocks...")
    print("This might take a minute, please wait...\n")

    data = yf.download(tickers, period="1y", progress=False)["Close"]
    return data


def rank_stocks(data, timeframe):
    """Calculates percentage change and returns top 10 and bottom 10 stocks."""
    timeframes = {
        "1D": 1,  # 1 day ago
        "1W": 5,  # 1 week ago (5 trading days)
        "1M": 21,  # 1 month ago (~21 trading days)
        "3M": 63,  # 3 months ago (~63 trading days)
        "6M": 126,  # 6 months ago (~126 trading days)
        "1Y": 252,  # 1 year ago (~252 trading days)
    }

    days_back = timeframes[timeframe]

    if len(data) <= days_back:
        print(f"Not enough data to calculate {timeframe}.")
        return None, None

    latest_prices = data.iloc[-1]
    past_prices = data.iloc[-(days_back + 1)]  # +1 because -1 is current day
    pct_change = ((latest_prices - past_prices) / past_prices) * 100
    pct_change = pct_change.dropna()

    sorted_changes = pct_change.sort_values(ascending=False)
    top_10 = sorted_changes.head(10)
    bottom_10 = sorted_changes.tail(10)
    return top_10, bottom_10


def display_results(top_10, bottom_10, timeframe):
    """Prints the results in a readable format."""
    print(f"\n{'=' * 40}")
    print(f" MARKET MOVERS: {timeframe} TIMEFRAME")
    print(f"{'=' * 40}")

    print("\nTOP 10 GAINERS:")
    print("-" * 25)
    for i, (ticker, pct) in enumerate(top_10.items(), 1):
        print(f"{i:>2}. {ticker:<6} | +{pct:.2f}%")

    print("\nBOTTOM 10 LOSERS:")
    print("-" * 25)
    for i, (ticker, pct) in enumerate(bottom_10.items(), 1):
        print(f"{i:>2}. {ticker:<6} | {pct:.2f}%")
    print(f"{'=' * 40}\n")


def main():
    print("Welcome to the Python Stock Ranker!")
    try:
        tickers = get_sp500_tickers()
        historical_data = download_data(tickers)
    except Exception as exc:
        print(f"Startup failed: {exc}")
        return

    while True:
        print("Available timeframes: 1D, 1W, 1M, 3M, 6M, 1Y")
        user_input = input("Enter a timeframe (or type 'exit' to quit): ").strip().upper()

        if user_input == "EXIT":
            print("Exiting the program. Happy trading!")
            break

        if user_input not in ["1D", "1W", "1M", "3M", "6M", "1Y"]:
            print("Invalid input. Please choose from the list.\n")
            continue

        top_10, bottom_10 = rank_stocks(historical_data, user_input)
        if top_10 is not None and bottom_10 is not None:
            display_results(top_10, bottom_10, user_input)


if __name__ == "__main__":
    main()
import yfinance as yf
import pandas as pd
import warnings

# Suppress warnings for cleaner console output
warnings.filterwarnings('ignore')

def get_sp500_tickers():
    """Fetches the current S&P 500 tickers from Wikipedia."""
    print("Fetching S&P 500 tickers from Wikipedia...")
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    # lxml is required under the hood for pd.read_html
    table = pd.read_html(url)[0]
    tickers = table['Symbol'].tolist()
    
    # yfinance uses dashes instead of dots for tickers like BRK.B
    tickers = [ticker.replace('.', '-') for ticker in tickers]
    return tickers

def download_data(tickers):
    """Downloads 1 year of daily closing prices for the given tickers."""
    print(f"Downloading 1-year historical data for {len(tickers)} stocks...")
    print("This might take a minute, please wait...\n")
    
    # Download 1 year of data. Group by ticker.
    data = yf.download(tickers, period='1y', progress=False)['Close']
    return data

def rank_stocks(data, timeframe):
    """Calculates percentage change and returns top 10 and bottom 10 stocks."""
    # Approximate trading days for each timeframe
    timeframes = {
        '1D': 1,     # 1 day ago
        '1W': 5,     # 1 week ago (5 trading days)
        '1M': 21,    # 1 month ago (~21 trading days)
        '3M': 63,    # 3 months ago (~63 trading days)
        '6M': 126,   # 6 months ago (~126 trading days)
        '1Y': 252    # 1 year ago (~252 trading days)
    }
    
    days_back = timeframes[timeframe]
    
    # Ensure we have enough data points
    if len(data) <= days_back:
        print(f"Not enough data to calculate {timeframe}.")
        return None, None

    # Get the latest price and the historical price
    latest_prices = data.iloc[-1]
    past_prices = data.iloc[-(days_back + 1)] # +1 because -1 is the current day

    # Calculate percentage change
    pct_change = ((latest_prices - past_prices) / past_prices) * 100
    
    # Drop any NaN values (stocks that might not have existed or had missing data)
    pct_change = pct_change.dropna()
    
    # Sort the values
    sorted_changes = pct_change.sort_values(ascending=False)
    
    # Extract top 10 and bottom 10
    top_10 = sorted_changes.head(10)
    bottom_10 = sorted_changes.tail(10)
    
    return top_10, bottom_10

def display_results(top_10, bottom_10, timeframe):
    """Prints the results in a readable format."""
    print(f"\n{'='*40}")
    print(f" MARKET MOVERS: {timeframe} TIMEFRAME")
    print(f"{'='*40}")
    
    print("\n🟢 TOP 10 GAINERS:")
    print("-" * 25)
    for i, (ticker, pct) in enumerate(top_10.items(), 1):
        print(f"{i:>2}. {ticker:<6} | +{pct:.2f}%")
        
    print("\n🔴 BOTTOM 10 LOSERS:")
    print("-" * 25)
    for i, (ticker, pct) in enumerate(bottom_10.items(), 1):
        print(f"{i:>2}. {ticker:<6} | {pct:.2f}%")
    print(f"{'='*40}\n")

def main():
    print("Welcome to the Python Stock Ranker!")
    tickers = get_sp500_tickers()
    
    # Download data once to keep the loop fast
    historical_data = download_data(tickers)
    
    while True:
        print("Available timeframes: 1D, 1W, 1M, 3M, 6M, 1Y")
        user_input = input("Enter a timeframe (or type 'exit' to quit): ").strip().upper()
        
        if user_input == 'EXIT':
            print("Exiting the program. Happy trading!")
            break
            
        if user_input not in ['1D', '1W', '1M', '3M', '6M', '1Y']:
            print("Invalid input. Please choose from the list.\n")
            continue
            
        top_10, bottom_10 = rank_stocks(historical_data, user_input)
        
        if top_10 is not None and bottom_10 is not None:
            display_results(top_10, bottom_10, user_input)

if __name__ == "__main__":
    main()