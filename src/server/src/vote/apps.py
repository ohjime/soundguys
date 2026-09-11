from django.apps import AppConfig


class VoteConfig(AppConfig):
    name = 'vote'

    def ready(self):
        from vote import signals  # noqa: F401
