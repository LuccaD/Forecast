from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from forecast_engine import run_forecast


APP_DIR = Path(__file__).resolve().parent


class ForecastApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.output_path: Path | None = None

        root.title("Forecast de Produção")
        root.geometry("760x620")
        root.minsize(680, 560)

        self.history_path = tk.StringVar(value=self._default_input("Prod_RV_2026.xlsx"))
        self.agenda_path = tk.StringVar(value=self._default_input("PACE RV 25-30 (1).xlsx"))
        self.output_dir = tk.StringVar(value=str(self._default_output_dir()))

        self._build_ui()
        root.after(100, self._process_events)

    def _default_input(self, filename: str) -> str:
        candidates = list((APP_DIR / "Base").glob(filename.replace(" (1)", "*")))
        return str(candidates[0]) if candidates else ""

    def _default_output_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path.home() / "Documents" / "ForecastProducao"
        return APP_DIR / "Saidas"

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        container = ttk.Frame(self.root, padding=22)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        ttk.Label(container, text="Forecast de Produção", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            container,
            text="Selecione a base histórica e a agenda futura para gerar a previsão.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 20))

        self._file_row(container, 2, "Base histórica de treino", self.history_path, self._browse_history)
        self._file_row(container, 4, "Agenda futura", self.agenda_path, self._browse_agenda)
        self._file_row(container, 6, "Pasta de saída", self.output_dir, self._browse_output)

        actions = ttk.Frame(container)
        actions.grid(row=8, column=0, sticky="ew", pady=(18, 12))
        actions.columnconfigure(0, weight=1)
        self.run_button = ttk.Button(actions, text="Executar previsão", command=self._start)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.open_button = ttk.Button(
            actions, text="Abrir última saída", command=self._open_output, state="disabled"
        )
        self.open_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=160)
        self.progress.grid(row=0, column=2, sticky="e", padx=(14, 0))

        ttk.Label(container, text="Andamento e arquivos gerados").grid(row=9, column=0, sticky="w")
        self.log = tk.Text(container, height=18, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.grid(row=10, column=0, sticky="nsew", pady=(6, 0))
        container.rowconfigure(10, weight=1)

    def _file_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        browse_command,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(8, 3))
        field = ttk.Frame(parent)
        field.grid(row=row + 1, column=0, sticky="ew")
        field.columnconfigure(0, weight=1)
        ttk.Entry(field, textvariable=variable).grid(row=0, column=0, sticky="ew")
        ttk.Button(field, text="Procurar...", command=browse_command).grid(row=0, column=1, padx=(8, 0))

    def _browse_history(self) -> None:
        self._browse_excel(self.history_path)

    def _browse_agenda(self) -> None:
        self._browse_excel(self.agenda_path)

    def _browse_excel(self, variable: tk.StringVar) -> None:
        selected = filedialog.askopenfilename(
            title="Selecionar planilha Excel",
            filetypes=[("Planilhas Excel", "*.xlsx *.xlsm"), ("Todos os arquivos", "*.*")],
        )
        if selected:
            variable.set(selected)

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(title="Selecionar pasta de saída")
        if selected:
            self.output_dir.set(selected)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        history = Path(self.history_path.get().strip())
        agenda = Path(self.agenda_path.get().strip())
        output_value = self.output_dir.get().strip()
        if not history.is_file():
            messagebox.showerror("Arquivo necessário", "Selecione uma base histórica existente.")
            return
        if not agenda.is_file():
            messagebox.showerror("Arquivo necessário", "Selecione uma agenda futura existente.")
            return
        if not output_value:
            messagebox.showerror("Pasta necessária", "Selecione uma pasta de saída.")
            return
        output = Path(output_value)

        self.run_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.progress.start(12)
        self._append_log("Iniciando treinamento e previsão...")

        def worker() -> None:
            try:
                result = run_forecast(history, agenda, output, progress=self.events.put)
                self.events.put(("success", result))
            except Exception as error:
                self.events.put(("error", error))

        threading.Thread(target=worker, daemon=True).start()

    def _process_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event == "progress":
                self._append_log(str(payload))
            elif event == "success":
                result = payload
                self.output_path = result["output_dir"]
                self._append_log(f"Modelo selecionado: {result['best_model']}")
                self._append_log(f"Receita estimada: R$ {result['estimated_revenue']:,.2f}")
                self._append_log("Materiais gerados:")
                for artifact in result["artifacts"]:
                    self._append_log(f"  {artifact.name}")
                self._append_log(f"Pasta: {self.output_path}")
                self.open_button.configure(state="normal")
                self._finish_run()
                messagebox.showinfo("Previsão concluída", f"Arquivos salvos em:\n{self.output_path}")
            elif event == "error":
                self._append_log(f"Falha: {payload}")
                self._finish_run()
                messagebox.showerror("Falha na previsão", str(payload))

        self.root.after(100, self._process_events)

    def _finish_run(self) -> None:
        self.progress.stop()
        self.run_button.configure(state="normal")

    def _open_output(self) -> None:
        if self.output_path and self.output_path.is_dir():
            os.startfile(self.output_path)


def main() -> None:
    root = tk.Tk()
    ForecastApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()