#!/usr/bin/env bash
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="update-agent"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON="${PYTHON:-python3}"
VENV_DIR="${AGENT_DIR}/venv"

echo "========================================"
echo "  Instalando Agente de Atualizações"
echo "========================================"

if [ "$EUID" -eq 0 ]; then
    echo "[!] Rodando como root — instalando para o sistema"
    INSTALL_USER="${SUDO_USER:-root}"
    INSTALL_HOME="${SUDO_USER:+$(getent passwd "$SUDO_USER" | cut -d: -f6)}"
    INSTALL_HOME="${INSTALL_HOME:-$HOME}"
    VENV_DIR="${AGENT_DIR}/venv"
else
    echo "[*] Rodando como usuário comum — instalando localmente"
    INSTALL_USER="$USER"
    INSTALL_HOME="$HOME"
fi

echo ""
echo "[1/5] Verificando dependências do sistema..."
DEPS=("python3" "pip3" "git")
MISSING=()
for dep in "${DEPS[@]}"; do
    if ! command -v "$dep" &>/dev/null; then
        MISSING+=("$dep")
    fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "[!] Dependências faltando: ${MISSING[*]}"
    echo "    Instale com: sudo apt install python3 python3-pip git"
    exit 1
fi
echo "    OK"

echo ""
echo "[2/5] Criando ambiente virtual Python..."
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
    echo "    Virtualenv criado em ${VENV_DIR}"
else
    echo "    Virtualenv já existe"
fi

echo ""
echo "[3/5] Instalando dependências Python..."
"${VENV_DIR}/bin/pip" install --upgrade pip --quiet
"${VENV_DIR}/bin/pip" install -r "${AGENT_DIR}/requirements.txt" --quiet
echo "    OK"

echo ""
echo "[4/5] Verificando diretórios..."
mkdir -p "${AGENT_DIR}/memory"
touch "${AGENT_DIR}/agent.log"
chmod 644 "${AGENT_DIR}/agent.log"
echo "    OK"

echo ""
echo "[5/5] Abrindo porta do firewall (opcional)..."
if command -v ufw &>/dev/null; then
    PORT=$(grep listen_port "${AGENT_DIR}/config.yaml" 2>/dev/null | awk '{print $2}')
    if [ -n "$PORT" ] && [ "$PORT" -ne 0 ]; then
        ufw allow "$PORT/tcp" 2>/dev/null && echo "    Porta $PORT liberada no firewall" || true
    fi
fi

echo ""
echo "[5/5] Configurando serviço systemd..."

if [ "$EUID" -ne 0 ]; then
    echo ""
    echo "========================================"
    echo "  Instalação concluída!"
    echo "========================================"
    echo ""
    echo "Para instalar o serviço systemd, execute:"
    echo "  sudo ./install.sh"
    echo ""
    echo "Ou manualmente:"
    echo "  sudo cp ${SERVICE_FILE} ${SERVICE_FILE}"
    echo "  sudo systemctl daemon-reload"
    echo "  sudo systemctl enable ${SERVICE_NAME}"
    echo "  sudo systemctl start ${SERVICE_NAME}"
    echo ""
    echo "Para testar o agente agora:"
    echo "  ${VENV_DIR}/bin/python main.py"
    exit 0
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
description=Agente de Atualizações — IA Agentic para update de software
after=network.target
wants=network-online.target

[Service]
type=simple
user=${INSTALL_USER}
group=${INSTALL_USER}
workingdirectory=${AGENT_DIR}
execstart=${VENV_DIR}/bin/python ${AGENT_DIR}/main.py
restart=on-failure
restartsec=10
standardoutput=append:${AGENT_DIR}/agent.log
standarderror=append:${AGENT_DIR}/agent.log
environment=PYTHONUNBUFFERED=1

[Install]
wantedby=multi-user.target
EOF

echo "    Serviço criado em ${SERVICE_FILE}"

systemctl daemon-reload
echo "    systemd recarregado"

systemctl enable "$SERVICE_NAME"
echo "    Serviço habilitado para iniciar com o sistema"

systemctl start "$SERVICE_NAME"
echo "    Serviço iniciado"

echo ""
echo "========================================"
echo "  Instalação concluída!"
echo "========================================"
echo ""
echo "Comandos úteis:"
echo ""
echo "  Status:    systemctl status ${SERVICE_NAME}"
echo "  Logs:      journalctl -u ${SERVICE_NAME} -f"
echo "  Iniciar:   systemctl start ${SERVICE_NAME}"
echo "  Parar:     systemctl stop ${SERVICE_NAME}"
echo "  Reiniciar: systemctl restart ${SERVICE_NAME}"
echo ""
echo "Arquivos:"
echo "  Código:       ${AGENT_DIR}"
echo "  Config:       ${AGENT_DIR}/config.yaml"
echo "  Skills:       ${AGENT_DIR}/skills/"
echo "  Virtualenv:   ${VENV_DIR}"
echo "  Log local:    ${AGENT_DIR}/agent.log"
echo ""
echo "Multi-servidor (mesmo bot):"
echo "  Edite config.yaml em cada servidor:"
echo "    server_name: \"meu-servidor\""
echo "    listen_port: 9700"
echo "    is_coordinator: true   # só um servidor precisa do token"
echo "    peers:"
echo "      servidor-a: \"http://IP_A:9700\""
echo "      servidor-b: \"http://IP_B:9700\""
echo ""
echo "  Comandos no Telegram:"
echo "    /scan@servidor-a"
echo "    /update 9router@servidor-b"
echo "    /scan@all"
