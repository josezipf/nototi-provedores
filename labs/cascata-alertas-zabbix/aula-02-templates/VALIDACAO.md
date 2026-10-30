# Validação do pacote — Aula 02 (Templates + Dependências Internas e Topológicas)

Data: 30/10/2026.

Verificações incluídas na Aula 02:

- Compilação dos arquivos Python e sintaxe dos scripts Bash/sh.
- Leitura do YAML do Docker Compose (`nototi-cascata-aula02`).
- Quatorze testes unitários offline (`python3 -m unittest discover -s tests -v`):
  - Caminhos de ida e volta calculados a partir das interfaces e rotas emitidas pelo `entrypoint.sh`.
  - Isolamento das falhas por POP e OLT.
  - Ausência de caminho direto entre Zabbix e OLTs.
  - Portas restritas a loopback (`127.0.0.1`).
  - Seleção de redes privadas sem conflito com a Aula 01 ou rotas do host.
  - Amarração automática de dependências topológicas via leitura da tag `uplink`.
  - Alternância das dependências internas no Template Base (`internal on` / `internal off`).
  - Receptor HTTP com autenticação, persistência concorrente e sanitização HTML.
