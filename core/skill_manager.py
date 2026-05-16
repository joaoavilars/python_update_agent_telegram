import os
import yaml
from pathlib import Path
from typing import Optional


class SkillManager:
    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(skills_dir)
        self._cache = {}

    def list_skills(self) -> list[dict]:
        skills = []
        for f in sorted(self.skills_dir.glob("*.yaml")):
            if f.name.startswith("_"):
                continue
            skills.append(self.load_skill(f.stem))
        return skills

    def load_skill(self, name: str) -> Optional[dict]:
        if name in self._cache:
            return self._cache[name]
        path = self.skills_dir / f"{name}.yaml"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._cache[name] = data
        return data

    def reload_skill(self, name: str) -> Optional[dict]:
        self._cache.pop(name, None)
        return self.load_skill(name)

    def save_skill(self, name: str, data: dict):
        path = self.skills_dir / f"{name}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        self._cache[name] = data

    def add_history_entry(self, name: str, entry: dict):
        data = self.load_skill(name)
        if not data:
            return
        if "history" not in data:
            data["history"] = []
        data["history"].insert(0, entry)
        data["software"]["current_version"] = entry.get("to_version", data["software"].get("current_version", ""))
        self.save_skill(name, data)

    def _backup_summary(self, data: dict) -> str:
        bk = data.get("backup", {})
        if not bk.get("enabled"):
            return "  Backup: ❌ Desabilitado"
        parts = ["  Backup: ✅ Habilitado"]
        parts.append(f"    Destino: `{bk.get('destination', '?')}`")
        parts.append(f"    Retenção: {bk.get('retention_days', '?')} dias")
        fs = bk.get("filesystem", {})
        if fs.get("paths"):
            parts.append(f"    Arquivos: {len(fs['paths'])} diretório(s)")
        dk = bk.get("docker", {})
        if dk:
            docker_items = []
            if dk.get("commit_image"):
                docker_items.append("commit imagem")
            if dk.get("export_container"):
                docker_items.append("export .tar")
            if dk.get("volumes"):
                docker_items.append(f"{len(dk['volumes'])} volume(s)")
            if docker_items:
                parts.append(f"    Docker: {', '.join(docker_items)}")
        db = bk.get("database", {})
        if db.get("type"):
            parts.append(f"    Banco: {db['type']} ({db.get('database', '?')})")
        return "\n".join(parts)

    def get_summary(self, name: str) -> Optional[str]:
        data = self.load_skill(name)
        if not data:
            return None
        sw = data.get("software", {})
        hist = data.get("history", [])
        last = hist[0] if hist else {}
        lines = [
            f"*{sw.get('name', name)}*",
            f"  Versão atual: `{sw.get('current_version', '?')}`",
            f"  Tipo: {sw.get('type', '?')}",
            f"  Path: `{sw.get('install_path', '?')}`",
            self._backup_summary(data),
        ]
        if last:
            lines.append(f"  Último update: {last.get('date', '?')} - _{last.get('status', '?')}_")
            if last.get("notes"):
                lines.append(f"  Obs: {last['notes']}")
        if data.get("update") and data["update"].get("source_url"):
            lines.append(f"  Fonte: {data['update']['source_url']}")
        return "\n".join(lines)

    def get_detailed_report(self, name: str) -> Optional[str]:
        data = self.load_skill(name)
        if not data:
            return None
        sw = data.get("software", {})
        hist = data.get("history", [])
        lines = [
            f"📋 *RELATÓRIO COMPLETO: {sw.get('name', name)}*",
            f"",
            f"*Informações do Software*",
            f"  Nome: {sw.get('name', '?')}",
            f"  Tipo: {sw.get('type', '?')}",
            f"  Versão Atual: `{sw.get('current_version', '?')}`",
            f"  Path: `{sw.get('install_path', '?')}`",
            f"",
        ]
        upd = data.get("update", {})
        if upd:
            lines.extend([
                f"*Procedimento de Update*",
                f"  Método: {upd.get('method', '?')}",
                f"  Fonte: {upd.get('source_url', '?')}",
            ])
            pre = upd.get("pre_update_hooks", [])
            if pre:
                lines.append(f"  Pré-hooks: {len(pre)} comando(s)")
            pos = upd.get("post_update_hooks", [])
            if pos:
                lines.append(f"  Pós-hooks: {len(pos)} comando(s)")

        bk = data.get("backup", {})
        if bk.get("enabled"):
            lines.extend([
                f"",
                f"*Backup*",
                f"  Status: ✅ Habilitado",
                f"  Destino: `{bk.get('destination', '?')}`",
                f"  Retenção: {bk.get('retention_days', '?')} dias",
            ])
            fs = bk.get("filesystem", {})
            if fs.get("paths"):
                for p in fs["paths"]:
                    lines.append(f"  📁 `{p}`")
            dk = bk.get("docker", {})
            if dk:
                items = []
                if dk.get("commit_image"):
                    items.append("commit imagem")
                if dk.get("export_container"):
                    items.append("export .tar")
                if dk.get("volumes"):
                    for v in dk["volumes"]:
                        lines.append(f"  🐳 Volume: `{v}`")
                if items:
                    lines.append(f"  🐳 {', '.join(items)}")
            db = bk.get("database", {})
            if db.get("type"):
                lines.append(f"  🗄️  BD {db['type']}: `{db.get('database', '?')}` em {db.get('host', '?')}:{db.get('port', '?')}")

        val = data.get("validation", {})
        if val:
            lines.extend([
                f"",
                f"*Validação*",
                f"  Comandos: {len(val.get('commands', []))}",
                f"  Expectativas: {len(val.get('expected', []))}",
            ])

        lines.extend([
            f"",
            f"*Histórico de Atualizações ({len(hist)} registro(s))*",
        ])
        for h in hist:
            lines.append(f"")
            lines.append(f"  📅 {h.get('date', '?')}")
            lines.append(f"  {h.get('from_version', '?')} → {h.get('to_version', '?')}")
            lines.append(f"  Status: {'✅ Sucesso' if h.get('status') == 'success' else '⚠️ Parcial' if h.get('status') == 'partial' else '❌ Falha'}")
            if h.get("notes"):
                lines.append(f"  Notas: {h['notes']}")
            adapt = h.get("adaptations", [])
            if adapt:
                lines.append(f"  Adaptações:")
                for a in adapt:
                    lines.append(f"    • {a}")

        return "\n".join(lines)

    def simulate_update(self, name: str) -> Optional[str]:
        data = self.load_skill(name)
        if not data:
            return None
        sw = data.get("software", {})
        upd = data.get("update", {})
        bk = data.get("backup", {})
        hist = data.get("history", [])
        lines = [
            f"🔄 *SIMULAÇÃO DE UPDATE: {sw.get('name', name)}*",
            f"",
            f"  Versão atual: `{sw.get('current_version', '?')}`",
            f"  Método: {upd.get('method', '?')}",
        ]

        if bk.get("enabled"):
            lines.append(f"")
            lines.append(f"  *Backup programado:*")
            lines.append(f"    📁 Filesystem: {'Sim' if bk.get('filesystem', {}).get('paths') else 'Não'}")
            dk = bk.get("docker", {})
            if dk:
                items = []
                if dk.get("commit_image"):
                    items.append("commit imagem")
                if dk.get("export_container"):
                    items.append("export .tar")
                if dk.get("volumes"):
                    items.append(f"{len(dk['volumes'])} volumes")
                if items:
                    lines.append(f"    🐳 Docker: {', '.join(items)}")
            db = bk.get("database", {})
            if db.get("type"):
                lines.append(f"    🗄️  BD {db['type']}: dump de `{db.get('database', '?')}`")
        else:
            lines.append(f"")
            lines.append(f"  ⚠️  *Backup: DESABILITADO*")

        hooks_added = False
        pre = upd.get("pre_update_hooks", [])
        if pre:
            if not hooks_added:
                lines.append(f"")
                hooks_added = True
            lines.append(f"  *Passos antes do update:*")
            for i, cmd in enumerate(pre, 1):
                lines.append(f"    {i}. `{cmd}`")
        pos = upd.get("post_update_hooks", [])
        if pos:
            if not hooks_added:
                lines.append(f"")
                hooks_added = True
            lines.append(f"  *Passos depois do update:*")
            for i, cmd in enumerate(pos, 1):
                lines.append(f"    {i}. `{cmd}`")
        if hist:
            last = hist[0]
            lines.append(f"")
            lines.append(f"  *Último update:* {last.get('date', '?')} ({last.get('status', '?')})")
            if last.get("notes"):
                lines.append(f"  ⚠️  Atenção: {last['notes']}")
        lines.append(f"")
        lines.append(f"  *Riscos identificados:*")
        risks = []
        if hist:
            fails = [h for h in hist if h.get("status") in ("fail", "partial")]
            if fails:
                risks.append(f"    ⚠️  {len(fails)} update(s) anterior(es) com problemas")
                for f in fails[:3]:
                    if f.get("notes"):
                        risks.append(f"       - {f['notes']}")
        if not risks:
            risks.append(f"    ✅ Nenhum risco conhecido")
        lines.extend(risks)
        return "\n".join(lines)
