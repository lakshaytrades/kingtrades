import io
from typing import Optional
def _send(
        self,
        text: str,
        image_buf: Optional[io.BytesIO] = None,
        parse_mode: str = "Markdown",
    ) -> bool:
        if not self._is_ready():
            logger.debug("Telegram alerter not ready — alert skipped")
            return False

        try:
            import asyncio

            async def _do_send():
                if image_buf:
                    image_buf.seek(0)
                    await self._bot.send_photo(
                        chat_id=self.chat_id,
                        photo=image_buf,
                        caption=text[:1024],
                        parse_mode=parse_mode,
                    )
                else:
                    await self._bot.send_message(
                        chat_id=self.chat_id,
                        text=text[:4096],
                        parse_mode=parse_mode,
                    )

            # Best practice for background worker (no assumption about existing loop)
            try:
                # If there's already a running loop (common in some schedulers), use create_task + ensure_future
                loop = asyncio.get_running_loop()
                asyncio.create_task(_do_send())   # Fire-and-forget (non-blocking)
                logger.debug(f"Telegram alert scheduled asynchronously: {text[:100]}...")
                return True
            except RuntimeError:  # No running loop
                # Fallback: run in a new loop (safe in most background workers)
                asyncio.run(_do_send())
                return True

        except Exception as e:
            logger.error(f"[{format_ist_timestamp()}] Telegram send failed: {e}", exc_info=True)
            return False
