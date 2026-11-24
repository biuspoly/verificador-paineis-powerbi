# Verificador de painéis Power BI

Script em Python que monitora a atualização (refresh) dos modelos semânticos
(datasets) por trás dos painéis Power BI de uma empresa e gera um relatório
diário com o status de cada um. Também permite disparar a atualização de um
dataset pela linha de comando.

Nasceu da rotina de uma distribuidora de bebidas com dezenas de painéis
(vendas diárias, crédito e prazo, markup, inventário, mapa comercial...): em
vez de abrir painel por painel no serviço do Power BI, o script consulta a
API REST e mostra de uma vez quais estão OK, quais falharam e por quê.

> **Aviso:** todos os nomes de painéis, IDs, horários e mensagens de erro deste
> repositório são **fictícios**. Nenhuma credencial ou dado real está incluído.

## O que ele faz

1. Lê a lista de painéis (`nome`, `id_dataset`) de um CSV.
2. Para cada dataset, consulta `GET /datasets/{id}/refreshes` e pega a
   atualização mais recente.
3. Classifica o resultado:

   | Status da API | Resultado     |
   |---------------|---------------|
   | `Completed`   | **OK**        |
   | `Unknown`     | **Atualizando** (refresh em andamento) |
   | outro (`Failed`, `Canceled`...) | **ERRO** |
   | falha na consulta (sem permissão, dataset inexistente...) | **NI** (não identificado) |

4. Para os erros, traduz a mensagem técnica (`serviceExceptionJson`) em uma
   categoria: *Número de licenças*, *Tempo de consulta excedido*,
   *Erro no modelo semântico*, *Atualização interrompida na origem* ou *Outro*.
5. Mostra a tabela no terminal e grava `saida/status_paineis.csv`
   (que pode alimentar um painel de monitoramento, um Shiny etc.).

## Funções

Tudo está em [`verificador_powerbi.py`](verificador_powerbi.py):

| Função | O que faz |
|---|---|
| `obter_credencial(nome)` | Ponto único de leitura de credenciais (hoje: variável de ambiente; troque aqui para usar um cofre de senhas) |
| `obter_token(forcar_novo=False)` | Token de acesso via service principal (client credentials), com cache local renovado a cada 60 min |
| `listar_atualizacoes(dataset_id, token=None, modo_demo=False)` | Histórico de refreshes do dataset (lista `value` da API) |
| `status_ultima_atualizacao(dataset_id, token=None, modo_demo=False)` | `(status, erro_descricao, ult_att)` da atualização mais recente |
| `classificar_erro(erro)` | Converte a mensagem de erro em categoria legível |
| `verificar_paineis(paineis, modo_demo=False)` | Aplica a verificação a uma lista de painéis e devolve o relatório |
| `atualizar_painel(dataset_id, token=None, modo_demo=False)` | Dispara o refresh (`POST /datasets/{id}/refreshes`, resposta 202) |
| `carregar_paineis(caminho)` / `salvar_relatorio(linhas, caminho)` | Leitura da lista de painéis e gravação do CSV |

As funções podem ser importadas em outro script:

```python
from verificador_powerbi import verificar_paineis, atualizar_painel

relatorio = verificar_paineis([{"nome": "Vendas", "id_dataset": "..."}])
for painel in relatorio:
    if painel["status"] == "ERRO":
        atualizar_painel(painel["id_dataset"])
```

## Modo demonstração (sem conta Power BI)

Não precisa instalar nada além do Python 3.9+:

```bash
python verificador_powerbi.py --demo
```

O modo demonstração lê respostas fictícias da API em `exemplos/api/` (no mesmo
formato JSON que a API do Power BI devolve) e a lista de painéis de
`exemplos/paineis_exemplo.csv`. Se as credenciais não estiverem configuradas,
o script entra nesse modo automaticamente. Um exemplo da saída gerada está em
`exemplos/status_paineis_exemplo.csv`.

Simular o disparo de uma atualização:

```bash
python verificador_powerbi.py --demo atualizar 00000000-0000-0000-0000-000000000001
```

## Modo real

### 1. Criar o service principal (app registration)

Em linhas gerais:

1. No portal do Azure / Microsoft Entra ID, crie um **App registration** e
   gere um **client secret**. Anote o *tenant ID*, o *client ID* e o segredo.
2. No **portal de administração do Power BI** (configurações de locatário),
   habilite *"Permitir que entidades de serviço usem as APIs do Power BI"*,
   de preferência restrito a um grupo de segurança que contenha o app.
3. Em cada **workspace** monitorado, adicione o app como membro (Membro ou
   Colaborador para poder disparar atualizações).

### 2. Configurar

```bash
pip install -r requirements.txt
cp .env.example .env        # preencha com os seus valores
```

Variáveis do `.env`:

| Variável | Tipo | Descrição |
|---|---|---|
| `PBI_TENANT_ID` | credencial | ID do diretório (tenant) no Microsoft Entra ID |
| `PBI_CLIENT_ID` | credencial | Application (client) ID do app registration |
| `PBI_CLIENT_SECRET` | credencial | Valor do client secret |
| `PBI_GROUP_ID` | opcional | ID do workspace (ver abaixo) |
| `PBI_TOKEN_CACHE` | opcional | Arquivo de cache do token (padrão `.token_cache`) |
| `PAINEIS_ARQUIVO` | opcional | Lista de painéis (padrão `paineis.csv`) |
| `SAIDA_ARQUIVO` | opcional | Relatório gerado (padrão `saida/status_paineis.csv`) |

As três credenciais são lidas só pela função `obter_credencial(nome)`. Para
buscá-las em um cofre de senhas (Azure Key Vault, AWS Secrets Manager,
`keyring`...) em vez de variáveis de ambiente, basta trocar o corpo dessa
função.

Crie `paineis.csv` (ignorado pelo git) com as colunas `nome,id_dataset`, no
mesmo formato de `exemplos/paineis_exemplo.csv`. O ID do dataset aparece na
URL do modelo semântico no serviço do Power BI.

Se os datasets estiverem em um workspace, defina `PBI_GROUP_ID` para usar o
endpoint `/groups/{id}/datasets/...` (recomendado com service principal).

### 3. Rodar

```bash
python verificador_powerbi.py                       # verifica todos
python verificador_powerbi.py verificar --paineis outro.csv --saida relatorio.csv
python verificador_powerbi.py atualizar <id_dataset>
```

Para rodar todos os dias, agende com o Agendador de Tarefas do Windows ou cron.

## Estrutura

```
verificador_powerbi.py      módulo + linha de comando
requirements.txt
.env.example                variáveis de ambiente (valores fictícios)
exemplos/
  paineis_exemplo.csv       lista fictícia de painéis
  status_paineis_exemplo.csv saída gerada no modo demonstração
  api/*.json                respostas fictícias da API de refreshes
```

## Histórico

A versão original eram três scripts separados (verificação, disparo de
atualização e obtenção do token) que gravavam o resultado em um banco MySQL.
Esta versão junta tudo em um único módulo com funções reutilizáveis, obtém o
token pelo fluxo oficial de service principal e grava o resultado em CSV.

## Autoria

Desenvolvido por **[biuspoly](https://github.com/biuspoly)**, com ajuda do Claude (Anthropic).
