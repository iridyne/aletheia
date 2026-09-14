"""Optional Learning producers using Core state and AgentRun owners."""


def register_feedback_builder() -> None:
    from aletheia.analytics.reasoning_feedback import configure_feedback_builder
    from extensions.learning.reasoning_feedback import build_reasoning_feedback

    configure_feedback_builder(build_reasoning_feedback)


def register_calibration_builder() -> None:
    from aletheia.analytics.confidence_calibration import configure_calibration_builder
    from extensions.learning.confidence_calibration import build_calibration_report

    configure_calibration_builder(build_calibration_report)


def register_memo_synthesizer() -> None:
    from aletheia.agent_runtime.scheduler import configure_memo_synthesizer
    from extensions.learning.insight_synthesizer import synthesize_trading_memo

    configure_memo_synthesizer(synthesize_trading_memo)


def register_lesson_reviewer() -> None:
    from aletheia.agents import register_agent
    from extensions.learning.lesson_reviewer import run_lesson_reviewer

    register_agent("lesson_reviewer")(run_lesson_reviewer)


def register_dreaming_agent() -> None:
    from aletheia.agents import register_agent
    from aletheia.agent_runtime.scheduler import configure_lesson_extractor
    from extensions.learning.dreaming_agent import _extract_lessons, run_dreaming_agent

    register_agent("dreaming_agent")(run_dreaming_agent)
    configure_lesson_extractor(_extract_lessons)


def register_post_trade_reviewer() -> None:
    from aletheia.agents import register_agent
    from extensions.learning.post_trade_reviewer import run_post_trade_reviewer

    register_agent("post_trade_reviewer")(run_post_trade_reviewer)
