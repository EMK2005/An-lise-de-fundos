import base64
import json
import hashlib

import streamlit as st
import anthropic
from jinja2 import Template

from prompt import EXTRACTION_PROMPT
from prompt_carteira import EXTRACTION_PROMPT_CARTEIRA

st.set_page_config(page_title="Analisador de investimentos", layout="wide")

MODEL = "claude-sonnet-5"

# Schema completo esperado pelo template. Qualquer chave ausente na resposta do
# modelo (por exemplo, uma análise feita antes de adicionarmos um campo novo)
# é preenchida com o valor default abaixo, em vez de quebrar o Jinja2.
DEFAULT_DATA = {
    "fundo": {
        "nome": None, "tipo": None, "classificacao_anbima": None, "gestora": None,
        "administrador": None, "publico_alvo": None, "data_referencia": None,
    },
    "resumo_executivo": None,
    "score_geral": None,
    "score_justificativa": None,
    "score_dimensoes": None,
    "rentabilidade": {
        "benchmark_nome": None, "serie_historica": [], "retornos_por_periodo": [],
        "rentabilidade_12m_fundo": None, "rentabilidade_12m_benchmark": None,
        "rentabilidade_24m_fundo": None, "rentabilidade_inicio_fundo": None,
        "percentual_do_benchmark": None,
    },
    "analise_oportunidade_cdi": None,
    "analise_risco": None,
    "risco": {
        "classificacao_risco": None, "escala_risco_1a5": None, "volatilidade_12m": None,
        "drawdown_maximo": None, "indice_sharpe": None,
    },
    "taxas": {
        "taxa_administracao": None, "taxa_performance": None, "taxa_entrada": None,
        "taxa_saida": None, "aplicacao_inicial_minima": None, "prazo_resgate": None,
    },
    "composicao_carteira": [],
    "patrimonio_liquido": None,
    "numero_cotistas": None,
    "pontos_positivos": [],
    "riscos_principais": [],
    "pontos_atencao": [],
}

# Schema de defaults para a análise de carteira.
DEFAULT_DATA_CARTEIRA = {
    "cliente": {"perfil_risco": None, "data_referencia": None, "patrimonio_total": None},
    "contexto_mercado": {
        "selic_meta": None, "cdi_anual": None, "ipca_12m": None, "data_referencia_indices": None,
    },
    "resumo_executivo": None,
    "ativos": [],
    "concentracao_por_categoria": [],
    "concentracao_por_instituicao": [],
    "top_10_posicoes": [],
    "distribuicao_qualidade": [],
    "leitura_diversificacao": None,
    "leitura_qualidade": None,
    "leitura_top10": None,
    "riscos_identificados": [],
    "destaques_positivos": [],
    "proximos_passos": [],
    "observacao_outliers": None,
}

# Defaults por ativo individual — protege o template contra ativos com campos
# ausentes (ex: uma versão futura do prompt que adicione um campo novo).
ASSET_DEFAULTS = {
    "nome": "Ativo não identificado",
    "categoria": None,
    "instituicao": None,
    "valor": None,
    "percentual_pl": 0,
    "rentabilidade_periodo": None,
    "isento_ir": False,
    "percentual_cdi_equivalente": None,
    "classificacao": "Em validação",
    "vencimento_ou_liquidez": None,
    "observacao": None,
}


def fill_defaults(data: dict, defaults: dict) -> dict:
    """Preenche recursivamente campos ausentes de `data` com os valores de `defaults`,
    sem sobrescrever nada que já exista."""
    data = data or {}
    result = {}
    for key, default_value in defaults.items():
        if isinstance(default_value, dict):
            result[key] = fill_defaults(data.get(key), default_value)
        else:
            result[key] = data.get(key, default_value)
    for key, value in data.items():
        if key not in result:
            result[key] = value
    return result


@st.cache_resource
def get_client():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])


