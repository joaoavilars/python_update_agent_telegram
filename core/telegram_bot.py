import asyncio
import json
import logging
from datetime import datetime

import httpx

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .scanner import SoftwareScanner
from .updater import SoftwareUpdater
from .skill_manager import SkillManager

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(
        self,
        token: str,
        allowed_chat_ids: list[int],
        scanner: SoftwareScanner,
        updater: SoftwareUpdater,
        skill_manager: SkillManager,
        server_name: str = "",
        peers: dict[str, str] = None,
        is_coordinator: bool = True,
    ):
        self.token = token
        self.allowed_chat_ids = allowed_chat_ids
        self.scanner = scanner
        self.updater = updater
        self.skill_manager = skill_manager
        self.server_name = server_name
        self.peers = peers or {}
        self.is_coordinator = is_coordinator
        self.app = Application.builder().token(token).build()
        self._register_handlers()
        self.pending_confirmations = {}

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("scan", self._cmd_scan))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("update", self._cmd_update))
        self.app.add_handler(CommandHandler("simulate", self._cmd_simulate))
        self.app.add_handler(CommandHandler("report", self._cmd_report))
        self.app.add_handler(CommandHandler("skills", self._cmd_skills))
        self.app.add_handler(CommandHandler("servers", self._cmd_servers))
        self.app.add_handler(CommandHandler("cancel", self._cmd_cancel))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

    def _authorized(self, update: Update) -> bool:
        return update.effective_chat.id in self.allowed_chat_ids

    def _parse_target(self, text: str) -> tuple[str, str]:
        if "@" in text:
            parts = text.split("@", 1)
            return parts[0].strip(), parts[1].strip()
        return text.strip(), ""

    async def _forward_to_peer(self, server: str, command: str, args: list[str]) -> dict | None:
        url = self.peers.get(server)
        if not url:
            return None
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"{url}/command",
                    json={"command": command, "args": args},
                )
                if resp.status_code == 200:
                    return resp.json()
                logger.error(f"Peer {server} retornou {resp.status_code}: {resp.text}")
                return {"server": server, "type": "error", "data": f"Erro HTTP {resp.status_code} no servidor {server}"}
        except httpx.ConnectError:
            return {"server": server, "type": "error", "data": f"❌ Servidor `{server}` offline ou inacessível."}
        except Exception as e:
            return {"server": server, "type": "error", "data": f"❌ Erro ao contactar {server}: {e}"}

    async def _route_command(self, update: Update, command: str, args: list[str]):
        target_server, rest = self._parse_target(" ".join(args))
        actual_args = rest.split() if rest else []

        if target_server and target_server in self.peers:
            response = await self._forward_to_peer(target_server, command, actual_args)
            if response and response.get("data"):
                await update.message.reply_text(response["data"], parse_mode="Markdown")
            elif response:
                await update.message.reply_text(
                    f"❌ Sem resposta de `{target_server}`.", parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    f"❌ Servidor `{target_server}` não configurado como peer.", parse_mode="Markdown")

        elif target_server == "all":
            prefix = f"🌐 *Comando para TODOS os servidores*\n\n"
            parts = [prefix]
            for server, url in self.peers.items():
                response = await self._forward_to_peer(server, command, actual_args)
                if response and response.get("data"):
                    parts.append(f"*{server}:*\n{response['data']}\n")
                else:
                    parts.append(f"*{server}:* ❌ offline\n")
            if self.server_name:
                parts.append(self._run_local(command, actual_args))
            await update.message.reply_text("\n".join(parts), parse_mode="Markdown")

        else:
            if self.server_name:
                result = self._run_local(command, actual_args)
                await update.message.reply_text(result, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    "Use `@server` para escolher o servidor.\n"
                    "Ex: `/scan@lucanus` ou `/scan@all`\n"
                    "Veja servidores disponíveis com `/servers`.",
                    parse_mode="Markdown",
                )

    def _run_local(self, command: str, args: list[str]) -> str:
        from .listener import CommandListener
        handler = CommandListener(self.server_name, self.skill_manager)
        result = asyncio.run(handler.handle_command(command, args, 0))
        return result.get("data", "Sem resposta.")

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        server_info = f" em `{self.server_name}`" if self.server_name else ""
        await update.message.reply_text(
            f"🤖 *Agente de Atualizações*{server_info}\n\n"
            "Comandos:\n"
            "`/scan[@server]` — Escaneia softwares\n"
            "`/status[@server]` — Status atual\n"
            "`/skills[@server]` — Lista perfis\n"
            "`/simulate <nome>[@server]` — Simula update\n"
            "`/update <nome>[@server]` — Inicia update\n"
            "`/report <nome>[@server]` — Relatório\n"
            "`/servers` — Lista servidores\n"
            "`/cancel` — Cancela confirmação\n"
            "`/help` — Ajuda detalhada\n\n"
            "Ex: `/scan@lucanus` ou `/scan@all`",
            parse_mode="Markdown",
        )

    async def _cmd_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        await self._route_command(update, "scan", context.args)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        await self._route_command(update, "status", context.args)

    async def _cmd_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        await self._route_command(update, "skills", context.args)

    async def _cmd_simulate(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        if not context.args:
            await update.message.reply_text("Uso: `/simulate <nome>[@server]`", parse_mode="Markdown")
            return
        await self._route_command(update, "simulate", context.args)

    async def _cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        if not context.args:
            await update.message.reply_text("Uso: `/report <nome>[@server]`", parse_mode="Markdown")
            return
        await self._route_command(update, "report", context.args)

    async def _cmd_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        if not context.args:
            await update.message.reply_text("Uso: `/update <nome>[@server]`", parse_mode="Markdown")
            return

        target_server, rest = self._parse_target(" ".join(context.args))
        actual_args = rest.split() if rest else []
        actual_name = " ".join(actual_args) if actual_args else target_server

        if target_server and target_server in self.peers:
            response = await self._forward_to_peer(target_server, "update", actual_args if actual_args else [target_server])
            if response and response.get("data"):
                await update.message.reply_text(response["data"], parse_mode="Markdown")
                if response.get("extra"):
                    chat_id = update.effective_chat.id
                    self.pending_confirmations[chat_id] = {
                        "server": target_server,
                        "name": response["extra"]["name"],
                    }
        elif target_server and target_server not in self.peers and not rest:
            if self.server_name:
                await self._route_command(update, "update", [target_server])
        else:
            if self.server_name:
                await self._route_command(update, "update", context.args)

    async def _cmd_servers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        lines = ["🌐 *SERVIDORES DISPONÍVEIS*", ""]
        peers = self.peers or {}
        for server in sorted(peers.keys()):
            lines.append(f"  🖥️  `{server}`")
        if self.server_name:
            lines.append(f"  🖥️  `{self.server_name}` *(este servidor)*")
        if not peers and not self.server_name:
            lines.append("  Nenhum servidor configurado.")
        lines.append("")
        lines.append("Use `@server` nos comandos.\nEx: `/scan@lucanus`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if chat_id in self.pending_confirmations:
            del self.pending_confirmations[chat_id]
            await update.message.reply_text("❌ Confirmação cancelada.")
        else:
            await update.message.reply_text("Nenhuma confirmação pendente.")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        server_info = f" em `{self.server_name}`" if self.server_name else ""
        await update.message.reply_text(
            f"🤖 *Agente de Atualizações*{server_info}\n\n"
            "*Comandos:*\n"
            "`/scan[@server]`\n  Escaneia softwares.\n  Ex: `/scan`, `/scan@lucanus`, `/scan@all`\n\n"
            "`/status[@server]`\n  Status resumido.\n\n"
            "`/skills[@server]`\n  Lista perfis.\n\n"
            "`/simulate <nome>[@server]`\n  Simula update.\n  Ex: `/simulate 9router@lucanus`\n\n"
            "`/update <nome>[@server]`\n  Inicia update (pede confirmação depois).\n\n"
            "`/report <nome>[@server]`\n  Relatório completo.\n\n"
            "`/servers`\n  Lista servidores disponíveis.\n\n"
            "`/cancel`\n  Cancela confirmação pendente.\n\n"
            "*Fluxo:*\n"
            "`/scan@all` → `/simulate 9router@lucanus` → `/update 9router@lucanus` → `sim` → `/report 9router@lucanus`",
            parse_mode="Markdown",
        )

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        chat_id = update.effective_chat.id
        text = update.message.text.strip().lower()

        if chat_id not in self.pending_confirmations:
            return

        if text.startswith("sim@"):
            info = self.pending_confirmations[chat_id]
            target_server = text.split("@", 1)[1].strip()
            expected_server = info.get("server", "")
            if target_server != expected_server:
                await update.message.reply_text(
                    f"⚠️ Este sim era para `{expected_server}`, não `{target_server}`.", parse_mode="Markdown")
                return

            name = info["name"]
            del self.pending_confirmations[chat_id]
            msg = await update.message.reply_text(f"🔄 Update de *{name}* em *{target_server}*...", parse_mode="Markdown")

            response = await self._forward_to_peer(target_server, "confirm_update", [name])
            if response and response.get("data"):
                await msg.edit_text(response["data"], parse_mode="Markdown")
            else:
                await msg.edit_text(f"❌ Falha ao executar update em {target_server}.", parse_mode="Markdown")

        elif text == "sim":
            info = self.pending_confirmations.get(chat_id)
            if info and info.get("server"):
                expected = info["server"]
                await update.message.reply_text(
                    f"Use `sim@{expected}` para confirmar o update em `{expected}`.",
                    parse_mode="Markdown")
            else:
                info = self.pending_confirmations.pop(chat_id, None)
                if info:
                    await self._run_local_confirm(update, info["name"])
            return

        elif text in ("nao", "não"):
            self.pending_confirmations.pop(chat_id, None)
            await update.message.reply_text("❌ Update cancelado.")
        else:
            await update.message.reply_text("Responda `sim@servidor` ou `nao`. Use `/cancel` para cancelar.", parse_mode="Markdown")

    async def _run_local_confirm(self, update: Update, name: str):
        msg = await update.message.reply_text(f"🔄 Iniciando update de *{name}* localmente...", parse_mode="Markdown")
        try:
            result = await self.updater.execute_update(name)
            history_entry = {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "from_version": result.get("from_version", ""),
                "to_version": result.get("to_version", ""),
                "status": result.get("status", "unknown"),
                "notes": result.get("notes", ""),
                "adaptations": result.get("adaptations", []),
            }
            self.skill_manager.add_history_entry(name, history_entry)
            report_lines = [
                f"✅ *Update de {name} concluído!*",
                f"  Versão: `{result.get('from_version', '?')}` → `{result.get('to_version', '?')}`",
                f"  Status: {result.get('status', '?')}",
            ]
            if result.get("notes"):
                report_lines.append(f"  Notas: {result['notes']}")
            if result.get("adaptations"):
                report_lines.append(f"  Adaptações:")
                for a in result["adaptations"]:
                    report_lines.append(f"    • {a}")
            await msg.edit_text("\n".join(report_lines), parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ *Falha no update de {name}*: {e}", parse_mode="Markdown")

    async def send_alert(self, chat_id: int, message: str):
        bot = Bot(self.token)
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")

    async def start_polling(self):
        logger.info("Iniciando Telegram bot polling...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def stop(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
