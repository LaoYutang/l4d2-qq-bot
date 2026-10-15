"""Real A2S plus independent map/voice enrichment; extensions cannot fake INFO."""

import asyncio
from dataclasses import replace

from ..config import GroupConfig, ServerConfig
from ..domain import ServerStatus
from ..integrations.heybox import HeyboxVoiceClient
from ..integrations.maps import MapNameClient
from .query import QueryService


class QueryCoordinator:
    def __init__(
        self,
        query: QueryService,
        maps: MapNameClient | None = None,
        voice: HeyboxVoiceClient | None = None,
    ):
        self.query, self.maps, self.voice = query, maps, voice

    async def server(
        self, server: ServerConfig, group: GroupConfig, *, players: bool = True
    ) -> ServerStatus:
        async def game():
            status = await self.query.query(
                server.address, alias=server.name, allow_private=True, include_players=players
            )
            return await self.enrich_map(status)

        if self.voice and self.voice.configured(group.hh_room_id, server.hh_channel_id):
            status, voice = await asyncio.gather(
                game(),
                self.voice.query(
                    server.name, group.hh_room_id, server.hh_channel_id, include_names=False
                ),
            )
            return replace(
                status, voice_configured=True, voice_count=voice.count, voice_error=voice.error
            )
        return await game()

    async def enrich_map(self, status: ServerStatus) -> ServerStatus:
        if status.online and self.maps:
            return replace(status, map_name=await self.maps.name(status.map_code))
        return status
