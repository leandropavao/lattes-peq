import streamlit as st
import pandas as pd
import os
import configparser
import xml.etree.ElementTree as ET
import requests
from datetime import datetime
from pybliometrics.scopus import AbstractRetrieval, SerialTitleISSN, AuthorRetrieval, SerialTitle

# ================= 1. CONFIGURAÇÃO DA PÁGINA =================
st.set_page_config(
    page_title="Avaliador Scopus UEM (Cascata)", 
    page_icon="🎓", 
    layout="wide"
)

diretorio_script = os.path.dirname(os.path.abspath(__file__))
caminho_logo = os.path.join(diretorio_script, "logo.png")

# ================= 2. SEGURANÇA (COFRE DO STREAMLIT) =================
try:
    SENHA_ACESSO = st.secrets["SENHA_ACESSO"]
    API_KEY = st.secrets["SCOPUS_API_KEY"]
    INST_TOKEN = st.secrets["SCOPUS_INST_TOKEN"]
except Exception as e:
    st.error("⚠️ Cofre de senhas não configurado. Verifique os Secrets no Streamlit Cloud ou o arquivo secrets.toml local.")
    st.stop()

# --- WHITELIST (RANGES PONTUÁVEIS) ---
VALID_ASJC_CODES = set()
ranges_pontuaveis = [
    (1500, 1600), (1600, 1700), (2500, 2600), (2300, 2400),
    (1700, 1800), (3100, 3200), (2000, 2100), (1100, 1200),
    (1300, 1400), (3000, 3100), (2100, 2200)
]
for start, end in ranges_pontuaveis: VALID_ASJC_CODES.update(range(start, end))
extras_pontuaveis = [2200, 2208, 1803, 2613, 2614]
for code in extras_pontuaveis: VALID_ASJC_CODES.add(code)

ASJC_MAP = {
    1000: "Multidisciplinary", 1100: "General Agricultural and Biological Sciences",
    1500: "General Chemical Engineering", 1600: "General Chemistry",
    2100: "General Energy", 2300: "General Environmental Science",
    2200: "General Engineering", 2500: "General Materials Science",
    1700: "General Computer Science", 3100: "General Physics and Astronomy",
    1300: "General Biochemistry", 3000: "Pharmacology, Toxicology",
    2000: "Economics, Econometrics"
}

# ================= 3. FUNÇÕES DE BACKEND =================

def garantir_configuracao():
    config_dir = os.path.join(os.path.expanduser("~"), ".config")
    config_path = os.path.join(config_dir, "pybliometrics.cfg")
    base_scopus = os.path.join(os.path.expanduser("~"), ".scopus")
    
    diretorios = {
        'AbstractRetrieval': os.path.join(base_scopus, 'abstract_retrieval'),
        'AffiliationRetrieval': os.path.join(base_scopus, 'affiliation_retrieval'),
        'AuthorRetrieval': os.path.join(base_scopus, 'author_retrieval'),
        'CitationOverview': os.path.join(base_scopus, 'citation_overview'),
        'ContentAffiliationRetrieval': os.path.join(base_scopus, 'content_affiliation_retrieval'),
        'ContentAuthorRetrieval': os.path.join(base_scopus, 'content_author_retrieval'),
        'PlumXMetrics': os.path.join(base_scopus, 'plumx_metrics'),
        'Retrieval': os.path.join(base_scopus, 'retrieval'),
        'ScopusSearch': os.path.join(base_scopus, 'scopus_search'),
        'Search': os.path.join(base_scopus, 'search'),
        'SerialSearch': os.path.join(base_scopus, 'serial_search'),
        'SerialTitle': os.path.join(base_scopus, 'serial_title'),
        'SerialTitleISSN': os.path.join(base_scopus, 'serial_title_issn'),
        'SerialTitleSourceID': os.path.join(base_scopus, 'serial_title_source_id'),
        'SubjectClassifications': os.path.join(base_scopus, 'subject_classifications')
    }
    
    views = ['ENHANCED', 'STANDARD', 'Ex', 'B', 'META', 'FULL', 'REF', 'CITESCORE', 'COMPLETE']
    for pasta_raiz in diretorios.values():
        os.makedirs(pasta_raiz, exist_ok=True)
        for v in views: os.makedirs(os.path.join(pasta_raiz, v), exist_ok=True)
    
    os.makedirs(config_dir, exist_ok=True)
    config = configparser.ConfigParser()
    config.optionxform = str
    
    config['Authentication'] = {
        'APIKey': API_KEY,
        'InstToken': INST_TOKEN
    }
    config['Directories'] = diretorios
    with open(config_path, 'w') as f: config.write(f)

