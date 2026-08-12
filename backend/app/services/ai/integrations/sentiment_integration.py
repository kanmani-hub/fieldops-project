"""
sentiment_integration.py

Integration layer between the backend
communication workflow and the AI Sentiment Agent.
"""

from app.services.ai.FieldOpsAI.agents.sentiment_agent import SentimentAgent
from app.services.ai.FieldOpsAI.schemas.sentiment import SentimentDecision


class SentimentIntegration:
    """
    Adapter for customer sentiment analysis.
    """

    def __init__(self):
        self.agent = SentimentAgent()

    def analyze(
        self,
        message: str,
        channel: str,
    ) -> SentimentDecision:
        """
        Analyze customer sentiment.
        """

        return self.agent.analyze(
            message=message,
            channel=channel,
        )