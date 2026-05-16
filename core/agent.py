import asyncio
import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

from .scanner import SoftwareScanner
from .telegram_bot import TelegramBot
from .updater import SoftwareUpdater
from .skill_manager import SkillManager

logger = logging.getLogger(__name__)


class TimestampFileHandler(logging.handlers.RotatingFileHandler):
    def __init__(self, filename, max_bytes=0, backup_count=0,
                 retention_days=30, encoding='utf-8'):
        self.retention_days = retention_days
        self._date_seq = {}
        super().__init__(filename, maxBytes=max_bytes,
                         backupCount=backup_count, encoding=encoding)

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        today = datetime.now().strftime("%Y%m%d")
        self._date_seq[today] = self._date_seq.get(today, 0) + 1
        seq = self._date_seq[today]
        base_no_ext = str(self.baseFilename)
        if base_no_ext.endswith(".log"):
            base_no_ext = base_no_ext[:-4]
        dfn = f"{base_no_ext}_{today}.{seq}.log"

        if os.path.exists(self.baseFilename):
            os.rename(self.baseFilename, dfn)
            self._cleanup_old()

        self.stream = self._open()

    def _cleanup_old(self):
        if self.retention_days <= 0:
            return
        cutoff = datetime.now().timestamp() - (self.retention_days * 86400)
        base = Path(self.baseFilename)
        parent = base.parent
        stem = base.stem
        for f in sorted(parent.glob(f"{stem}_*")):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


