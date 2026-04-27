"""Webhook system: HTTP ingress for external event triggers."""

from ductor_slack.webhook.manager import WebhookManager
from ductor_slack.webhook.models import WebhookEntry, WebhookResult

__all__ = ["WebhookEntry", "WebhookManager", "WebhookResult"]