def get_specific_name(code): return ASJC_MAP.get(code, f"Sub-área {code}")

def get_categoria_principal(code):
    c = int(code)
    if 1500 <= c < 1600: return "Chemical Engineering"
    if 1600 <= c < 1700: return "Chemistry"
    if 2100 <= c < 2200: return "Energy"
    if 2200 <= c < 2300: return "Engineering"
    if 2300 <= c < 2400: return "Environmental Science"
    if 2500 <= c < 2600: return "Materials Science"
    if 1700 <= c < 1800: return "Computer Science"
    if 3100 <= c < 3200: return "Physics and Astronomy"
    if 1100 <= c < 1200: return "Agricultural & Bio Sciences"
    if 1300 <= c < 1400: return "Biochemistry, Genetics & Mol. Bio"
    if 3000 <= c < 3100: return "Pharmacology, Toxicology"
    if 2000 <= c < 2100: return "Economics, Econometrics"
    if c == 1803: return "Management/Operations"
    if c in [2613, 2614]: return "Math/Statistics"
    return "Outra Área Pontuável"

def calcular_estrato(p):
    if p >= 87.5: return 'A1', 15.0
    elif p >= 75.0: return 'A2', 13.0
    elif p >= 62.5: return 'A3', 11.0
    elif p >= 50.0: return 'A4', 9.0
    elif p >= 37.5: return 'A5', 7.0
    elif p >= 25.0: return 'A6', 6.0
    elif p >= 12.5: return 'A7', 5.0
    else: return 'A8', 4.0

def get_pontos_base(estrato):
    tabela = {'A1':15.0, 'A2':13.0, 'A3':11.0, 'A4':9.0, 'A5':7.0, 'A6':6.0, 'A7':5.0, 'A8':4.0}
    return tabela.get(estrato, 0.0)

def extrair_issn_limpo(objeto_issn):
    valor = None
    if hasattr(objeto_issn, 'print') and objeto_issn.print: valor = objeto_issn.print
    elif hasattr(objeto_issn, 'electronic') and objeto_issn.electronic: valor = objeto_issn.electronic
    elif hasattr(objeto_issn, 'value'): valor = objeto_issn.value
    else: valor = str(objeto_issn)
    return str(valor).replace('-', '').replace(' ', '').strip() if valor else None

def verificar_doi_externo(doi):
    """Consulta Crossref para DOIs que ainda não estão na Scopus e extrai os autores"""
    try:
        url = f"https://api.crossref.org/works/{doi}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            msg = resp.json()['message']
            
            autores_crossref = []
            for a in msg.get('author', []):
                family = a.get('family', '')
                given = a.get('given', '')
                if family or given:
                    autores_crossref.append(f"{family}, {given}".strip(', '))
            
            if not autores_crossref:
                autores_crossref = ["Autor Desconhecido"]

            return {
                "titulo": msg.get('title', ['Desconhecido'])[0],
                "revista": msg.get('container-title', [''])[0],
                "ano": msg.get('created', {}).get('date-parts', [[2026]])[0][0],
                "issn": msg.get('ISSN', [None])[0],
                "autores": autores_crossref
            }
    except: pass
    return None

