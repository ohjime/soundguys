"""Best-effort invalidations for the authoritative player HTTP endpoints.

Normal model saves and collection operations are wired up in player_signals.
QuerySet.update(), bulk_create(), bulk_update(), and direct SQL bypass Django
signals: callers that use them must notify_players_changed() explicitly.
Notifications aren't a durable log; clients reconcile after reconnecting and
periodically so a Redis outage never prevents a database write.
"""

import asyncio
import logging
from functools import partial

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


def player_group_name(player_id):
    return f"player.{player_id}"


async def _send_player_changes(player_ids):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        raise RuntimeError("No channel layer configured for player notifications")

    # Bound the whole batch, including connection establishment. A unavailable
    # Redis service must not hold up admin or prediction writes indefinitely.
    timeout = getattr(settings, "PLAYER_EVENT_PUBLISH_TIMEOUT", 2.0)
    async with asyncio.timeout(timeout):
        await asyncio.gather(
            *(
                channel_layer.group_send(
                    player_group_name(player_id),
                    {"type": "player.changed", "schema_version": 1},
                )
                for player_id in player_ids
            )
        )


def publish_player_changes(player_ids):
    """Publish committed changes without propagating messaging failures."""
    try:
        async_to_sync(_send_player_changes)(player_ids)
    except Exception:
        logger.warning("Could not publish player change notifications", exc_info=True)


def notify_players_changed(player_ids, *, using=None):
    """Capture recipient IDs now; publish only if their transaction commits."""
    player_ids = tuple(sorted({pk for pk in player_ids if pk is not None}))
    if player_ids:
        transaction.on_commit(
            partial(publish_player_changes, player_ids), using=using
        )
