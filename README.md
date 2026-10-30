# NOTO TI — Curso de Provedores (ISP) · Labs, Templates e Dashboards

Repositório oficial da **NOTO TI** com laboratórios práticos em Docker, templates padronizados para Zabbix e dashboards voltados para **Provedores de Internet (ISP) e operações de NOC**.

---

## 🚀 Como clonar e utilizar

```bash
git clone https://github.com/josezipf/nototi-provedores.git
cd nototi-provedores
```

---

## 📂 Estrutura do Repositório

### 1. 🧪 Laboratórios Práticos (`labs/`)

| Módulo / Série | Aula | Tema | Pasta |
|---|---|---|---|
| **Cascata de Alertas no Zabbix 7.0** | **Visão Geral + Slides** | Apresentação interativa em slides e Guia passo a passo do aluno (Aulas 01 e 02) | [labs/cascata-alertas-zabbix](labs/cascata-alertas-zabbix) |
| **Cascata de Alertas no Zabbix 7.0** | **Aula 01** | Topologia ISP em 3 camadas (Core → Switch POP → OLT), escalonamento de coleta e dependências entre hosts | [labs/cascata-alertas-zabbix/aula-01-fundamentos](labs/cascata-alertas-zabbix/aula-01-fundamentos) |
| **Cascata de Alertas no Zabbix 7.0** | **Aula 02** | Arquitetura em escala com **Templates, Herança (*Linked Templates*), Macros por Camada (`{$ICMP.FALHAS}`), Dependência Interna e Tag `uplink`** | [labs/cascata-alertas-zabbix/aula-02-templates](labs/cascata-alertas-zabbix/aula-02-templates) |

### 2. 🧩 Templates Zabbix (`templates/`)

| Versão | Arquivo | Descrição |
|---|---|---|
| **Zabbix 7.0** | [templates/zabbix-7.0/NOTO_TI_Templates_ISP_Cascata_Zabbix70.yaml](templates/zabbix-7.0/NOTO_TI_Templates_ISP_Cascata_Zabbix70.yaml) | Pacote com os **4 Templates** da Aula 02 (`TPL - NOTO TI - Base ICMP ISP` + `N1 Core`, `N2 Switch POP` e `N3 OLT`) prontos para importar em qualquer Zabbix 7.0 |

👉 Veja instruções detalhadas de importação em [templates/README.md](templates/README.md).

### 3. 📊 Dashboards (`dashboards/`)

Diretório dedicado aos Dashboards de NOC e operação de Provedores (Grafana / Zabbix). Veja [dashboards/README.md](dashboards/README.md).

---

## ⚠️ Aviso — Uso em Ambiente de Estudo e Homologação

Todo o conteúdo deste repositório é **material didático e de laboratório para estudo e capacitação técnica**.

- Os laboratórios sobem containers isolados com senhas geradas automaticamente para facilitar o aprendizado.
- Sempre valide templates, intervalos de coleta e dependências em ambiente controlado (VM ou laboratório) antes de aplicar em servidores de produção.
- Os arquivos são fornecidos "como estão", sem garantia de qualquer tipo. **O uso é por sua conta e risco.**

Achou um erro ou tem alguma dúvida sobre as aulas? Abra uma *issue* neste repositório.

**NOTO TI | Monitoramento na prática.** · [nototi.com.br](https://nototi.com.br)