def obter_dados_por_revista(identificador):
    """Busca percentil de uma revista sem precisar de um artigo (Plano B)"""
    try:
        revista = SerialTitle(identificador, view='CITESCORE')
        ranks = revista._entry.get('citeScoreYearInfoList', {}).get('citeScoreYearInfo', [])[0]
        rank_list = ranks.get('citeScoreInformationList', [{}])[0].get('citeScoreInfo', [{}])[0].get('citeScoreSubjectRank', [])
        
        candidatos = []
        for r in rank_list:
            c_code = int(r.get('subjectCode'))
            p_perc = float(r.get('percentile'))
            if c_code in VALID_ASJC_CODES:
                estr, pts = calcular_estrato(p_perc)
                candidatos.append({'code': c_code, 'percentile': p_perc, 'estrato': estr, 'pontos': pts})
        
        if not candidatos:
            return None, None, "Fora das Áreas de Avaliação do Programa"
            
        melhor = max(candidatos, key=lambda x: x['percentile'])
        return melhor['percentile'], {
            'estrato': melhor['estrato'],
            'base': melhor['pontos'],
            'area_principal': get_categoria_principal(melhor['code']),
            'area_detalhe': get_specific_name(melhor['code']),
            'area_code': melhor['code'],
            'todos_candidatos': candidatos
        }, None
    except Exception as e: 
        return None, None, f"Erro ao acessar dados da revista: {str(e)}"

def obter_dados_revista(issn_code, ano_target):
    try:
        issn_clean = extrair_issn_limpo(issn_code)
        revista = SerialTitleISSN(issn_clean, view='CITESCORE')
        historico = revista.citescoreyearinfolist
        if not historico: return None, None, "Sem histórico"
        historico_ordenado = sorted(historico, key=lambda x: int(x.year), reverse=True)
        metrica_escolhida, ranks_encontrados = None, []
        for m in historico_ordenado:
            raw_metrics = revista._entry.get('citeScoreYearInfoList', {}).get('citeScoreYearInfo', [])
            if isinstance(raw_metrics, dict): raw_metrics = [raw_metrics]
            raw_year_data = next((x for x in raw_metrics if str(x.get('@year')) == str(m.year)), None)
            if not raw_year_data: continue
            level_1 = raw_year_data.get('citeScoreInformationList')
            if isinstance(level_1, list) and len(level_1) > 0: level_1 = level_1[0]
            level_2 = level_1.get('citeScoreInfo') if isinstance(level_1, dict) else None
            if isinstance(level_2, list) and len(level_2) > 0: level_2 = level_2[0]
            ranks = level_2.get('citeScoreSubjectRank') if isinstance(level_2, dict) else None
            if isinstance(ranks, dict): ranks = [ranks]
            if ranks:
                metrica_escolhida = m
                ranks_encontrados = ranks
                break
        if not metrica_escolhida: return None, None, "Sem ranking de áreas"
        candidatos = []
        for r in ranks_encontrados:
            raw_code = r.get('subjectCode') or r.get('code') or r.get('@subjectCode')
            raw_perc = r.get('percentile') or r.get('@percentile')
            if raw_code and raw_perc:
                try:
                    c_int = int(raw_code); p_float = float(raw_perc)
                    if c_int in VALID_ASJC_CODES:
                        estr, pts = calcular_estrato(p_float)
                        candidatos.append({'code':c_int, 'name':get_specific_name(c_int), 'percentile':p_float, 'estrato':estr, 'pontos':pts})
                except: continue
                
        if not candidatos: return None, None, "Fora das Áreas de Avaliação do Programa"
        
        melhor = max(candidatos, key=lambda x: x['percentile'])
        return melhor['percentile'], {'estrato': melhor['estrato'], 'base': melhor['pontos'], 'ano': metrica_escolhida.year, 'area_principal': get_categoria_principal(melhor['code']), 'area_detalhe': melhor['name'], 'area_code': melhor['code'], 'todos_candidatos': candidatos}, None
    except Exception as e: return None, None, f"Erro interno: {str(e)}"

def extrair_dois_lattes(arquivo_xml):
    try:
        tree = ET.parse(arquivo_xml); root = tree.getroot(); dois = set()
        for artigo in root.findall(".//ARTIGO-PUBLICADO"):
            dados = artigo.find("DADOS-BASICOS-DO-ARTIGO")
            if dados is not None:
                d = dados.get("DOI")
                if d: dois.add(d.replace("https://doi.org/", "").replace("http://dx.doi.org/", "").strip())
        return list(dois)
    except: return []

