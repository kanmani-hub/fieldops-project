"""
sentiment_service.py

Sentiment Service for FieldOps Commander.

Responsibilities
----------------
- Analyze customer messages.
- Analyze technician messages.
- Detect customer satisfaction.
- Detect urgency or frustration.

This service NEVER:
- Updates database.
- Sends notifications.
- Makes assignment decisions.

It only returns AI sentiment analysis.
"""

from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator


class SentimentService:
    """
    AI-powered sentiment analysis service.
    """

    def __init__(self):
        self.orchestrator = AIOrchestrator()

    # ---------------------------------------------------------

    def analyze_customer_message(
        self,
        message: str,
        language: str = "en",
    ):
        """
        Analyze customer sentiment.

        Returns:
            positive
            neutral
            negative
        """

        context = {
            "text": message,
            "language": language,
        }

        return self.orchestrator.execute(
            task="sentiment",
            context=context,
        )

    # ---------------------------------------------------------

    def analyze_technician_message(
        self,
        message: str,
        language: str = "en",
    ):
        """
        Analyze technician sentiment.
        """

        context = {
            "text": message,
            "language": language,
        }

        return self.orchestrator.execute(
            task="sentiment",
            context=context,
        )