class UpdateAgent:
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self._setup_logging()
        self.server_name = self.config.get("server_name", "")
        self.listen_port = self.config.get("listen_port", 0)
        self.peers = self.config.get("peers", {})
        self.is_coordinator = self.config.get("is_coordinator", False)

        self.skill_manager = SkillManager(self.config.get("skills_dir", "skills"))
        self.scanner = SoftwareScanner(self.skill_manager)
        self.updater = SoftwareUpdater(self.skill_manager)

        self.bot = None
        if self.is_coordinator or self.config.get("telegram", {}).get("token", "SEU_TOKEN_AQUI") != "SEU_TOKEN_AQUI":
            self.bot = TelegramBot(
                token=self.config["telegram"]["token"],
                allowed_chat_ids=self.config["telegram"]["allowed_chat_ids"],
                scanner=self.scanner,
                updater=self.updater,
                skill_manager=self.skill_manager,
                server_name=self.server_name,
                peers=self.peers,
                is_coordinator=self.is_coordinator,
            )
        self.running = False
        self.http_server = None
        self.evolution = None

        evo_cfg = self.config.get("evolution_api", {})
        if evo_cfg.get("enabled") and evo_cfg.get("base_url"):
            from .evolution_api import EvolutionAPI
            self.evolution = EvolutionAPI(
                base_url=evo_cfg["base_url"],
                api_key=evo_cfg["api_key"],
                instance=evo_cfg["instance"],
                group_jid=evo_cfg.get("group_jid", ""),
            )
            logger.info(f"Evolution API configurado: {evo_cfg['base_url']}")

    def _load_config(self, path: str) -> dict:
        if not Path(path).exists():
            logger.warning(f"Config {path} não encontrado, usando defaults")
            return {
                "telegram": {"token": "SEU_TOKEN_AQUI", "allowed_chat_ids": []},
                "server_name": "",
                "listen_port": 0,
                "is_coordinator": False,
                "peers": {},
                "skills_dir": "skills",
                "scan_interval_hours": 24,
                "auto_notify": True,
                "logging": {"max_size_mb": 10, "max_files": 30, "retention_days": 30},
                "evolution_api": {"enabled": False},
            }
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _setup_logging(self):
        log_cfg = self.config.get("logging", {})
        max_mb = log_cfg.get("max_size_mb", 10)
        max_bytes = max_mb * 1024 * 1024
        ret_days = log_cfg.get("retention_days", 30)
        log_dir = log_cfg.get("log_dir", ".")

        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = str(log_path / "agent.log")

        file_handler = TimestampFileHandler(
            filename=log_file,
            max_bytes=max_bytes,
            retention_days=ret_days,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                file_handler,
            ],
        )
        logger.info(f"Log configurado: max {max_mb}MB/arquivo, retenção {ret_days} dias")

    async def _start_http_listener(self):
        if not self.listen_port:
            return

        try:
            from fastapi import FastAPI, Request
            import uvicorn

            app = FastAPI()

            from .listener import CommandListener
            handler = CommandListener(self.server_name, self.skill_manager)

            @app.post("/command")
            async def handle_command(request: Request):
                body = await request.json()
                command = body.get("command", "")
                args = body.get("args", [])
                result = await handler.handle_command(command, args, 0)
                return result

            if self.evolution:
                @app.post("/whatsapp-webhook")
                async def whatsapp_webhook(request: Request):
                    body = await request.json()
                    parsed = self.evolution.parse_webhook(body)
                    if not parsed:
                        return {"status": "ignored"}

                    cmd_info = self.evolution.extract_command(parsed["text"])
                    if not cmd_info:
                        return {"status": "not_a_command"}

                    command, args, target_server = cmd_info
                    logger.info(f"WhatsApp /{command} {' '.join(args)} (target={target_server or 'local'}) de {parsed['sender']}")

                    if target_server and target_server != self.server_name and target_server in self.peers:
                        url = self.peers[target_server]
                        try:
                            import httpx
                            async with httpx.AsyncClient(timeout=300) as client:
                                resp = await client.post(
                                    f"{url}/command",
                                    json={"command": command, "args": args},
                                )
                                if resp.status_code == 200:
                                    data = resp.json()
                                    resp_text = data.get("data", "Sem resposta.")
                                else:
                                    resp_text = f"Erro HTTP {resp.status_code} em {target_server}"
                        except Exception as e:
                            resp_text = f"Servidor {target_server} offline: {e}"
                    else:
                        response_data = await handler.handle_command(command, args, 0)
                        resp_text = response_data.get("data", "Sem resposta.")

                    await self.evolution.send_text(resp_text, parsed["jid"])
                    return {"status": "ok"}

            @app.get("/health")
            async def health():
                return {"server": self.server_name, "status": "ok"}

            config = uvicorn.Config(app, host="0.0.0.0", port=self.listen_port, log_level="error")
            self.http_server = uvicorn.Server(config)
            logger.info(f"HTTP listener iniciado na porta {self.listen_port}")
            await self.http_server.serve()
        except ImportError:
            logger.warning("fastapi/uvicorn não instalados — listener HTTP desabilitado")
        except SystemExit:
            logger.error(f"Porta {self.listen_port} já em uso — listener HTTP desabilitado")
        except Exception as e:
            logger.error(f"Erro no HTTP listener: {e}")

    async def _get_server_name(self) -> str:
        if self.server_name:
            return self.server_name
        try:
            proc = await asyncio.create_subprocess_shell(
                "hostname",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode(errors="ignore").strip()
        except Exception:
            return "servidor"

    async def _notify_all(self, message: str):
        if not self.bot:
            return
        for chat_id in self.config["telegram"]["allowed_chat_ids"]:
            try:
                await self.bot.send_alert(chat_id, message)
            except Exception as e:
                logger.error(f"Erro ao notificar chat {chat_id}: {e}")

    async def _check_mismatches(self):
        results = self.scanner.scan_all()
        mismatches = [r for r in results if r["status"] == "version_mismatch"]
        errors = [r for r in results if r["status"] in ("not_found", "error")]
        return results, mismatches, errors

    async def _startup_scan(self):
        server = await self._get_server_name()
        logger.info(f"Executando scan inicial no {server}...")

        await self._notify_all(
            f"🚀 *Agente iniciado* — `{server}`\n"
            f"Escaneando softwares..."
        )

        results, mismatches, errors = await self._check_mismatches()
        report = self.scanner.format_scan_report(results)
        await self._notify_all(report)

        if mismatches or errors:
            alert_parts = [f"⚠️ *Atenção no {server}:*"]
            for m in mismatches:
                alert_parts.append(f"  • *{m['name']}* — versão `{m['detected_version']}` (esperado `{m['expected_version']}`)")
            for e in errors:
                alert_parts.append(f"  • *{e['name']}* — {e['status']}")
            await self._notify_all("\n".join(alert_parts))

    async def _periodic_scan(self):
        interval = self.config.get("scan_interval_hours", 24) * 3600
        health_interval = max(interval // 6, 3600)
        health_counter = 0

        while self.running:
            await asyncio.sleep(health_interval)
            health_counter += 1
            logger.info("Executando verificação de saúde...")
            try:
                _, mismatches, errors = await self._check_mismatches()
                if mismatches or errors:
                    server = await self._get_server_name()
                    alert_parts = [f"⚠️ *Problemas detectados — {server}*"]
                    for m in mismatches:
                        alert_parts.append(f"  • *{m['name']}* — `{m['detected_version']}` (esperado `{m['expected_version']}`)")
                    for e in errors:
                        alert_parts.append(f"  • *{e['name']}* — {e['status']}")
                    alert_parts.append("")
                    alert_parts.append("Use `/update <nome>@<server>` para atualizar ou `/status` para detalhes.")
                    await self._notify_all("\n".join(alert_parts))

                if health_counter >= 6:
                    health_counter = 0
                    logger.info("Executando scan completo periódico...")
                    results, _, _ = await self._check_mismatches()
                    report = self.scanner.format_scan_report(results)
                    await self._notify_all(report)
            except Exception as e:
                logger.error(f"Erro na verificação: {e}")

    async def run(self):
        self.running = True
        server_label = self.server_name or "local"
        logger.info(f"Iniciando UpdateAgent em '{server_label}' (coordenador={self.is_coordinator})...")

        if self.is_coordinator:
            asyncio.create_task(self._startup_scan())
            asyncio.create_task(self._periodic_scan())

        if self.listen_port:
            asyncio.create_task(self._start_http_listener())

        if self.is_coordinator:
            await self.bot.start_polling()
            logger.info(f"Coordenador '{server_label}' rodando. Pressione Ctrl+C para parar.")
        else:
            logger.info(f"Worker '{server_label}' rodando (HTTP listener na porta {self.listen_port}).")

        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            if self.http_server:
                self.http_server.should_exit = True
            if self.is_coordinator:
                await self.bot.stop()
            logger.info("Agente parado.")