def extrair_dois_scopus_author(author_id):
    try:
        au = AuthorRetrieval(author_id.strip(), refresh=True)
        docs = au.get_documents(refresh=True)
        dois = [d.doi for d in docs if d.doi]
        nome = f"{au.given_name} {au.surname}" if au.given_name else au.indexed_name
        return dois, nome, None
    except Exception as e: return [], None, str(e)

# ================= 4. INTERFACE WEB =================

with st.sidebar:
    if os.path.exists(caminho_logo): st.image(caminho_logo, width=250)
    else: st.markdown("### 🏛️ PEQ-UEM")
    
    senha_user = st.text_input("Senha", type="password")
    
    if senha_user != SENHA_ACESSO:
        st.warning("🔒 Bloqueado")
        st.stop()
    else:
        st.success("🔓 Conectado")
        garantir_configuracao()
        try:
            import pybliometrics
            pybliometrics.scopus.init()
        except: pass

    st.markdown("---")
    st.subheader("📅 Período")
    col_data1, col_data2 = st.columns(2)
    with col_data1: ano_ini = st.number_input("Ano Inicial", value=2021, step=1)
    with col_data2: ano_fim = st.number_input("Ano Final", value=2026, step=1)

    st.markdown("---")
    st.subheader("📊 Regras de Cascata")
    limites = {}
    col_a, col_b = st.columns(2)
    with col_a:
        limites['A1'] = st.number_input("Máx A1", value=5, min_value=0)
        limites['A2'] = st.number_input("Máx A2", value=5, min_value=0)
        limites['A3'] = st.number_input("Máx A3", value=5, min_value=0)
        limites['A4'] = st.number_input("Máx A4", value=5, min_value=0)
    with col_b:
        limites['A5'] = st.number_input("Máx A5", value=5, min_value=0)
        limites['A6'] = st.number_input("Máx A6", value=5, min_value=0)
        limites['A7'] = st.number_input("Máx A7", value=5, min_value=0)
        limites['A8'] = st.number_input("Máx A8", value=999, min_value=0)

# --- TELA PRINCIPAL ---
st.title("🎓 Avaliador de Periódicos (Cascata)")
st.caption(f"Sistema Scopus UEM | Filtro Ativo: {ano_ini} a {ano_fim}")

modo_entrada = st.radio("Como deseja inserir os dados?", ("📝 Lista Manual de DOIs", "🆔 Scopus Author ID", "📄 Upload Lattes XML"), horizontal=True)
lista_dois_final = []

if modo_entrada == "📝 Lista Manual de DOIs":
    text_input = st.text_area("Cole os DOIs (um por linha):", height=120)
    if text_input: lista_dois_final = [d.strip() for d in text_input.split('\n') if d.strip()]

elif modo_entrada == "🆔 Scopus Author ID":
    scopus_id = st.text_input("Digite o Scopus Author ID:")
    if scopus_id and st.button("🔍 Buscar Autor"):
        with st.spinner("Buscando..."):
            dois_encontrados, nome_autor, erro_msg = extrair_dois_scopus_author(scopus_id)
            if erro_msg: st.error(f"Erro: {erro_msg}")
            elif nome_autor:
                st.success(f"Autor: {nome_autor}")
                st.session_state['dois_autor_cache'] = dois_encontrados
    if 'dois_autor_cache' in st.session_state: lista_dois_final = st.session_state['dois_autor_cache']

elif modo_entrada == "📄 Upload Lattes XML":
    uploaded_file = st.file_uploader("Envie o arquivo XML", type=['xml'])
    if uploaded_file: lista_dois_final = extrair_dois_lattes(uploaded_file)