class ResponseTruncatedError(Exception):
    """A resposta da Claude foi cortada por atingir o limite de max_tokens antes
    de terminar o JSON. Guarda o texto parcial pra facilitar o debug."""
    def __init__(self, partial_text: str):
        self.partial_text = partial_text
        super().__init__("Resposta cortada por atingir max_tokens")


def parse_json_response(raw_text: str) -> dict:
    """Remove eventuais cercas de markdown e faz o parse do JSON retornado pelo modelo."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text)


@st.cache_data(show_spinner=False)
def extract_fund_data(pdf_bytes: bytes, prompt_text: str) -> dict:
    """Envia o PDF pra Claude e retorna os dados estruturados do fundo.
    Cacheado por (hash do PDF + texto do prompt): reprocessar o mesmo PDF com o
    mesmo prompt não gasta tokens de novo, mas qualquer alteração no prompt.py
    invalida o cache automaticamente e força uma nova chamada à API."""
    client = get_client()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    message = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
    )

    raw_text = "".join(block.text for block in message.content if block.type == "text")

    if message.stop_reason == "max_tokens":
        raise ResponseTruncatedError(raw_text)

    return parse_json_response(raw_text)


@st.cache_data(show_spinner=False)
def extract_portfolio_data(pdf_bytes: bytes, prompt_text: str) -> dict:
    """Envia o PDF da carteira pra Claude e retorna os dados estruturados.
    Usa streaming (em vez de messages.create) porque carteiras com muitos ativos
    podem gerar respostas longas — streaming evita o limite prático de tokens de
    saída de chamadas não-streaming, permitindo um max_tokens bem mais alto."""
    client = get_client()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    with client.messages.stream(
        model=MODEL,
        max_tokens=24000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
    ) as stream:
        for _ in stream.text_stream:
            pass  # consome o stream; não precisamos exibir token a token na UI
        message = stream.get_final_message()

    raw_text = "".join(block.text for block in message.content if block.type == "text")

    if message.stop_reason == "max_tokens":
        raise ResponseTruncatedError(raw_text)

    return parse_json_response(raw_text)


# Mapeia a classificação textual de risco pra escala 1-5, usada como fallback
# quando o modelo não retorna o número (evita o medidor de risco ficar com uma
# cor que não bate com o texto, ex: "Baixo" pintado de âmbar).
def infer_risk_scale(classificacao: str | None):
    if not classificacao:
        return None
    label = classificacao.lower()
    if "baixíssimo" in label or "muito baixo" in label:
        return 1
    if "baixo" in label:
        return 2 if "moderado" in label else 1
    if "muito alto" in label:
        return 5
    if "alto" in label:
        return 3 if "moderado" in label else 4
    if "moderado" in label:
        return 3
    return None


def render_report(data: dict) -> str:
    data = fill_defaults(data, DEFAULT_DATA)
    if data["risco"]["escala_risco_1a5"] is None:
        data["risco"]["escala_risco_1a5"] = infer_risk_scale(data["risco"]["classificacao_risco"])
    with open("template.html", "r", encoding="utf-8") as f:
        template = Template(f.read())
    return template.render(data=data)


def render_portfolio_report(data: dict) -> str:
    data = fill_defaults(data, DEFAULT_DATA_CARTEIRA)
    data["ativos"] = [fill_defaults(a, ASSET_DEFAULTS) for a in data["ativos"]]
    with open("template_carteira.html", "r", encoding="utf-8") as f:
        template = Template(f.read())
    return template.render(data=data)


def main_fundo():
    st.caption(
        "Anexe a lâmina, fact sheet ou relatório em PDF de um fundo para gerar "
        "uma análise visual detalhada."
    )

    uploaded_file = st.file_uploader("PDF do fundo", type=["pdf"], key="upload_fundo")

    if not uploaded_file:
        return

    file_bytes = uploaded_file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()[:10]

    if st.button("Gerar análise", type="primary", key="btn_fundo"):
        with st.spinner("Lendo o documento e extraindo os dados com a Claude..."):
            try:
                data = extract_fund_data(file_bytes, EXTRACTION_PROMPT)
            except ResponseTruncatedError as e:
                st.error(
                    "A resposta da Claude foi cortada antes de terminar (o documento é "
                    "extenso e o limite de tokens de saída não foi suficiente). Se isso "
                    "persistir, pode ser necessário aumentar ainda mais o `max_tokens` "
                    "em app.py."
                )
                with st.expander("Ver resposta parcial (debug)"):
                    st.code(e.partial_text)
                return
            except json.JSONDecodeError:
                st.error(
                    "Não consegui interpretar a resposta do modelo em JSON. "
                    "Tente gerar novamente — às vezes ajuda re-rodar."
                )
                return
            except anthropic.APIError as e:
                st.error(f"Erro na chamada da API: {e}")
                return

        st.session_state[f"report_fundo_{file_hash}"] = data

    report_data = st.session_state.get(f"report_fundo_{file_hash}")

    if report_data:
        html = render_report(report_data)

        col1, col2 = st.columns([1, 4])
        with col1:
            st.download_button(
                "⬇️ Baixar relatório (HTML)",
                data=html,
                file_name=f"analise_{report_data.get('fundo', {}).get('nome', 'fundo')}.html",
                mime="text/html",
                key="download_fundo",
            )
        with col2:
            with st.expander("Ver dados extraídos (JSON)"):
                st.json(report_data)

        st.components.v1.html(html, height=2600, scrolling=True)


def main_carteira():
    st.caption(
        "Anexe o extrato/posição consolidada em PDF da carteira para gerar uma "
        "análise completa de risco, concentração e qualidade dos ativos."
    )

    uploaded_file = st.file_uploader("PDF da carteira", type=["pdf"], key="upload_carteira")

    if not uploaded_file:
        return

    file_bytes = uploaded_file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()[:10]

    if st.button("Gerar análise", type="primary", key="btn_carteira"):
        with st.spinner(
            "Lendo o documento e classificando os ativos com a Claude... "
            "carteiras grandes podem levar um pouco mais de tempo."
        ):
            try:
                data = extract_portfolio_data(file_bytes, EXTRACTION_PROMPT_CARTEIRA)
            except ResponseTruncatedError as e:
                st.error(
                    "A resposta da Claude foi cortada antes de terminar (a carteira é "
                    "muito extensa para o limite de tokens de saída atual). Pode ser "
                    "necessário aumentar ainda mais o `max_tokens` em app.py."
                )
                with st.expander("Ver resposta parcial (debug)"):
                    st.code(e.partial_text)
                return
            except json.JSONDecodeError:
                st.error(
                    "Não consegui interpretar a resposta do modelo em JSON. "
                    "Tente gerar novamente — às vezes ajuda re-rodar."
                )
                return
            except anthropic.APIError as e:
                st.error(f"Erro na chamada da API: {e}")
                return

        st.session_state[f"report_carteira_{file_hash}"] = data

    report_data = st.session_state.get(f"report_carteira_{file_hash}")

    if report_data:
        html = render_portfolio_report(report_data)

        col1, col2 = st.columns([1, 4])
        with col1:
            st.download_button(
                "⬇️ Baixar relatório (HTML)",
                data=html,
                file_name="analise_carteira.html",
                mime="text/html",
                key="download_carteira",
            )
        with col2:
            with st.expander("Ver dados extraídos (JSON)"):
                st.json(report_data)

        st.components.v1.html(html, height=2900, scrolling=True)


def main():
    st.title("Analisador de investimentos")
    modo = st.radio(
        "O que você quer analisar?",
        ["Fundo de investimento", "Carteira de investimentos"],
        horizontal=True,
    )
    st.divider()

    if modo == "Fundo de investimento":
        main_fundo()
    else:
        main_carteira()


if __name__ == "__main__":
    main()
