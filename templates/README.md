# 🧩 Templates Zabbix para Provedores (ISP) — NOTO TI

Este diretório reúne os templates prontos para importação no Zabbix utilizados nas aulas do **Curso de Provedores da NOTO TI**.

---

## 📦 Templates Disponíveis

### 1. Arquitetura ICMP em 3 Camadas com Dupla Dependência (Zabbix 7.0)
- **Arquivo:** [`zabbix-7.0/NOTO_TI_Templates_ISP_Cascata_Zabbix70.yaml`](zabbix-7.0/NOTO_TI_Templates_ISP_Cascata_Zabbix70.yaml)
- **Grupo de Templates criado:** `LAB - NOTO TI - Templates ISP`
- **O que está incluído no arquivo YAML:**
  1. **`TPL - NOTO TI - Base ICMP ISP` *(Template Base)*:**
     - **3 Itens Simple Check:** `icmpping[,3,200,,500]`, `icmppingloss[,3,200,,500]` (%) e `icmppingsec[,3,200,,500]` (s).
     - **3 Triggers parametrizadas por Macros:**
       - `{HOST.NAME}: Equipamento inacessivel` → `max(/.../icmpping,{$ICMP.FALHAS})=0`
       - `{HOST.NAME}: Perda de pacotes alta (>{$ICMP.LOSS.WARN}%)` → `min(/.../icmppingloss,{$ICMP.PERDA.AMOSTRAS})>{$ICMP.LOSS.WARN} and max(/.../icmppingloss,{$ICMP.PERDA.AMOSTRAS})<100`
       - `{HOST.NAME}: Latencia alta (>80ms)` → `min(/.../icmppingsec,{$ICMP.LATENCIA.AMOSTRAS})>{$ICMP.LATENCIA.WARN}`
     - **Dependências Internas já configuradas:**
       - `Latencia alta` depende de `Perda de pacotes alta` e de `Equipamento inacessivel`.
       - `Perda de pacotes alta` depende de `Equipamento inacessivel`.
  2. **`TPL - NOTO TI - N1 Core` *(Herda o Template Base)*:**
     - Define a macro `{$ICMP.FALHAS} = #2` (janela rápida para o Core abrir incidente primeiro).
  3. **`TPL - NOTO TI - N2 Switch POP` *(Herda o Template Base)*:**
     - Define a macro `{$ICMP.FALHAS} = #6` (janela intermediária de agregação/distribuição).
  4. **`TPL - NOTO TI - N3 OLT` *(Herda o Template Base)*:**
     - Define a macro `{$ICMP.FALHAS} = #10` (janela de acesso).

---

## 📥 Como Importar no Zabbix 7.0

1. No menu lateral do Zabbix 7.0, acesse **Data collection → Templates**.
2. No canto superior direito, clique em **Import**.
3. Selecione o arquivo [`zabbix-7.0/NOTO_TI_Templates_ISP_Cascata_Zabbix70.yaml`](zabbix-7.0/NOTO_TI_Templates_ISP_Cascata_Zabbix70.yaml).
4. Clique em **Import** e confirme.
5. Ao cadastrar seus hosts, vincule apenas o template da camada correspondente (`N1 Core`, `N2 Switch POP` ou `N3 OLT`) e adicione a dependência topológica na trigger herdada `Equipamento inacessivel` apontando para o equipamento pai (uplink).
