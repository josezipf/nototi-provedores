# Validação do pacote

Data: 30/10/2026.

O ambiente de preparação não disponibiliza Docker, socket do Docker ou acesso ao computador de destino. Os containers ainda precisam ser iniciados e testados no Linux onde o laboratório será usado.

Verificações realizadas na preparação:

- Compilação dos arquivos Python e sintaxe dos scripts Bash/sh.
- Leitura do YAML, incluindo as extensões e heranças do Compose.
- Treze testes offline passaram: caminhos de ida e volta calculados a partir das interfaces e rotas efetivamente emitidas pelo entrypoint; isolamento das falhas por POP e OLT; ausência de caminho direto entre Zabbix e OLTs; portas restritas a loopback; escolha de redes sem conflito; alternância das dependências; receptor HTTP com autenticação, persistência concorrente, problemas, recuperações e contagem de mensagens repetidas.

Esses testes não executam o kernel de rede dos containers nem o servidor Zabbix. Não confirmam download das imagens, aceitação do Compose pelo plugin instalado ou criação dos objetos pela API de uma instância em execução.

Validação no host de destino:

```bash
bash lab.sh up
bash lab.sh test
```

`up` executa `docker compose config --quiet`, inicia os serviços, verifica os sete IPs a partir do NOC, cadastra os objetos pela API e exige coletas saudáveis antes de concluir.

`test` verifica cinco falhas controladas usando os containers reais. Confere o alcance dos IPs, os estados das triggers e as notificações recebidas. Gera `test-report.json` e tenta recuperar todos os equipamentos ao terminar, inclusive se um cenário falhar.

Para executar novamente as verificações offline, é necessário instalar PyYAML no ambiente Python de desenvolvimento e rodar:

```bash
python3 -m unittest discover -s tests -v
```

PyYAML é utilizado somente nesses testes de preparação. O laboratório e seu teste com Docker utilizam a biblioteca padrão do Python.
