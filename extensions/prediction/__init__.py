"""Optional Prediction observation bound to the existing Agent registry."""


def register_prediction_observer() -> None:
    from aletheia.agents import register_agent
    from extensions.prediction.observer import run_prediction_observer

    register_agent("prediction_observer")(run_prediction_observer)
