"""
Verificador de atualização de painéis Power BI.

Consulta a API REST do Power BI para saber como terminou a última
atualização (refresh) do modelo semântico (dataset) de cada painel,
classifica o resultado (OK / Atualizando / ERRO / NI), traduz as mensagens
de erro mais comuns e grava um relatório em CSV. Também permite disparar
a atualização de um dataset.

Uso:
    python verificador_powerbi.py --demo              # dados fictícios, sem conta
    python verificador_powerbi.py                     # modo real (lê o .env)
    python verificador_powerbi.py atualizar <id_dataset>

Todas as credenciais e IDs vêm de variáveis de ambiente (veja .env.example).
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PASTA_PROJETO = Path(__file__).resolve().parent
PASTA_EXEMPLOS = PASTA_PROJETO / "exemplos"

URL_API = "https://api.powerbi.com/v1.0/myorg"
ESCOPO_POWERBI = "https://analysis.windows.net/powerbi/api/.default"
VALIDADE_TOKEN_MIN = 60  # mesmo critério do script original: token com mais de 60 min é renovado


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

def carregar_env(caminho=PASTA_PROJETO / ".env"):
    """Carrega um arquivo .env simples (CHAVE=valor) sem sobrescrever o ambiente."""
    if not Path(caminho).exists():
        return
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


CREDENCIAIS = ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET")


def obter_credencial(nome):
    """
    Ponto único de leitura de credenciais.

    Hoje lê a variável de ambiente `nome` (ou o .env). Para usar um cofre de
    senhas (Azure Key Vault, AWS Secrets Manager, keyring etc.), basta trocar
    a implementação desta função; o resto do código não muda.
    """
    return os.environ.get(nome, "")


def credenciais_configuradas():
    """Indica se as credenciais do service principal estão preenchidas."""
    return all(obter_credencial(nome) for nome in CREDENCIAIS)


def carregar_paineis(caminho):
    """Lê a lista de painéis (colunas: nome, id_dataset) de um CSV."""
    with open(caminho, newline="", encoding="utf-8") as arquivo:
        return [
            {"nome": linha["nome"].strip(), "id_dataset": linha["id_dataset"].strip()}
            for linha in csv.DictReader(arquivo)
            if linha.get("id_dataset", "").strip()
        ]


def _url_refreshes(dataset_id):
    """Monta o endpoint de refreshes; usa o workspace (group) se PBI_GROUP_ID estiver definido."""
    grupo = os.environ.get("PBI_GROUP_ID")
    if grupo:
        return f"{URL_API}/groups/{grupo}/datasets/{dataset_id}/refreshes"
    return f"{URL_API}/datasets/{dataset_id}/refreshes"


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def obter_token(forcar_novo=False):
    """
    Retorna um token de acesso à API do Power BI.

    Usa o fluxo client credentials (service principal / app registration).
    O token fica em cache num arquivo local (PBI_TOKEN_CACHE, padrão
    ".token_cache", ignorado pelo git) e é renovado quando o arquivo tem
    mais de 60 minutos, como no script original.
    """
    import requests

    cache = Path(os.environ.get("PBI_TOKEN_CACHE") or PASTA_PROJETO / ".token_cache")
    if cache.exists() and not forcar_novo:
        idade_min = (time.time() - cache.stat().st_mtime) / 60
        if idade_min <= VALIDADE_TOKEN_MIN:
            return cache.read_text(encoding="utf-8").strip()
        print("Token expirado... gerando um token novo")

    resposta = requests.post(
        f"https://login.microsoftonline.com/{obter_credencial('PBI_TENANT_ID')}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": obter_credencial("PBI_CLIENT_ID"),
            "client_secret": obter_credencial("PBI_CLIENT_SECRET"),
            "scope": ESCOPO_POWERBI,
        },
        timeout=30,
    )
    resposta.raise_for_status()
    token = resposta.json()["access_token"]
    cache.write_text(token, encoding="utf-8")
    return token


def _cabecalhos(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Consultas à API
# ---------------------------------------------------------------------------

def listar_atualizacoes(dataset_id, token=None, modo_demo=False):
    """
    Retorna o histórico de atualizações do dataset (lista "value" da API),
    da mais recente para a mais antiga.

    GET .../datasets/{dataset_id}/refreshes
    No modo demonstração lê exemplos/api/<dataset_id>.json.
    """
    if modo_demo:
        arquivo = PASTA_EXEMPLOS / "api" / f"{dataset_id}.json"
        resposta = json.loads(arquivo.read_text(encoding="utf-8"))  # FileNotFoundError -> NI
    else:
        import requests

        token = token or obter_token()
        r = requests.get(_url_refreshes(dataset_id), headers=_cabecalhos(token), timeout=60)
        r.raise_for_status()
        resposta = r.json()
    return resposta["value"]


def status_ultima_atualizacao(dataset_id, token=None, modo_demo=False):
    """
    Lê a atualização mais recente do dataset e devolve (status, erro_descricao, ult_att).

    - status: valor bruto da API (Completed, Failed, Canceled, Unknown...)
    - erro_descricao: campo errorDescription de serviceExceptionJson, se houver
    - ult_att: startTime convertido para o fuso local ("%Y-%m-%d %H:%M:%S")
    """
    latest = listar_atualizacoes(dataset_id, token=token, modo_demo=modo_demo)[0]

    status = latest.get("status", "Unknown")
    ult_att = None
    erro_descricao = None

    try:
        ult_att = datetime.fromisoformat(latest.get("startTime").replace("Z", "+00:00"))
        ult_att = ult_att.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (AttributeError, ValueError):
        pass

    erro = latest.get("serviceExceptionJson")
    if erro:
        try:
            erro_descricao = json.loads(erro).get("errorDescription")
        except ValueError:
            pass

    return status, erro_descricao, ult_att


def classificar_erro(erro):
    """Traduz a mensagem de erro da API para uma categoria legível."""
    erro = erro or ""
    if "dmconfig.ini" in erro:
        return "Número de licenças"
    if "ConnectionTimeoutError" in erro or "time-out" in erro:
        return "Tempo de consulta excedido"
    if "column does not exist" in erro:
        return "Erro no modelo semântico"
    if "killed" in erro:
        return "Atualização interrompida na origem"
    return "Outro"


def verificar_paineis(paineis, modo_demo=False):
    """
    Verifica cada painel e devolve uma lista de dicionários com
    Id, nome, id_dataset, status, erro, ult_at e dta_exec.

    Regras (as mesmas do script original):
      Completed -> OK
      Unknown   -> Atualizando (refresh em andamento)
      outro     -> ERRO, com o erro classificado por classificar_erro()
      falha ao consultar (sem acesso, dataset inexistente...) -> NI
    """
    token = None if modo_demo else obter_token()
    dta_exec = datetime.today().strftime("%Y%m%d")
    resultado = []

    for i, painel in enumerate(paineis, start=1):
        linha = {"Id": i, "nome": painel["nome"], "id_dataset": painel["id_dataset"],
                 "status": "NI", "erro": None, "ult_at": None, "dta_exec": dta_exec}
        try:
            status, erro, ult_att = status_ultima_atualizacao(painel["id_dataset"], token, modo_demo)
            if status == "Completed":
                linha["status"] = "OK"
            elif status == "Unknown":
                linha["status"] = "Atualizando"
            else:
                linha["status"] = "ERRO"
                erro = classificar_erro(erro)
            linha["erro"] = erro
            linha["ult_at"] = ult_att
        except Exception as exc:  # painel não consultado
            linha["erro"] = None
            print(f"  aviso: painel {i} não consultado ({type(exc).__name__})")
        resultado.append(linha)
        print(f"verificou painel {i}")

    return resultado


def atualizar_painel(dataset_id, token=None, modo_demo=False):
    """
    Dispara a atualização do dataset.

    POST .../datasets/{dataset_id}/refreshes  (a API responde 202 Accepted)
    No modo demonstração apenas simula a chamada.
    """
    if modo_demo:
        print(f"[demo] POST {_url_refreshes(dataset_id)} -> 202 (simulado)")
        return 202
    import requests

    token = token or obter_token()
    r = requests.post(_url_refreshes(dataset_id), headers=_cabecalhos(token), timeout=60)
    r.raise_for_status()
    return r.status_code


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------

COLUNAS = ["Id", "nome", "id_dataset", "status", "erro", "ult_at", "dta_exec"]


def salvar_relatorio(linhas, caminho):
    """Grava o resultado em CSV (substitui o arquivo a cada execução)."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS)
        escritor.writeheader()
        escritor.writerows(linhas)


