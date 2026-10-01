"""Text-only TypeSafe Choice batches through the common measured boundary."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.base import EventSink, MeasuredModelClient, ProviderReply, TokenUsage
from models.pricing import PriceRate


class ChoiceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["choice"] = "choice"
    instructions: str | dict
    criteria: dict[str, str | dict | None] = Field(min_length=1, max_length=255)


class ChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: dict
    questions: dict[str, ChoiceQuestion] = Field(min_length=1)

    def batches(self, max_bytes: int = 48_000) -> list[ChoiceRequest]:
        """Pack independent questions without another capture or dependent inference.

        UTF-8 bytes are a conservative local token proxy, not provider billing.
        Keep state below the separate state+question context limit as well.
        """
        if len(json.dumps(self.state, ensure_ascii=False).encode("utf-8")) > 24_000:
            raise ValueError("jev_state_requires_compaction")
        result, pending = [], {}
        for key, question in self.questions.items():
            candidate = ChoiceRequest(state=self.state, questions={**pending, key: question})
            if len(candidate.model_dump_json().encode("utf-8")) > max_bytes:
                if pending:
                    result.append(ChoiceRequest(state=self.state, questions=pending))
                pending = {key: question}
                single = ChoiceRequest(state=self.state, questions=pending)
                if len(single.model_dump_json().encode("utf-8")) > max_bytes:
                    raise ValueError("jev_question_requires_compaction")
            else:
                pending[key] = question
        if pending:
            result.append(ChoiceRequest(state=self.state, questions=pending))
        return result


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def distribution(self):
        if not self.probabilities or any(
            not 0 <= value <= 1 for value in self.probabilities.values()
        ):
            raise ValueError("invalid_choice_distribution")
        # The live API rounds probabilities (observed sums of 0.99).
        # Preserve those reported values; do not reject or renormalize them.
        if self.choice not in self.probabilities or self.probabilities[self.choice] < max(
            self.probabilities.values()
        ):
            raise ValueError("choice_is_not_maximum")
        return self


class ChoiceAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, ChoiceAnswer]

    def validate_request(self, request: ChoiceRequest) -> None:
        if self.answers.keys() != request.questions.keys():
            raise ValueError("jev_question_set_mismatch")
        for key, answer in self.answers.items():
            if answer.probabilities.keys() != request.questions[key].criteria.keys():
                raise ValueError("jev_option_set_mismatch")


class JevClient(MeasuredModelClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        price: PriceRate,
        run_log: EventSink,
        timeout_seconds: float = 60,
    ) -> None:
        super().__init__(provider="jev", model=model, run_log=run_log)
        self._api_key, self._price, self._timeout = api_key, price, timeout_seconds
        self.last_response: dict | None = None

    def _generate(
        self, prompt: str, schema: type[BaseModel], *, images: list[Path] | None
    ) -> ProviderReply:
        self.last_response = None
        if images or schema is not ChoiceAnswers:
            raise ValueError("jev_requires_text_choice_batch")
        batch = ChoiceRequest.model_validate_json(prompt)
        body = {"model": self.model, **batch.model_dump()}
        request = Request(
            "https://api.typesafe.ai/v1/systemone",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # No SDK retries: one measured call represents exactly one HTTP attempt.
        try:
            with urlopen(request, timeout=self._timeout) as response:
                data = json.load(response)
        except HTTPError as error:
            # Private diagnostic artifact only, never headers/key or public event payload.
            self.last_response = {
                "http_status": error.code,
                "error_body": error.read(65_536).decode("utf-8", errors="replace"),
            }
            raise
        self.last_response = data
        metadata = data.get("usage") or {}
        usage = None
        if metadata.get("input_tokens") is not None and metadata.get("output_tokens") is not None:
            usage = TokenUsage(
                input_tokens=metadata["input_tokens"], response_tokens=metadata["output_tokens"]
            )
        # Parse answers only in the measured wrapper, after usage/cost are retained.
        return ProviderReply(text=json.dumps({"answers": data.get("answers")}), usage=usage)

    def _cost(self, usage: TokenUsage) -> Decimal:
        return self._price.cost(usage)

    def _failure_metadata(self, error: BaseException) -> dict[str, str | int]:
        return {"http_status": error.code} if isinstance(error, HTTPError) else {}
