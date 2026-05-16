import asyncio
import json
import logging

from .skill_manager import SkillManager
from .scanner import SoftwareScanner
from .updater import SoftwareUpdater

logger = logging.getLogger(__name__)


class CommandListener:
    def __init__(self, server_name: str, skill_manager: SkillManager):
        self.server_name = server_name
        self.skill_manager = skill_manager
        self.scanner = SoftwareScanner(skill_manager)
        self.updater = SoftwareUpdater(skill_manager)

    async def handle_command(self, command: str, args: list[str], chat_id: int) -> dict:
        logger.info(f"Comando recebido: /{command} {' '.join(args)} (de chat {chat_id})")

        if command == "scan":
            results = self.scanner.scan_all()
            return {
                "server": self.server_name,
                "type": "scan",
                "data": self.scanner.format_scan_report(results),
            }

        elif command == "status":
            lines = [f"📊 *STATUS — {self.server_name}*", ""]
            for skill in self.skill_manager.list_skills():
                sw = skill.get("software", {})
                hist = skill.get("history", [])
                last = hist[0] if hist else {}
                s = "✅" if last.get("status") == "success" else "⚠️" if last.get("status") == "partial" else "❓"
                lines.append(f"  {s} *{sw.get('name', '?')}* — `{sw.get('current_version', '?')}`")
                lines.append(f"     Último update: {last.get('date', 'Nunca')} ({last.get('status', 'N/A')})")
            return {"server": self.server_name, "type": "status", "data": "\n".join(lines)}

        elif command == "skills":
            skills = self.skill_manager.list_skills()
            lines = [f"📂 *PERFIS — {self.server_name}*", ""]
            for s in skills:
                sw = s.get("software", {})
                lines.append(f"  • `{sw.get('name', '?')}` — v{sw.get('current_version', '?')}")
            if not skills:
                lines.append("  Nenhum perfil encontrado.")
            return {"server": self.server_name, "type": "skills", "data": "\n".join(lines)}

        elif command == "simulate":
            if not args:
                return {"server": self.server_name, "type": "error", "data": "Uso: simulate <nome>"}
            name = " ".join(args)
            report = self.skill_manager.simulate_update(name)
            if not report:
                return {"server": self.server_name, "type": "error", "data": f"❌ Perfil `{name}` não encontrado."}
            return {"server": self.server_name, "type": "simulate", "data": report}

        elif command == "report":
            if not args:
                return {"server": self.server_name, "type": "error", "data": "Uso: report <nome>"}
            name = " ".join(args)
            report = self.skill_manager.get_detailed_report(name)
            if not report:
                return {"server": self.server_name, "type": "error", "data": f"❌ Perfil `{name}` não encontrado."}
            return {"server": self.server_name, "type": "report", "data": report}

        elif command == "update":
            if not args:
                return {"server": self.server_name, "type": "error", "data": "Uso: update <nome>"}
            name = " ".join(args)
            skill = self.skill_manager.load_skill(name)
            if not skill:
                return {"server": self.server_name, "type": "error", "data": f"❌ Perfil `{name}` não encontrado."}
            sim = self.skill_manager.simulate_update(name)
            return {
                "server": self.server_name,
                "type": "update_confirm",
                "data": f"{sim}\n\n⚠️ *Confirma atualização de {name} em {self.server_name}?*\nResponda `sim@{self.server_name}` para confirmar.",
                "extra": {"name": name, "server": self.server_name},
            }

        elif command == "confirm_update":
            if not args:
                return {"server": self.server_name, "type": "error", "data": "Uso: confirm_update <nome>"}
            name = " ".join(args)
            try:
                result = await self.updater.execute_update(name)
                from datetime import datetime
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
                    f"✅ *Update de {name} em {self.server_name} concluído!*",
                    f"  Versão: `{result.get('from_version', '?')}` → `{result.get('to_version', '?')}`",
                    f"  Status: {result.get('status', '?')}",
                ]
                if result.get("notes"):
                    report_lines.append(f"  Notas: {result['notes']}")
                if result.get("adaptations"):
                    report_lines.append(f"  Adaptações:")
                    for a in result["adaptations"]:
                        report_lines.append(f"    • {a}")
                return {"server": self.server_name, "type": "update_result", "data": "\n".join(report_lines)}
            except Exception as e:
                return {"server": self.server_name, "type": "error", "data": f"❌ *Falha no update de {name} em {self.server_name}*: {e}"}

        elif command == "ping":
            return {"server": self.server_name, "type": "ping", "data": "pong"}

        else:
            return {"server": self.server_name, "type": "error", "data": f"Comando desconhecido: {command}"}
