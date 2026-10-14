"""Headless tests for the call-detected "Record this meeting?" prompt."""

from meeting_recorder.config.defaults import DEFAULT_CONFIG
from meeting_recorder.core.state_machine import State
from meeting_recorder.daemon.call_prompt import CallPrompt


class FakeEngine:
    def __init__(self, state=State.IDLE):
        self.state = state
        self.started = []

    def start_recording(self, mode):
        self.started.append(mode)


def _prompt(engine):
    shown = []
    prompt = CallPrompt(engine, lambda *args: shown.append(args), lambda func, *a: func(*a))
    return prompt, shown


def test_idle_offers_record_and_not_now():
    prompt, shown = _prompt(FakeEngine())
    prompt.on_call_detected("audio-stream")
    summary, _body, actions, _cb = shown[0]
    assert summary == "Record this meeting?"
    assert [label for _name, label in actions] == ["Record (Headphones)", "Not now"]


def test_active_engine_stays_silent():
    for state in (State.RECORDING, State.PAUSED, State.COUNTDOWN):
        prompt, shown = _prompt(FakeEngine(state))
        prompt.on_call_detected("audio-stream")
        assert shown == []


def test_record_starts_recording_in_the_default_mode():
    engine = FakeEngine()
    prompt, shown = _prompt(engine)
    prompt.on_call_detected("audio-stream")
    shown[0][3]("record")
    assert engine.started == ["headphones"]


def test_not_now_starts_nothing():
    engine = FakeEngine()
    prompt, shown = _prompt(engine)
    prompt.on_call_detected("audio-stream")
    shown[0][3]("not-now")
    assert engine.started == []


def test_call_detection_is_on_by_default():
    assert DEFAULT_CONFIG["call_detection_enabled"] is True


def test_one_prompt_at_a_time_until_it_ends():
    prompt, shown = _prompt(FakeEngine())
    prompt.on_call_detected("audio-stream")
    prompt.on_call_detected("audio-stream")
    assert len(shown) == 1
    shown[0][3]("")
    prompt.on_call_detected("audio-stream")
    assert len(shown) == 2


def test_the_last_mode_chosen_is_offered_and_started():
    engine = FakeEngine()
    engine.last_mode = "speaker"
    prompt, shown = _prompt(engine)
    prompt.on_call_detected("audio-stream")
    assert shown[0][2][0][1] == "Record (Speaker)"
    shown[0][3]("record")
    assert engine.started == ["speaker"]


def test_a_failing_notifier_does_not_block_later_prompts():
    calls = []

    def notify(*args):
        calls.append(args)
        if len(calls) == 1:
            raise OSError("no bus")

    prompt = CallPrompt(FakeEngine(), notify, lambda func, *a: func(*a))
    try:
        prompt.on_call_detected("audio-stream")
    except OSError:
        pass
    prompt.on_call_detected("audio-stream")
    assert len(calls) == 2
