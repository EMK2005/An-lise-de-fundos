import base64
import json
import hashlib

import streamlit as st
import anthropic
from jinja2 import Template

from prompt import EXTRACTION_PROMPT

st.set_page_config(page_title="Analisador de fundos", layout="wide")

MODEL = "claude-sonnet-5"


@st.cache_resource
def get_client():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])


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
def extract_fund_data(pdf_bytes: bytes) -> dict:
    """Envia o PDF pra Claude e retorna os dados estruturados do fundo.
    Cacheado por hash do conteúdo do arquivo: reprocessar o mesmo PDF não gasta tokens de novo."""
    client = get_client()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    message = client.messages.create(
        model=MODEL,
        max_tokens=4000,
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
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    )

    raw_text = "".join(block.text for block in message.content if block.type == "text")
    return parse_json_response(raw_text)


def render_report(data: dict) -> str:
    with open("template.html", "r", encoding="utf-8") as f:
        template = Template(f.read())
    return template.render(data=data)


def main():
    st.title("Analisador de fundos de investimento")
    st.caption(
        "Anexe a lâmina, fact sheet ou relatório em PDF de um fundo para gerar "
        "uma análise visual detalhada."
    )

    uploaded_file = st.file_uploader("PDF do fundo", type=["pdf"])

    if not uploaded_file:
        return

    file_bytes = uploaded_file.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()[:10]

    if st.button("Gerar análise", type="primary"):
        with st.spinner("Lendo o documento e extraindo os dados com a Claude..."):
            try:
                data = extract_fund_data(file_bytes)
            except json.JSONDecodeError:
                st.error(
                    "Não consegui interpretar a resposta do modelo em JSON. "
                    "Tente gerar novamente — às vezes ajuda re-rodar."
                )
                return
            except anthropic.APIError as e:
                st.error(f"Erro na chamada da API: {e}")
                return

        st.session_state[f"report_{file_hash}"] = data

    report_data = st.session_state.get(f"report_{file_hash}")

    if report_data:
        html = render_report(report_data)

        col1, col2 = st.columns([1, 4])
        with col1:
            st.download_button(
                "⬇️ Baixar relatório (HTML)",
                data=html,
                file_name=f"analise_{report_data.get('fundo', {}).get('nome', 'fundo')}.html",
                mime="text/html",
            )
        with col2:
            with st.expander("Ver dados extraídos (JSON)"):
                st.json(report_data)

        st.components.v1.html(html, height=2600, scrolling=True)


if __name__ == "__main__":
    main()