def imprimir_relatorio(linhas):
    """Mostra nome, status, erro e última atualização em formato de tabela."""
    largura = max(len(l["nome"]) for l in linhas) if linhas else 10
    print(f"\n{'nome':<{largura}}  {'status':<11}  {'erro':<34}  ult_at")
    print("-" * (largura + 70))
    for l in linhas:
        print(f"{l['nome']:<{largura}}  {l['status']:<11}  {str(l['erro'] or '-'):<34}  {l['ult_at'] or '-'}")
    contagem = {}
    for l in linhas:
        contagem[l["status"]] = contagem.get(l["status"], 0) + 1
    print("\nResumo: " + ", ".join(f"{k}={v}" for k, v in sorted(contagem.items())))


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------

def main(argv=None):
    carregar_env()
    parser = argparse.ArgumentParser(description="Verifica/atualiza painéis Power BI.")
    parser.add_argument("--demo", action="store_true",
                        help="usa respostas fictícias de exemplos/api (não precisa de conta)")
    sub = parser.add_subparsers(dest="comando")
    p_ver = sub.add_parser("verificar", help="verifica todos os painéis (padrão)")
    p_ver.add_argument("--paineis", help="CSV com nome,id_dataset")
    p_ver.add_argument("--saida", help="CSV de saída")
    p_att = sub.add_parser("atualizar", help="dispara a atualização de um dataset")
    p_att.add_argument("id_dataset")
    args = parser.parse_args(argv)

    modo_demo = args.demo or not credenciais_configuradas()
    if modo_demo and not args.demo:
        print("Credenciais não encontradas: executando em MODO DEMONSTRAÇÃO (dados fictícios).")

    if args.comando == "atualizar":
        codigo = atualizar_painel(args.id_dataset, modo_demo=modo_demo)
        print(f"Atualização solicitada (HTTP {codigo}).")
        return 0

    inicio = datetime.now()
    padrao_paineis = PASTA_EXEMPLOS / "paineis_exemplo.csv" if modo_demo else "paineis.csv"
    arquivo_paineis = getattr(args, "paineis", None) or os.environ.get("PAINEIS_ARQUIVO") or padrao_paineis
    arquivo_saida = getattr(args, "saida", None) or os.environ.get("SAIDA_ARQUIVO") or "saida/status_paineis.csv"

    linhas = verificar_paineis(carregar_paineis(arquivo_paineis), modo_demo=modo_demo)
    imprimir_relatorio(linhas)
    salvar_relatorio(linhas, arquivo_saida)
    print(f"\nRelatório salvo em {arquivo_saida}")
    print(f"Tempo de execução: {datetime.now() - inicio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
