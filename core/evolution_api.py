import logging

import httpx

logger = logging.getLogger(__name__)


class EvolutionAPI:
    def __init__(self, base_url: str, api_key: str, instance: str, group_jid: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.instance = instance
        self.group_jid = group_jid
        self.headers = {
            "Content-Type": "application/json",
            "apiKey": self.api_key,
        }

    async def send_text(self, text: str, jid: str = "") -> bool:
        target = jid or self.group_jid
        if not target:
            logger.warning("EvolutionAPI: nenhum JID de destino")
            return False

        payload = {
            "number": target,
            "text": text,
            "options": {"delay": 0, "linkPreview": False},
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/message/sendText/{self.instance}",
                    json=payload,
                    headers=self.headers,
                )
                if resp.status_code in (200, 201):
                    return True
                logger.error(f"EvolutionAPI erro {resp.status_code}: {resp.text[:200]}")
                return False
        except httpx.ConnectError:
            logger.error(f"EvolutionAPI: conexão recusada em {self.base_url}")
            return False
        except Exception as e:
            logger.error(f"EvolutionAPI: exceção {e}")
            return False

    async def send_group_text(self, text: str) -> bool:
        return await self.send_text(text, self.group_jid)

    @staticmethod
    def parse_webhook(body: dict) -> dict | None:
        try:
            data = body.get("data", {})
            key = data.get("key", {})
            message = data.get("message", {})

            remote_jid = key.get("remoteJid", "")
            from_me = key.get("fromMe", False)
            msg_text = (
                message.get("conversation", "")
                or message.get("extendedTextMessage", {}).get("text", "")
                or ""
            )

            if from_me or not msg_text:
                return None

            is_group = remote_jid.endswith("@g.us")
            sender = key.get("participant", remote_jid) if is_group else remote_jid

            return {
                "jid": remote_jid,
                "sender": sender,
                "text": msg_text.strip(),
                "is_group": is_group,
            }
        except Exception as e:
            logger.error(f"EvolutionAPI: erro ao parsear webhook {e}")
            return None

    @staticmethod
    def extract_command(text: str) -> tuple[str, list[str], str] | None:
        if not text.startswith("/"):
            return None
        parts = text[1:].split()
        if not parts:
            return None
        raw_cmd = parts[0].lower()
        args = parts[1:]
        target_server = ""

        if "@" in raw_cmd:
            cmd_parts = raw_cmd.split("@", 1)
            raw_cmd = cmd_parts[0]
            target_server = cmd_parts[1]

        clean_args = []
        for a in args:
            if a.startswith("@"):
                target_server = a[1:]
            elif "@" in a:
                name_parts = a.split("@", 1)
                clean_args.append(name_parts[0])
                target_server = name_parts[1]
            else:
                clean_args.append(a)

        return raw_cmd, clean_args, target_server
