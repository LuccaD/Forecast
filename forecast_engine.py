from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Callable

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


RANDOM_STATE = 42
CATEGORICAL_FEATURES = ["item_base", "Convênio", "Categoria", "dose_unidade"]
NUMERIC_FEATURES = ["Qtd.", "dose_valor", "tem_dose"]
DOSE_PATTERN = re.compile(r"(\d+[.,]?\d*)\s*(MG|ML|MCG|G|UI)\b", re.IGNORECASE)


def converter_data_os(serie: pd.Series) -> pd.Series:
    """Converte datas textuais, datetime e números seriais do Excel."""
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_datetime(serie, errors="coerce", unit="D", origin="1899-12-30")
    return pd.to_datetime(serie, errors="coerce", dayfirst=True)


def wmape(y_true, y_pred) -> float:
    valores_reais = np.asarray(y_true)
    previsoes = np.asarray(y_pred)
    denominador = np.sum(np.abs(valores_reais))
    if denominador == 0:
        return float("nan")
    return float(np.sum(np.abs(valores_reais - previsoes)) / denominador)


def extrair_dose_e_nome(item_os: str) -> tuple[str, float | None, str | None]:
    item_os = str(item_os).strip()
    match = DOSE_PATTERN.search(item_os)
    if not match:
        return item_os, None, None
    dose_valor = float(match.group(1).replace(",", "."))
    dose_unidade = match.group(2).upper()
    nome_base = (item_os[: match.start()] + item_os[match.end() :]).strip(" -/")
    nome_base = re.sub(r"\s+", " ", nome_base)
    return nome_base or item_os, dose_valor, dose_unidade


def preparar_agenda_futura(agenda_df: pd.DataFrame) -> pd.DataFrame:
    """Prepara a agenda com as mesmas variáveis usadas no treinamento."""
    agenda_df = agenda_df.copy()
    obrigatorias = ["Item da OS", "Qtd.", "Convênio"]
    faltantes = [coluna for coluna in obrigatorias if coluna not in agenda_df.columns]
    if faltantes:
        raise ValueError(f"A agenda futura está sem as colunas obrigatórias: {faltantes}")
    if agenda_df.empty:
        raise ValueError("A agenda futura não contém registros para prever.")

    if "Categoria" not in agenda_df.columns:
        agenda_df["Categoria"] = "SEM_CATEGORIA"
    agenda_df["Item da OS"] = agenda_df["Item da OS"].fillna("").astype(str).str.strip()
    agenda_df["Qtd."] = pd.to_numeric(agenda_df["Qtd."], errors="coerce")
    agenda_df["Convênio"] = agenda_df["Convênio"].fillna("NÃO INFORMADO").astype(str)
    agenda_df["Categoria"] = agenda_df["Categoria"].fillna("SEM_CATEGORIA").astype(str)

    dados_item = agenda_df["Item da OS"].map(extrair_dose_e_nome)
    agenda_df[["item_base", "dose_valor", "dose_unidade"]] = pd.DataFrame(
        dados_item.tolist(), index=agenda_df.index
    )
    agenda_df["tem_dose"] = agenda_df["dose_valor"].notna().astype(int)
    agenda_df["dose_unidade"] = agenda_df["dose_unidade"].fillna("SEM_DOSE").astype(str)
    for coluna in CATEGORICAL_FEATURES:
        agenda_df[coluna] = agenda_df[coluna].fillna("SEM_INFORMACAO").astype(str)
    return agenda_df


def _make_preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [
            ("categoricas", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            (
                "numericas",
                Pipeline(
                    [
                        ("imputar", SimpleImputer(strategy="median")),
                        ("escalar", StandardScaler()),
                    ]
                ),
                NUMERIC_FEATURES,
            ),
        ]
    )


def _artifact_path(output_dir: Path, number: int, label: str, timestamp: str, suffix: str) -> Path:
    return output_dir / f"{number:02d}_{label}_{timestamp}.{suffix}"


