import asyncio
import re
from pathlib import Path

from .skill_manager import SkillManager


class SoftwareScanner:
    def __init__(self, skill_manager: SkillManager):
        self.skill_manager = skill_manager

    def scan_all(self) -> list[dict]:
        results = []
        for skill in self.skill_manager.list_skills():
            sw = skill.get("software", {})
            name = sw.get("name", "unknown")
            result = {
                "name": name,
                "expected_version": sw.get("current_version", "?"),
                "detected_version": None,
                "status": "unknown",
                "errors": [],
            }
            try:
                detected = self._detect_version(skill)
                result["detected_version"] = detected
                expected = sw.get("current_version", "")
                if detected and expected:
                    result["status"] = "ok" if detected == expected else "version_mismatch"
                elif detected:
                    result["status"] = "ok"
                else:
                    result["status"] = "not_found"
            except Exception as e:
                result["status"] = "error"
                result["errors"].append(str(e))
            results.append(result)
        return results

    def _detect_version(self, skill: dict) -> str | None:
        detection = skill.get("detection", {})
        sw_type = skill.get("software", {}).get("type", "")
        method = detection.get("method", "command")

        if method == "docker" or sw_type == "docker":
            return self._detect_docker(detection)
        elif method == "command":
            return self._detect_by_command(detection)
        elif method == "file":
            return self._detect_by_file(detection)
        elif method == "registry":
            return self._detect_by_registry(detection)
        return None

    def _detect_docker(self, detection: dict) -> str | None:
        docker_cfg = detection.get("docker", {})
        container = docker_cfg.get("container_name", "")
        if not container:
            return None

        try:
            proc = asyncio.run(asyncio.create_subprocess_shell(
                f"docker ps --filter name={container} --filter status=running --format {{{{.Names}}}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ))
            stdout, _ = proc.communicate(timeout=15)
            if not stdout.decode(errors="ignore").strip():
                return None
        except Exception:
            return None

        cmd = detection.get("version_command")
        if cmd:
            return self._detect_by_command(detection)

        try:
            proc = asyncio.run(asyncio.create_subprocess_shell(
                f"docker inspect {container} --format {{{{.Config.Image}}}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ))
            stdout, _ = proc.communicate(timeout=15)
            image = stdout.decode(errors="ignore").strip()
            pattern = detection.get("version_regex", r"(\\d+\\.\\d+\\.\\d+)")
            match = re.search(pattern, image)
            return match.group(1) if match else image
        except Exception:
            return None

    def _detect_by_command(self, detection: dict) -> str | None:
        cmd = detection.get("version_command")
        if not cmd:
            return None
        try:
            import subprocess
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr
            pattern = detection.get("version_regex", r"v?(\d+\.\d+\.\d+)")
            match = re.search(pattern, output)
            return match.group(1) if match else output.strip()[:50]
        except Exception:
            return None

    def _detect_by_file(self, detection: dict) -> str | None:
        file_path = detection.get("file_path")
        if not file_path or not Path(file_path).exists():
            return None
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            pattern = detection.get("version_regex", r"v?(\d+\.\d+\.\d+)")
            match = re.search(pattern, content)
            return match.group(1) if match else None
        except Exception:
            return None

    def _detect_by_registry(self, detection: dict) -> str | None:
        key = detection.get("registry_key")
        value = detection.get("registry_value", "DisplayVersion")
        if not key:
            return None
        try:
            import subprocess
            result = subprocess.run(
                f'reg query "{key}" /v "{value}"',
                shell=True, capture_output=True, text=True, timeout=15
            )
            pattern = detection.get("version_regex", r"(\d+\.\d+\.\d+)")
            match = re.search(pattern, result.stdout)
            return match.group(1) if match else None
        except Exception:
            return None

    def format_scan_report(self, results: list[dict]) -> str:
        lines = ["🔍 *RELATÓRIO DE SCAN*", ""]
        ok_count = 0
        problem_count = 0
        for r in results:
            status_icon = {
                "ok": "✅",
                "version_mismatch": "⚠️",
                "not_found": "❌",
                "error": "💥",
                "unknown": "❓",
            }.get(r["status"], "❓")
            line = f"  {status_icon} *{r['name']}*"
            if r["detected_version"]:
                line += f" — `{r['detected_version']}`"
            if r["status"] == "version_mismatch":
                line += f" (esperado: `{r['expected_version']}`)"
                problem_count += 1
            elif r["status"] == "ok":
                ok_count += 1
            else:
                problem_count += 1
            if r["errors"]:
                line += f" — {'; '.join(r['errors'])}"
            lines.append(line)
        lines.extend([
            "",
            f"  *Resumo:* {ok_count} ok, {problem_count} com problema",
        ])
        return "\n".join(lines)
