# Forecast de Produção

Aplicação desktop para treinar os modelos de previsão de receita e estimar uma agenda futura. A interface permite selecionar uma base histórica, uma agenda e a pasta de saída.

## Executar pelo código-fonte

Requer Python 3.12 ou superior no Windows.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Também é possível importar a lógica sem abrir a interface:

```python
from forecast_engine import run_forecast

resultado = run_forecast("historico.xlsx", "agenda.xlsx", "Saidas")
```

A base histórica deve conter `Data OS`, `Item da OS`, `Qtd.`, `Receita` e `Convênio`. A agenda deve conter `Item da OS`, `Qtd.` e `Convênio`; `Categoria` é opcional.

Cada execução cria uma pasta `execucao_YYYYMMDD_HHMMSS` no destino, com relatórios numerados e datados, o modelo treinado e um índice dos materiais gerados.

## Gerar o executável

No Windows, execute `build.bat`. O PyInstaller cria `dist\ForecastProducao.exe`. O aplicativo pede as planilhas ao iniciar; elas não precisam ser incluídas no executável.
