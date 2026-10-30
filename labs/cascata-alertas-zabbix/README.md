# 🧪 Laboratório ISP: Cascata de Alertas no Zabbix 7.0 (Aulas 01 e 02)

Bem-vindo ao módulo prático de **Dependências e Controle de Cascata de Alertas para Provedores (ISP)** da **NOTO TI**.

Este diretório contém:
1. **[`apresentacao_aulas_01_e_02.html`](apresentacao_aulas_01_e_02.html)** — Apresentação oficial em slides interativos (abra no navegador e pressione `F` para Tela Cheia).
2. **[`GUIA_ALUNO_E_ROTEIRO_AULAS_01_02.md`](GUIA_ALUNO_E_ROTEIRO_AULAS_01_02.md)** — Apostila passo a passo com todos os testes práticos para você executar e acompanhar junto com a aula.
3. **[`aula-01-fundamentos/`](aula-01-fundamentos)** — Laboratório da **Aula 01** (itens e triggers diretos nos hosts, foco na topologia L3 e dependências entre camadas).
4. **[`aula-02-templates/`](aula-02-templates)** — Laboratório da **Aula 02** (arquitetura avançada com 1 Template Base ICMP + 3 Templates de Camada com Herança, Macros, Dependência Interna e Dependência Topológica via Tag `uplink`).

---

## ⚡ Início Rápido

### Para rodar a Aula 01:
```bash
cd aula-01-fundamentos
bash lab.sh up
```
- **Zabbix (Aula 01):** <http://127.0.0.1:8188>
- **Notificações (Aula 01):** <http://127.0.0.1:8189>

### Para rodar a Aula 02 (Templates):
```bash
cd aula-02-templates
bash lab.sh up
```
- **Zabbix (Aula 02):** <http://127.0.0.1:8190>
- **Notificações (Aula 02):** <http://127.0.0.1:8191>

*(Os dois laboratórios usam portas e sub-redes diferentes e podem inclusive rodar ao mesmo tempo na mesma máquina).*
