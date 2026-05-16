# Update Agent — IA Agentic para Atualização de Software

Agente que monitora softwares em servidores, notifica via Telegram e executa atualizações com backup, validação e aprendizado contínuo.

## Arquitetura

```
Telegram (um único bot)
    │
    ▼
[ Coordenador ]  ← servidor com o token do bot
    │
    ├── HTTP → servidor-a:9700   (executa comandos localmente)
    └── HTTP → servidor-b:9700   (encaminha comandos via HTTP)
```

- **Coordenador**: faz polling do Telegram, roteia comandos para o servidor correto via `@server`
- **Workers**: só rodam o listener HTTP, sem acesso ao bot — recebem comandos do coordenador e executam

## Quick Start

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Editar config.yaml com seu token do Telegram
#    (crie o bot com @BotFather no Telegram)

# 3. Rodar
python main.py

# 4. No Telegram, inicie conversa com o bot e mande:
/start
```

## Instalação como Serviço (Linux)

```bash
sudo ./install.sh
```

Comandos úteis:

```bash
systemctl status update-agent
journalctl -u update-agent -f
systemctl stop update-agent
systemctl restart update-agent
```

## Configuração

### Coordenador (quem recebe comandos do Telegram)

Arquivo: `config.yaml`

```yaml
server_name: "servidor-a"
listen_port: 9700
is_coordinator: true

telegram:
  token: "SEU_TOKEN_AQUI"
  allowed_chat_ids:
    - SEU_CHAT_ID

peers:
  servidor-a: "http://IP_DO_SERVIDOR_A:9700"
  servidor-b: "http://IP_DO_SERVIDOR_B:9700"

skills_dir: "skills"
scan_interval_hours: 24
```

### Worker (servidor sem acesso ao bot)

Arquivo: `config.yaml` (mesmo nome, conteúdo diferente)

```yaml
server_name: "servidor-b"
listen_port: 9700
is_coordinator: false

skills_dir: "skills"
scan_interval_hours: 24
```

> Ambos os servidores usam `config.yaml`. O conteúdo muda: o coordenador tem `telegram:` e `peers:`, o worker não precisa de nada disso.

### Campos

| Campo | Obrigatório | Descrição |
|-------|:-----------:|-----------|
| `server_name` | sim | Nome único do servidor (usado nos comandos `@server`) |
| `listen_port` | sim | Porta do listener HTTP entre servidores |
| `is_coordinator` | sim | `true` = faz polling do Telegram; `false` = só listener |
| `telegram.token` | só coordinator | Token do bot (criado no @BotFather) |
| `telegram.allowed_chat_ids` | só coordinator | IDs dos chats autorizados a comandar |
| `peers` | só coordinator | Mapeamento servidor → URL HTTP dos workers |
| `skills_dir` | não | Pasta com os perfis YAML (default: `skills/`) |
| `scan_interval_hours` | não | Intervalo entre scans completos (default: 24h) |
| `logging.log_dir` | não | Diretório dos logs (default: diretório atual) |
| `logging.max_size_mb` | não | Tamanho máximo por arquivo de log (default: 10MB) |
| `logging.retention_days` | não | Dias de retenção dos logs (default: 30) |

### WhatsApp (Evolution API)

O agente também pode receber comandos e enviar alertas via WhatsApp, usando o [Evolution API](https://github.com/EvolutionAPI/evolution-api).

**Configuração no `config.yaml`:**

```yaml
evolution_api:
  enabled: true
  base_url: "http://IP_DO_EVOLUTION:8080"
  api_key: "SEU_API_KEY"
  instance: "minha-instancia"
  group_jid: "5511999999999-123456@g.us"
```

**Passos:**

1. Tenha uma instância do Evolution API rodando com um número conectado
2. No Evolution API, configure o webhook para apontar para:
   ```
   http://SEU_SERVIDOR:9700/whatsapp-webhook
   ```
3. Adicione no `config.yaml` os dados da instância e o JID do grupo

**Comportamento:**

- O agente recebe as mensagens do grupo via webhook
- Se a mensagem começar com `/`, processa como comando
- A resposta é enviada de volta no mesmo grupo
- Os mesmos comandos do Telegram funcionam: `/scan@all`, `/status@server-a`, etc.

### Logs

Os logs giram automaticamente por data e tamanho:

```
agent.log                  ← arquivo atual
agent_2026-05-16.log       ← rotation do dia anterior
agent_2026-05-15.log       ← 2 dias atrás
...
```

Quando o arquivo atual atinge `max_size_mb` ou vira o dia, ele é renomeado com a data. Arquivos mais antigos que `retention_days` são deletados automaticamente.

## Skills / Perfis de Software

Cada software tem um arquivo YAML em `skills/` que define:

```yaml
software:
  name: "9router"
  type: "docker"
  current_version: "3.2.1"
  install_path: "/opt/9router"

detection:
  method: "docker"
  version_command: "docker inspect 9router-api --format '{{.Config.Image}}'"
  version_regex: "(\\d+\\.\\d+\\.\\d+)"
  docker:
    container_name: "9router-api"
    image_name: "9router/api"

backup:
  enabled: true
  destination: "/opt/backups/9router"
  retention_days: 30
  filesystem:
    paths:
      - "/opt/9router/config.yaml"
  docker:
    commit_image: true
    export_container: true
    volumes:
      - "9router_data:/opt/backups/9router/data"
  database:
    type: "postgres"
    host: "localhost"
    port: 5432
    database: "9router"
    user: "postgres"