if lista_dois_final:
    st.divider()
    if st.button("🚀 Iniciar Avaliação Completa", type="primary"):
        barra = st.progress(0)
        dados_brutos = [] 

        for i, doi in enumerate(lista_dois_final):
            try:
                obs_artigo = ""
                status_icone = "✅"
                
                # 1. TENTA SCOPUS
                try:
                    artigo = AbstractRetrieval(doi, view='FULL')
                    nome_revista, titulo_artigo, ano_str = artigo.publicationName, artigo.title, str(artigo.coverDate[:4])
                    
                    if not (ano_ini <= int(ano_str) <= ano_fim): 
                        raise Exception(f"Ano {ano_str} fora do período estipulado")
                    
                    issn_obj = artigo.issn if artigo.issn else artigo.eIssn
                    issn_str = extrair_issn_limpo(issn_obj)
                    if not issn_str: raise Exception("Revista sem ISSN registrado")
                    
                    autores = [f"{a.surname}, {a.given_name}" for a in artigo.authors] if artigo.authors else ["Desconhecido"]
                    
                    percentil, dados_nota, erro = obter_dados_revista(issn_str, ano_str)
                    if erro: 
                        percentil = "N/A"
                        dados_nota = {'estrato': 'REJEITADO', 'base': 0.0, 'area_principal': 'Fora do Escopo'}
                        obs_artigo = erro
                        status_icone = "⚠️"
                
                except Exception as e_scopus:
                    if "fora do período estipulado" in str(e_scopus):
                        raise e_scopus
                        
                    # 2. PLANO B: CROSSREF
                    info = verificar_doi_externo(doi)
                    if info:
                        if not (ano_ini <= int(info['ano']) <= ano_fim):
                            raise Exception(f"Ano {info['ano']} (via Crossref) fora do período")
                        
                        nome_revista, titulo_artigo, ano_str = info['revista'], info['titulo'], str(info['ano'])
                        autores = info['autores']
                        obs_artigo = "Não indexado na Scopus"
                        
                        percentil, dados_nota, err = obter_dados_por_revista(info['issn'] if info['issn'] else info['revista'])
                        if err: 
                            percentil = "N/A"
                            dados_nota = {'estrato': 'REJEITADO', 'base': 0.0, 'area_principal': 'Fora do Escopo'}
                            obs_artigo = f"{obs_artigo} | {err}"
                            status_icone = "⚠️"
                    else: 
                        raise Exception(f"Não encontrado (Erro Scopus: {str(e_scopus)})")
                
                n_autores = len(autores)
                fator = 1.0 if n_autores <= 4 else (4.0 / n_autores)
                
                item = {
                    "DOI": doi, 
                    "Revista": nome_revista, 
                    "Título": titulo_artigo, 
                    "Ano": int(ano_str), 
                    "Estrato Original": dados_nota['estrato'], 
                    "Fator Autores": fator, 
                    "Nº Autores": n_autores, 
                    "Autores": "; ".join(autores), 
                    "Percentil": percentil, 
                    "Area Principal": dados_nota['area_principal'],
                    "Obs Previa": obs_artigo
                }
                dados_brutos.append(item)
                
                titulo_expander = f"{status_icone} {dados_nota['estrato']} | {nome_revista} ({ano_str})" if dados_nota['estrato'] != 'REJEITADO' else f"{status_icone} Avaliação Indisponível | {nome_revista} ({ano_str})"
                
                with st.expander(titulo_expander, expanded=False):
                    st.write(f"📄 **Título:** {titulo_artigo}")
                    st.write(f"👥 **Autores:** {item['Autores']}")
                    if dados_nota['estrato'] != 'REJEITADO':
                        st.markdown(f"**🎯 Área Base:** `{item['Area Principal']}` | **Percentil:** {percentil}% | **Fator:** {fator:.2f}")
                    else:
                        st.markdown(f"**🎯 Área Base:** `Inválida/Fora do escopo` | **Fator:** {fator:.2f}")
                        
                    if obs_artigo:
                        st.warning(f"⚠️ {obs_artigo}")

            except Exception as e:
                with st.expander(f"❌ Erro Crítico | DOI: {doi}", expanded=False):
                    st.error(f"Motivo: {str(e)}")
            
            barra.progress((i + 1) / len(lista_dois_final))

        # --- FASE 2: CÁLCULO CASCATA ---
        if dados_brutos:
            st.divider()
            st.header("🌊 Cálculo de Saturação (Cascata)")
            estratos_ordem = ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8']
            
            buckets = {e: [] for e in estratos_ordem}
            pool_excedente = [] 
            itens_rejeitados = [] 
            
            for item in dados_brutos: 
                if item['Estrato Original'] in estratos_ordem:
                    buckets[item['Estrato Original']].append(item)
                else:
                    itens_rejeitados.append(item) 
            
            resultado_final = []
            for estrato_atual in estratos_ordem:
                disponiveis = sorted(buckets[estrato_atual] + pool_excedente, key=lambda x: x['Fator Autores'], reverse=True)
                limite = limites[estrato_atual]
                selecionados, pool_excedente = disponiveis[:limite], disponiveis[limite:]
                pts_base = get_pontos_base(estrato_atual)
                
                for item in selecionados:
                    obs_lista = []
                    if item['Obs Previa']: obs_lista.append(item['Obs Previa'])
                    if item['Estrato Original'] != estrato_atual: obs_lista.append(f"Original {item['Estrato Original']}")
                    
                    resultado_final.append({
                        "DOI": item['DOI'], "Revista": item['Revista'], "Título do Artigo": item['Título'], 
                        "Ano": item['Ano'], "Estrato Efetivo": estrato_atual, "Origem": item['Estrato Original'], 
                        "Área Base": item['Area Principal'], "Pontos": pts_base * item['Fator Autores'], 
                        "Nº Autores": item['Nº Autores'], "Autores": item['Autores'], 
                        "Obs": " | ".join(obs_lista)
                    })
            
            for item_lixo in pool_excedente:
                obs_lista = ["Saturou limite da cascata"]
                if item_lixo['Obs Previa']: obs_lista.append(item_lixo['Obs Previa'])
                
                resultado_final.append({
                    "DOI": item_lixo['DOI'], "Revista": item_lixo['Revista'], "Título do Artigo": item_lixo['Título'], 
                    "Ano": item_lixo['Ano'], "Estrato Efetivo": "DESCARTADO", "Origem": item_lixo['Estrato Original'], 
                    "Área Base": item_lixo['Area Principal'], "Pontos": 0.0, 
                    "Nº Autores": item_lixo['Nº Autores'], "Autores": item_lixo['Autores'], 
                    "Obs": " | ".join(obs_lista)
                })

            # CORREÇÃO DA REDUNDÂNCIA AQUI
            for item_rejeitado in itens_rejeitados:
                obs_lista = []
                if item_rejeitado['Obs Previa']: 
                    obs_lista.append(item_rejeitado['Obs Previa'])
                else:
                    obs_lista.append("Rejeitado: Fora do escopo") 
                
                resultado_final.append({
                    "DOI": item_rejeitado['DOI'], "Revista": item_rejeitado['Revista'], "Título do Artigo": item_rejeitado['Título'], 
                    "Ano": item_rejeitado['Ano'], "Estrato Efetivo": "DESCARTADO", "Origem": item_rejeitado['Estrato Original'], 
                    "Área Base": item_rejeitado['Area Principal'], "Pontos": 0.0, 
                    "Nº Autores": item_rejeitado['Nº Autores'], "Autores": item_rejeitado['Autores'], 
                    "Obs": " | ".join(obs_lista)
                })

            df_final = pd.DataFrame(resultado_final)
            st.metric("Pontuação FINAL (Saturada)", f"{df_final['Pontos'].sum():.2f}")
            
            def color_row(row):
                if row['Estrato Efetivo'] == 'DESCARTADO': cor = 'background-color: #f8d7da' 
                elif row['Origem'] != row['Estrato Efetivo']: cor = 'background-color: #fff3cd' 
                else: cor = 'background-color: #d4edda' 
                return [cor] * len(row)

            st.dataframe(df_final.style.apply(color_row, axis=1), use_container_width=True)
            st.download_button("📥 Baixar Planilha", df_final.to_csv(index=False).encode('utf-8'), "resultado_cascata.csv", "text/csv")
