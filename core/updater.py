import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path

from .skill_manager import SkillManager

logger = logging.getLogger(__name__)


class SoftwareUpdater:
    def __init__(self, skill_manager: SkillManager):
        self.skill_manager = skill_manager

    async def execute_update(self, name: str) -> dict:
        skill = self.skill_manager.load_skill(name)
        if not skill:
            raise ValueError(f"Perfil '{name}' não encontrado")

        result = {
            "from_version": skill.get("software", {}).get("current_version", "?"),
            "to_version": "?",
            "status": "success",
            "notes": "",
            "adaptations": [],
        }

        update_config = skill.get("update", {})
        backup_config = skill.get("backup", {})
        sw = skill.get("software", {})

        try:
            if backup_config.get("enabled", False):
                await self._run_backup(sw, backup_config, result)

            await self._run_hooks(update_config.get("pre_update_hooks", []), "pré-update", result)

            new_version = await self._apply_update(skill, result)
            if new_version:
                result["to_version"] = new_version

            await self._run_hooks(update_config.get("post_update_hooks", []), "pós-update", result)

            validation_ok = await self._validate(skill, result)
            if not validation_ok:
                result["status"] = "partial"
                result["notes"] += "Validação pós-update falhou em alguns pontos. "

        except Exception as e:
            result["status"] = "fail"
            result["notes"] += f"Erro: {str(e)}. "
            if backup_config.get("enabled", False):
                try:
                    await self._restore_backup(sw, backup_config, result)
                except Exception as restore_err:
                    result["notes"] += f"Falha ao restaurar backup: {restore_err}. "

        return result

    async def _run_backup(self, sw: dict, backup_config: dict, result: dict):
        dest = backup_config.get("destination", "")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        sw_name = sw.get("name", "unknown")
        base_dir = Path(dest) if dest else Path(f"/opt/backups/{sw_name}")
        backup_dir = base_dir.parent / f"{base_dir.name}_{ts}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        fs_cfg = backup_config.get("filesystem", {})
        if fs_cfg:
            await self._backup_filesystem(fs_cfg, backup_dir, result)

        docker_cfg = backup_config.get("docker", {})
        docker_meta = sw.get("detection", {}).get("docker", {})
        container = docker_cfg.get("container_name") or docker_meta.get("container_name", "")
        if container:
            await self._backup_docker(container, docker_cfg, backup_dir, result)

        db_cfg = backup_config.get("database", {})
        if db_cfg and db_cfg.get("type"):
            await self._backup_database(db_cfg, backup_dir, result)

        if not fs_cfg and not docker_cfg and not db_cfg:
            result["notes"] += "Backup habilitado mas nenhuma configuração definida. "

    async def _backup_filesystem(self, fs_cfg: dict, backup_dir: Path, result: dict):
        paths = fs_cfg.get("paths", [])
        for src in paths:
            src_path = Path(src)
            if not src_path.exists():
                result["notes"] += f"Path '{src}' não existe, ignorado. "
                continue
            try:
                dst = backup_dir / src_path.name
                if src_path.is_dir():
                    shutil.copytree(src, str(dst))
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, str(dst))
                logger.info(f"Backup filesystem: {src} -> {dst}")
            except Exception as e:
                result["adaptations"].append(f"Falha no backup de '{src}': {e}")
                result["notes"] += f"Falha no backup de '{src}'. "
        result["notes"] += f"Backup filesystem em {backup_dir}. "

    async def _backup_docker(self, container: str, docker_cfg: dict, backup_dir: Path, result: dict):
        if docker_cfg.get("commit_image"):
            try:
                tag = f"{container}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                proc = await asyncio.create_subprocess_shell(
                    f"docker commit {container} {tag}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate(timeout=60)
                if proc.returncode == 0:
                    result["notes"] += f"Imagem Docker '{tag}' salva. "
                    logger.info(f"Docker commit: {container} -> {tag}")
                else:
                    result["adaptations"].append(f"docker commit falhou: {stderr.decode(errors='ignore').strip()[:100]}")
            except Exception as e:
                result["adaptations"].append(f"docker commit exception: {e}")

        if docker_cfg.get("export_container"):
            try:
                tar_path = backup_dir / f"{container}.tar"
                proc = await asyncio.create_subprocess_shell(
                    f"docker export {container} -o {tar_path}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate(timeout=120)
                if proc.returncode == 0:
                    result["notes"] += f"Container exportado para {tar_path}. "
                else:
                    result["adaptations"].append(f"docker export falhou: {stderr.decode(errors='ignore').strip()[:100]}")
            except Exception as e:
                result["adaptations"].append(f"docker export exception: {e}")

        volumes = docker_cfg.get("volumes", [])
        for vol in volumes:
            try:
                parts = vol.split(":")
                vol_name = parts[0]
                vol_dest = parts[1] if len(parts) > 1 else str(backup_dir / vol_name)
                vol_path = Path(vol_dest)
                vol_path.mkdir(parents=True, exist_ok=True)
                proc = await asyncio.create_subprocess_shell(
                    f"docker run --rm -v {vol_name}:/source -v {vol_path.absolute()}:/dest alpine sh -c 'cp -a /source/. /dest/'",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate(timeout=120)
                if proc.returncode == 0:
                    result["notes"] += f"Volume '{vol_name}' copiado. "
                else:
                    result["adaptations"].append(f"Backup volume '{vol_name}' falhou")
            except Exception as e:
                result["adaptations"].append(f"Backup volume exception: {e}")

    async def _backup_database(self, db_cfg: dict, backup_dir: Path, result: dict):
        db_type = db_cfg.get("type", "").lower()
        db_name = db_cfg.get("database", "")
        host = db_cfg.get("host", "localhost")
        port = db_cfg.get("port", 5432)
        user = db_cfg.get("user", "postgres")
        dump_path = db_cfg.get("dump_path", "")
        pass_var = db_cfg.get("password_env", "PGPASSWORD")

        if not db_name:
            return

        dump_dir = Path(dump_path) if dump_path else backup_dir / "db"
        dump_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dump_file = dump_dir / f"{db_name}_{ts}.sql"

        try:
            if db_type == "postgres":
                cmd = (f"PGPASSWORD=${{{pass_var}:''}} pg_dump -h {host} -p {port} "
                       f"-U {user} -d {db_name} -F c -f {dump_file}")
            elif db_type == "mysql":
                cmd = (f"MYSQL_PWD=${{{pass_var}:''}} mysqldump -h {host} -P {port} "
                       f"-u {user} {db_name} > {dump_file}")
            elif db_type == "mongo":
                cmd = f"mongodump --host {host} --port {port} --db {db_name} --out {dump_dir}/mongo_{ts}"
            else:
                result["notes"] += f"Tipo de banco '{db_type}' não suportado. "
                return

            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(timeout=300)
            if proc.returncode == 0:
                result["notes"] += f"Backup DB '{db_name}' em {dump_file}. "
            else:
                err = stderr.decode(errors="ignore").strip()[:100]
                result["adaptations"].append(f"Backup DB '{db_name}' falhou: {err}")
                result["notes"] += f"Backup DB '{db_name}' falhou. "
        except Exception as e:
            result["adaptations"].append(f"Exceção backup DB '{db_name}': {e}")

    async def _restore_backup(self, sw: dict, backup_config: dict, result: dict):
        result["notes"] += "Restauração automática não implementada — faça manualmente. "

    async def _run_hooks(self, hooks: list, stage: str, result: dict):
        if not hooks:
            return
        for cmd in hooks:
            try:
                logger.info(f"Executando hook {stage}: {cmd}")
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    result["adaptations"].append(
                        f"Hook {stage} retornou código {proc.returncode}: {stderr.decode(errors='ignore').strip()}"
                    )
                    result["notes"] += f"Hook '{cmd}' falhou (código {proc.returncode}). "
            except Exception as e:
                result["adaptations"].append(f"Exceção no hook {stage} '{cmd}': {e}")
                result["notes"] += f"Exceção no hook '{cmd}': {e}. "

    async def _apply_update(self, skill: dict, result: dict) -> str | None:
        update_config = skill.get("update", {})
        method = update_config.get("method", "manual")

        if method == "docker":
            return await self._update_docker(update_config, result)
        elif method == "command":
            return await self._update_by_command(update_config, result)
        elif method == "git_pull":
            return await self._update_git_pull(update_config, result)
        elif method in ("pip", "npm"):
            return await self._update_package_manager(update_config, result)
        elif method == "manual":
            result["notes"] += "Update manual — sem automação definida. "
            return None
        else:
            result["notes"] += f"Método '{method}' não implementado. "
            return None

    async def _update_docker(self, config: dict, result: dict) -> str | None:
        docker_cfg = config.get("docker", {})
        container = docker_cfg.get("container_name", "")
        image = docker_cfg.get("image", "")
        compose_file = docker_cfg.get("compose_file", "")
        compose_service = docker_cfg.get("compose_service", "")

        old_image = ""
        if container:
            try:
                proc = await asyncio.create_subprocess_shell(
                    f"docker inspect {container} --format {{{{.Config.Image}}}}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate(timeout=15)
                old_image = stdout.decode(errors="ignore").strip()
            except Exception:
                pass

        if compose_file and Path(compose_file).exists():
            result["notes"] += "Usando docker-compose. "

            if docker_cfg.get("pull_image", True):
                pull_cmd = f"docker-compose -f {compose_file} pull"
                if compose_service:
                    pull_cmd += f" {compose_service}"
                proc = await asyncio.create_subprocess_shell(
                    pull_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate(timeout=300)
                output = (stdout + stderr).decode(errors="ignore")
                if proc.returncode != 0:
                    result["adaptations"].append(f"docker-compose pull retornou {proc.returncode}")
                    result["notes"] += f"Pull falhou: {output[:200]}. "

            up_cmd = f"docker-compose -f {compose_file} up -d"
            if compose_service:
                up_cmd += f" {compose_service}"
            if docker_cfg.get("force_recreate", False):
                up_cmd += " --force-recreate"

            proc = await asyncio.create_subprocess_shell(
                up_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(timeout=300)
            output = (stdout + stderr).decode(errors="ignore")

            if proc.returncode != 0:
                raise RuntimeError(f"docker-compose up falhou: {output[:500]}")

            if docker_cfg.get("remove_old", False):
                asyncio.create_task(self._cleanup_old_docker_images(old_image, result))

            import re
            match = re.search(r"(\\d+\\.\\d+\\.\\d+)", output)
            return match.group(1) if match else None

        elif image:
            result["notes"] += "Usando docker run direto. "
            if docker_cfg.get("pull_image", True):
                proc = await asyncio.create_subprocess_shell(
                    f"docker pull {image}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate(timeout=300)
                if proc.returncode != 0:
                    raise RuntimeError(f"docker pull falhou: {(stdout+stderr).decode(errors='ignore')[:500]}")

            if container:
                await asyncio.create_subprocess_shell(
                    f"docker rm -f {container} 2>/dev/null",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )

            proc = await asyncio.create_subprocess_shell(
                f"docker run -d --name {container} {image}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(timeout=60)
            if proc.returncode != 0:
                raise RuntimeError(f"docker run falhou: {(stdout+stderr).decode(errors='ignore')[:500]}")

            import re
            match = re.search(r"(\\d+\\.\\d+\\.\\d+)", image)
            return match.group(1) if match else image.split(":")[-1] if ":" in image else None

        result["notes"] += "Nenhuma config Docker encontrada. "
        return None

    async def _cleanup_old_docker_images(self, old_image: str, result: dict):
        if not old_image:
            return
        try:
            proc = await asyncio.create_subprocess_shell(
                f"docker image prune -f --filter 'reference={old_image}' 2>/dev/null || true",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate(timeout=30)
        except Exception:
            pass

    async def _update_by_command(self, config: dict, result: dict) -> str | None:
        cmd = config.get("update_command")
        if not cmd:
            return None
        logger.info(f"Executando comando de update: {cmd}")
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(timeout=300)
        output = (stdout + stderr).decode(errors="ignore")
        if proc.returncode != 0:
            result["adaptations"].append(f"Update command return code: {proc.returncode}")
            raise RuntimeError(f"Comando falhou (código {proc.returncode}): {output[:500]}")
        import re
        match = re.search(config.get("version_regex", r"v?(\d+\.\d+\.\d+)"), output)
        return match.group(1) if match else None

    async def _update_git_pull(self, config: dict, result: dict) -> str | None:
        path = config.get("repo_path")
        if not path or not Path(path).exists():
            result["notes"] += "Git repo path inválido. "
            return None
        logger.info(f"Executando git pull em {path}")
        proc = await asyncio.create_subprocess_shell(
            "git pull",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(timeout=120)
        output = (stdout + stderr).decode(errors="ignore")
        if proc.returncode != 0:
            result["adaptations"].append(f"Git pull retornou código {proc.returncode}: {output[:300]}")
            result["notes"] += "Git pull teve conflitos. "
        import re
        match = re.search(r"v?(\d+\.\d+\.\d+)", output)
        return match.group(1) if match else None

    async def _update_package_manager(self, config: dict, result: dict) -> str | None:
        method = config.get("method", "pip")
        package = config.get("package_name")
        if not package:
            result["notes"] += "package_name não definido. "
            return None
        if method == "pip":
            cmd = f"pip install --upgrade {package}"
        elif method == "npm":
            cmd = f"npm update {package}"
        else:
            return None
        logger.info(f"Executando: {cmd}")
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(timeout=300)
        output = (stdout + stderr).decode(errors="ignore")
        if proc.returncode != 0:
            result["adaptations"].append(f"Package manager falhou (código {proc.returncode})")
            raise RuntimeError(f"Package manager falhou: {output[:500]}")
        import re
        match = re.search(r"v?(\d+\.\d+\.\d+)", output)
        return match.group(1) if match else None

    async def _validate(self, skill: dict, result: dict) -> bool:
        validation = skill.get("validation", {})
        commands = validation.get("commands", [])
        if not commands:
            return True
        all_ok = True
        for cmd in commands:
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate(timeout=30)
                if proc.returncode != 0:
                    result["adaptations"].append(f"Validação falhou: '{cmd}' retornou {proc.returncode}")
                    result["notes"] += f"Validação '{cmd}' falhou. "
                    all_ok = False
            except Exception as e:
                result["adaptations"].append(f"Exceção na validação '{cmd}': {e}")
                result["notes"] += f"Exceção validação '{cmd}': {e}. "
                all_ok = False
        return all_ok