update:
  method: "docker"
  docker:
    container_name: "9router-api"
    image: "9router/api:latest"
    compose_file: "/opt/9router/docker-compose.yml"
    compose_service: "api"
    pull_image: true
    force_recreate: true
  pre_update_hooks:
    - "docker stop 9router-api 2>/dev/null || true"
  post_update_hooks:
    - "docker system prune -f 2>/dev/null || true"

validation:
  commands:
    - "curl -s http://localhost:9090/health | grep -q '\"status\":\"ok\"'"
    - "docker ps --filter name=9router-api --filter status=running --format '{{.Names}}' | grep -q 9router-api"

history: []
```

Use `skills/_template.yaml` como ponto de partida.

### Nomenclatura dos arquivos

| Prefixo | Significado | Aparece no scan? |
|---------|-------------|:----------------:|
| `_template.yaml` | Template/vazio para copiar | ❌ ignorado |
| `_exemplo_*.yaml` | Exemplo didático | ❌ ignorado |
| `qualquercoisa.yaml` | App real que você monitora | ✅ scaneado |

Arquivos começando com `_` são ignorados pelo scanner — use para templates e exemplos.

### Tipos de software suportados

| `type` | `method` | Descrição |
|--------|----------|-----------|
| `docker` | `docker` | docker-compose pull + up -d |
| `git_repo` | `git_pull` | git pull + hooks |
| `pip_package` | `pip` | pip install --upgrade |
| `npm_package` | `npm` | npm update |
| `custom` | `command` | comando arbitrário |
| `custom` | `manual` | só avisa, não automatiza |

### Backup

Cada skill define seu próprio backup:

- **filesystem**: copia arquivos/pastas do host
- **docker**: `docker commit` da imagem, `docker export` do container, cópia de volumes nomeados
- **database**: `pg_dump`, `mysqldump` ou `mongodump`

## Comandos do Telegram

| Comando | Exemplo | Descrição |
|---------|---------|-----------|
| `/start` | `/start` | Mensagem inicial |
| `/help` | `/help` | Ajuda completa |
| `/servers` | `/servers` | Lista servidores disponíveis |
| `/scan` | `/scan@server-a` | Escaneia softwares de um servidor |
| `/scan@all` | `/scan@all` | Escaneia todos os servidores |
| `/status` | `/status@server-a` | Status resumido |
| `/skills` | `/skills@server-b` | Lista perfis de um servidor |
| `/simulate` | `/simulate meuapp@server-a` | Simula update (mostra riscos) |
| `/update` | `/update meuapp@server-a` | Inicia processo de update |
| `/report` | `/report meuapp@server-a` | Relatório completo com histórico |
| `/cancel` | `/cancel` | Cancela confirmação pendente |
| `sim@server` | `sim@server-a` | Confirma update no servidor X |
| `nao` | `nao` | Cancela update |

### Fluxo típico

```
/scan@all
  → vê que meuapp no server-a está desatualizado

/simulate meuapp@server-a
  → mostra riscos (último update teve breaking change)

/update meuapp@server-a
  → pergunta confirmação
  → sim@server-a
  → executa backup + update + validação
  → salva resultado no histórico do YAML

/report meuapp@server-a
  → mostra relatório completo com o histórico
```

## Aprendizado Contínuo

Quando um update é executado, o resultado é salvo no YAML do software:

```yaml
history:
  - date: "2026-05-16"
    from_version: "3.1.0"
    to_version: "3.2.0"
    status: partial
    notes: "Breaking change no formato de rotas"
    adaptations:
      - "Script de migração adicionado"
      - "Nova flag --strict-routes no config"
```

Na próxima simulação, essas adaptações aparecem como **riscos conhecidos**:

```
/simulate meuapp@server-a

Riscos identificados:
  ⚠️ 2 update(s) anterior(es) com problemas
    - Breaking change no formato de rotas...
    - Container não subiu (faltava DATABASE_URL)...
```

## Estrutura do Projeto

```
update_agent/
├── main.py                 # Entry point
├── config.yaml.example     # Modelo para config.yaml (comite este)
├── config.yaml             # ⚠️ Config real (NÃO comitar — está no .gitignore)
├── requirements.txt        # Dependências
├── install.sh              # Instalação Linux + systemd
├── .gitignore
├── core/
│   ├── __init__.py
│   ├── agent.py            # Loop principal
│   ├── telegram_bot.py     # Bot Telegram + roteamento @server
│   ├── scanner.py          # Detecção de versões
│   ├── updater.py          # Motor de update + backup
│   ├── skill_manager.py    # Gerenciamento de perfis YAML
│   └── listener.py         # HTTP listener (comunicação entre servidores)
├── skills/
│   ├── _template.yaml               # Template para criar novos perfis
│   ├── _exemplo_docker_wordpress.yaml   # 🚩 EXEMPLO Docker + MySQL
│   ├── _exemplo_git_appweb.yaml         # 🚩 EXEMPLO Git pull (Node.js)
│   ├── 9router_example.yaml             # 🚩 EXEMPLO Docker + Postgres
│   └── sistema-linux.yaml               # App real (updates do sistema apt)
└── memory/
```
