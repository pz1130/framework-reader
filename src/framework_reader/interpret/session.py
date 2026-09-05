"""Interview session: testable pure logic, separate from terminal IO. W2 spec §5, §6"""
from collections.abc import Callable

from framework_reader.interpret.extractor import extract_fields
from framework_reader.interpret.lint import unplaced_answers
from framework_reader.interpret.model import (
    Interpretation,
    InterpretationState,
    ModelRef,
    Question,
)
from framework_reader.interpret.questioner import adaptive_question, fixed_questions
from framework_reader.interpret.store import InterpretationStore
from framework_reader.llm.client import LLMClient

TOTAL_QUESTIONS = 3


class InterviewSession:
    def __init__(
        self,
        store: InterpretationStore,
        questioner_client: LLMClient,
        extractor_client: LLMClient,
        *,
        outcome_lookup: Callable[[str], str],
        questioner_model: str,
        extractor_model: str,
        extractor_provider: str,
        extractor_prompt_version: str,
    ) -> None:
        self._store = store
        self._questioner = questioner_client
        self._extractor = extractor_client
        self._outcome_lookup = outcome_lookup
        self._questioner_model = questioner_model
        self._extractor_model = extractor_model
        self._extractor_provider = extractor_provider
        self._extractor_prompt_version = extractor_prompt_version

    def next_question(self, control_id: str) -> Question | None:
        interp = self._store.load(control_id)
        answered = {r.n for r in interp.interview.raw}
        fixed = fixed_questions()

        for question in fixed:
            if question.n not in answered:
                self._remember(interp, question)
                return question

        if TOTAL_QUESTIONS in answered:
            return None

        question = adaptive_question(
            self._questioner,
            control_id=control_id,
            outcome=self._outcome_lookup(control_id),
            answers=interp.interview.raw,
            model=self._questioner_model,
        )
        self._remember(interp, question)
        return question

    def _remember(self, interp: Interpretation, question: Question) -> None:
        kept = [q for q in interp.interview.questions if q.n != question.n]
        kept.append(question)
        interp.interview.questions = sorted(kept, key=lambda q: q.n)
        self._store.save(interp)

    def record(self, control_id: str, n: int, text: str) -> None:
        """Persist immediately after each answer. W2 spec §6"""
        self._store.append_raw(control_id, n, text)

    def finish(self, control_id: str, *, force: bool = False) -> Interpretation:
        interp = self._store.load(control_id)
        if len(interp.interview.raw) < TOTAL_QUESTIONS:
            raise ValueError(
                f"{control_id} does not have all three questions answered yet (currently {len(interp.interview.raw)})"
            )
        if (
            interp.state in (InterpretationState.INTERVIEWED, InterpretationState.CONFIRMED)
            and not force
        ):
            raise ValueError(
                f"{control_id} is already {interp.state.value}; pass force=True to re-run extraction"
            )
        fields = extract_fields(
            self._extractor,
            control_id=control_id,
            questions=interp.interview.questions,
            answers=interp.interview.raw,
            model=self._extractor_model,
        )
        interp.fields.update(fields)
        interp.interview.unplaced = unplaced_answers(fields, interp.interview.raw)
        interp.state = InterpretationState.INTERVIEWED
        interp.provenance.extractor = ModelRef(
            provider=self._extractor_provider,
            model=self._extractor_model,
            prompt_version=self._extractor_prompt_version,
        )
        self._store.save(interp)
        return interp
