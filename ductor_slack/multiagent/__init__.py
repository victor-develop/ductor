"""Multi-agent architecture: supervisor, bus, and inter-agent communication."""

from ductor_slack.multiagent.bus import InterAgentBus
from ductor_slack.multiagent.health import AgentHealth
from ductor_slack.multiagent.models import SubAgentConfig
from ductor_slack.multiagent.supervisor import AgentSupervisor

__all__ = ["AgentHealth", "AgentSupervisor", "InterAgentBus", "SubAgentConfig"]
