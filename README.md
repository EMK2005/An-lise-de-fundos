# Analisador de fundos de investimento

App em Streamlit que recebe o PDF de um fundo (lâmina, fact sheet ou relatório) e
gera uma análise visual detalhada usando a API da Claude.

## Como funciona

1. Você anexa o PDF na interface
2. O PDF é enviado direto pra Claude (não extraímos texto manualmente — o modelo lê
   o PDF nativamente, incluindo tabelas e gráficos)
3. A Claude retorna um JSON estruturado com os dados do fundo (rentabilidade, risco,
   taxas, composição da carteira etc.)
4. Esse JSON preenche um template HTML fixo (`template.html`) com os gráficos —
   assim toda análise sai com a mesma identidade visual, só os dados mudam

## Instalação

```bash
pip install -r requirements.txt
```

Copie o arquivo de exemplo de secrets e coloque sua chave da API:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# depois edite .streamlit/secrets.toml e cole sua chave (começa com sk-ant-...)
```

Você consegue uma chave em https://console.anthropic.com/settings/keys

## Rodando

```bash
streamlit run app.py
```

## Estrutura

- `app.py` — interface Streamlit, chamada da API, cache por hash do arquivo
- `prompt.py` — prompt de extração e o schema JSON pedido pro modelo
- `template.html` — template Jinja2 do relatório, com Chart.js para os gráficos

## Personalizando

- **Mudar o schema de dados**: edite `EXTRACTION_PROMPT` em `prompt.py` e ajuste o
  `template.html` para usar os novos campos
- **Mudar o visual**: as cores e fontes ficam nas variáveis CSS no topo do
  `template.html` (`:root { --navy: ...; --gold: ...; }`)
- **Trocar o modelo**: mude a constante `MODEL` em `app.py` — `claude-haiku-4-5-20251001`
  é mais barato se quiser cortar custo ainda mais; `claude-opus-4-8` dá análises
  mais aprofundadas se precisar

## Custo estimado

Com o modelo padrão (Sonnet 5) e o volume de poucas análises por semana, o custo de
API fica na casa de centavos de dólar por mês — o cache por hash evita reprocessar
o mesmo PDF duas vezes.
