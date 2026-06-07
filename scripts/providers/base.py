from abc import ABC, abstractmethod

class AgentProvider(ABC):
    @abstractmethod
    def execute(self, prompt: str, workspace_path: str, model: str) -> str:
        """Executes the agent and returns stdout/stderr response."""
        pass