def run_forecast(
    historical_path: str | Path,
    future_path: str | Path,
    output_dir: str | Path,
    progress: Callable[[tuple[str, str]], object] | None = None,
) -> dict[str, object]:
    """Treina, valida e prevê; importar este módulo não inicia processamento."""

    def report(message: str) -> None:
        if progress:
            progress(("progress", message))

    historical_path = Path(historical_path)
    future_path = Path(future_path)
    output_root = Path(output_dir)
    for path, label in ((historical_path, "base histórica"), (future_path, "agenda futura")):
        if not path.is_file():
            raise FileNotFoundError(f"Arquivo da {label} não encontrado: {path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"execucao_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    report(f"Lendo base histórica: {historical_path.name}")
    df = pd.read_excel(historical_path)

    required_columns = ["Data OS", "Item da OS", "Qtd.", "Receita", "Convênio"]
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise ValueError(f"A base histórica está sem as colunas obrigatórias: {missing}")
    df = df.dropna(subset=["Item da OS", "Qtd.", "Receita", "Convênio"]).copy()
    df["Data OS"] = converter_data_os(df["Data OS"])
    df = df.dropna(subset=["Data OS"]).copy()
    df["Qtd."] = pd.to_numeric(df["Qtd."], errors="coerce")
    df["Receita"] = pd.to_numeric(df["Receita"], errors="coerce")
    df = df.dropna(subset=["Qtd.", "Receita"])
    df = df[(df["Qtd."] > 0) & (df["Receita"] > 0.01)].copy()
    if df.empty:
        raise ValueError("A base histórica ficou sem registros válidos após a limpeza.")
    if "Categoria" not in df.columns:
        df["Categoria"] = "SEM_CATEGORIA"

    dados_item = df["Item da OS"].map(extrair_dose_e_nome)
    df[["item_base", "dose_valor", "dose_unidade"]] = pd.DataFrame(
        dados_item.tolist(), index=df.index
    )
    df["tem_dose"] = df["dose_valor"].notna().astype(int)
    df["dose_unidade"] = df["dose_unidade"].fillna("SEM_DOSE").astype(str)
    for column in CATEGORICAL_FEATURES:
        df[column] = df[column].fillna("SEM_INFORMACAO").astype(str)

    df["competencia"] = df["Data OS"].dt.to_period("M")
    competencias = sorted(df["competencia"].unique())
    if len(competencias) < 2:
        raise ValueError("São necessários pelo menos dois meses válidos na base histórica.")

    feature_columns = CATEGORICAL_FEATURES + NUMERIC_FEATURES
    X = df[feature_columns]
    y = df["Receita"]
    last_month = competencias[-1]
    train_mask = df["competencia"] < last_month
    test_mask = df["competencia"] == last_month
    X_train, X_test = X.loc[train_mask], X.loc[test_mask]
    y_train, y_test = y.loc[train_mask], y.loc[test_mask]
    if X_train.empty or X_test.empty:
        raise ValueError("Não foi possível separar treino e teste por competência mensal.")

    models = {
        "Regressao_Linear": LinearRegression(),
        "Random_Forest": RandomForestRegressor(
            n_estimators=300, random_state=RANDOM_STATE, n_jobs=-1
        ),
        "Gradient_Boosting": GradientBoostingRegressor(random_state=RANDOM_STATE),
    }
    preprocessor = _make_preprocessor()
    report(f"Validando modelos em {len(competencias) - 1} competências...")
    temporal_validations = []
    for validation_month in competencias[1:]:
        validation_train = df["competencia"] < validation_month
        validation_test = df["competencia"] == validation_month
        for name, model in models.items():
            pipeline = Pipeline(
                [("preprocessamento", clone(preprocessor)), ("modelo", clone(model))]
            )
            pipeline.fit(X.loc[validation_train], y.loc[validation_train])
            predictions = pipeline.predict(X.loc[validation_test])
            temporal_validations.append(
                {
                    "mes_teste": str(validation_month),
                    "modelo": name,
                    "MAE": mean_absolute_error(y.loc[validation_test], predictions),
                    "WMAPE": wmape(y.loc[validation_test], predictions),
                }
            )

    temporal_df = pd.DataFrame(temporal_validations)
    temporal_summary = (
        temporal_df.groupby("modelo", as_index=False)
        .agg(MAE_medio=("MAE", "mean"), WMAPE_medio=("WMAPE", "mean"), MAE_desvio=("MAE", "std"))
        .sort_values(["MAE_medio", "WMAPE_medio"])
        .reset_index(drop=True)
    )
    artifacts: list[Path] = []

    def save_frame(frame: pd.DataFrame, number: int, label: str) -> None:
        path = _artifact_path(run_dir, number, label, timestamp, "xlsx")
        frame.to_excel(path, index=False)
        artifacts.append(path)

    report("Gravando relatórios de validação...")
    save_frame(temporal_df, 1, "validacao_temporal")
    save_frame(temporal_summary, 2, "resumo_validacao_temporal")

    plot_data = temporal_df.pivot(index="mes_teste", columns="modelo", values="WMAPE").sort_index()
    axis = plot_data.plot(kind="line", marker="o", figsize=(10, 6), linewidth=2)
    axis.set_title("Variação do WMAPE por mês testado")
    axis.set_xlabel("Mês testado")
    axis.set_ylabel("WMAPE")
    axis.yaxis.set_major_formatter(lambda value, position: f"{value:.1%}")
    axis.grid(True, alpha=0.3)
    axis.legend(title="Modelo")
    figure = axis.get_figure()
    figure.tight_layout()
    chart_path = _artifact_path(run_dir, 3, "variacao_wmape_modelos", timestamp, "png")
    figure.savefig(chart_path, dpi=150)
    plt.close(figure)
    artifacts.append(chart_path)

    report("Treinando os modelos e prevendo a agenda...")
    scores: dict[str, dict[str, float]] = {}
    trained_pipelines = {}
    for index, (name, model) in enumerate(models.items(), start=4):
        pipeline = Pipeline([("preprocessamento", clone(preprocessor)), ("modelo", clone(model))])
        pipeline.fit(X_train, y_train)
        predictions = pipeline.predict(X_test)
        scores[name] = {
            "MAE": float(mean_absolute_error(y_test, predictions)),
            "WMAPE": wmape(y_test, predictions),
        }
        trained_pipelines[name] = pipeline
        comparison = pd.DataFrame(
            {"Receita_real": y_test.reset_index(drop=True), "Receita_prevista": predictions}
        )
        comparison["Erro_absoluto"] = (
            comparison["Receita_real"] - comparison["Receita_prevista"]
        ).abs()
        comparison = comparison.sort_values("Erro_absoluto", ascending=False).reset_index(drop=True)
        save_frame(comparison, index, f"comparacao_{name.lower()}")

    agenda = preparar_agenda_futura(pd.read_excel(future_path))
    revenues = {
        name: float(pipeline.predict(agenda[feature_columns]).sum())
        for name, pipeline in trained_pipelines.items()
    }
    model_summary = pd.DataFrame(
        [
            {
                "modelo": name,
                "MAE": scores[name]["MAE"],
                "WMAPE": scores[name]["WMAPE"],
                "receita_total_estimativa": revenues[name],
            }
            for name in scores
        ]
    ).sort_values(["MAE", "WMAPE"]).reset_index(drop=True)
    save_frame(model_summary, 7, "resumo_modelos")

    best_model = str(temporal_summary.iloc[0]["modelo"])
    best_pipeline = trained_pipelines[best_model]
    forecast = agenda.copy()
    forecast["receita_estimada"] = best_pipeline.predict(agenda[feature_columns])
    save_frame(forecast, 8, "previsao_agenda")

    model_path = _artifact_path(run_dir, 9, "modelo_forecast_producao", timestamp, "pkl")
    joblib.dump(best_pipeline, model_path)
    artifacts.append(model_path)

    manifest_path = _artifact_path(run_dir, 10, "indice_materiais", timestamp, "txt")
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest_lines = [f"Materiais gerados em: {created_at}", f"Modelo selecionado: {best_model}", ""]
    manifest_lines.extend(
        f"{number:02d}. {path.name} | criado em {created_at}"
        for number, path in enumerate(artifacts, 1)
    )
    manifest_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    artifacts.append(manifest_path)
    report(f"Concluído. Materiais salvos em {run_dir}")

    return {
        "output_dir": run_dir,
        "artifacts": artifacts,
        "best_model": best_model,
        "estimated_revenue": float(forecast["receita_estimada"].sum()),
    }