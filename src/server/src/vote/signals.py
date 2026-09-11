from django.db.models.signals import post_save
from django.dispatch import receiver

from core.player_events import notify_player_vote
from vote.models import Vote


@receiver(post_save, sender=Vote, dispatch_uid="player_events.vote_received")
def vote_received(sender, instance, created, raw, using, **kwargs):
    # Rejected requests never create a vote. Edits and fixture loads aren't taps.
    if created and not raw:
        notify_player_vote(instance.player_id, instance.pk, using=using)
