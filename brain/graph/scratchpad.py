"""Anthropic 스타일의 3계층 구조화 스크래치패드 및 롤링 턴 버퍼 관리 모듈."""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RollingTurn(BaseModel):
    """최근 N턴 중 한 턴의 액션과 관찰된 화면 델타(변화)만 담는 모델."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_index: int = Field(ge=0, description="에이전트 실행 누적 스텝 번호")
    intent: str = Field(description="수행한 행동의 의도 (fill, navigate, search 등)")
    target_summary: str = Field(
        max_length=120, description="조작 대상 요약 (예: 'set_text:hbl_no')"
    )
    action_type: str = Field(description="실제 디스패치된 액션 타입 (click, set_text 등)")
    observed_effect: str = Field(max_length=120, description="액션 직후 관찰된 효과 요약")
    success: bool = Field(description="화면 변화나 값 검증 성공 여부")
    checkpoint: str = Field(default="", description="액션 수행 당시의 정책 체크포인트")


class StructuredScratchpad(BaseModel):
    """Layer 1: 에이전트의 현재 비즈니스 진행 상태를 압축한 영구 텍스트 메모."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completed_milestones: list[str] = Field(
        default_factory=list,
        description="완료된 상위 마일스톤 (턴 버퍼에서 밀려나도 영구 보존됨)",
    )
    current_goal: str = Field(
        default="", max_length=200, description="현재 집중하고 있는 상위 목표"
    )
    verified_inputs: list[str] = Field(
        default_factory=list,
        description="현재까지 화면 상에서 검증 완료된 입력 필드 목록",
    )
    remaining_inputs: list[str] = Field(
        default_factory=list,
        description="아직 화면에 입력/검증되지 않은 잔여 입력 필드 목록",
    )


class RollingContextManager:
    """최근 N턴의 시각적 델타 윈도우와 압축 스크래치패드를 유지하는 컨텍스트 매니저."""

    def __init__(self, buffer_size: int = 3):
        # buffer_size 개수를 초과하면 가장 오래된 턴이 자동으로 밀려남(FIFO)
        self.buffer_size = buffer_size
        self._turns: deque[RollingTurn] = deque(maxlen=buffer_size)
        self._completed_milestones: list[str] = []

    @property
    def recent_turns(self) -> list[RollingTurn]:
        """최근 N턴의 실행 기록 반환."""
        return list(self._turns)

    @property
    def completed_milestones(self) -> list[str]:
        return list(self._completed_milestones)

    def record_turn(
        self,
        *,
        turn_index: int,
        intent: str,
        target_summary: str,
        action_type: str,
        observed_effect: str,
        success: bool,
        checkpoint: str = "",
    ) -> None:
        """한 턴의 실행 결과를 기록하며, 버퍼 크기를 초과한 오래된 턴은 자동 제거."""
        turn = RollingTurn(
            turn_index=turn_index,
            intent=intent,
            target_summary=target_summary[:120],
            action_type=action_type,
            observed_effect=observed_effect[:120],
            success=success,
            checkpoint=checkpoint,
        )
        self._turns.append(turn)

    def mark_milestone(self, description: str) -> None:
        """상위 마일스톤을 기록하여 과거 턴이 버퍼에서 지워져도 진행 상태를 보존."""
        if description and description not in self._completed_milestones:
            self._completed_milestones.append(description[:120])

    def build_scratchpad(
        self,
        *,
        current_goal: str,
        verified_inputs: list[str],
        remaining_inputs: list[str],
    ) -> StructuredScratchpad:
        """현재 에이전트 상태를 기반으로 Layer 1 스크래치패드 생성."""
        return StructuredScratchpad(
            completed_milestones=list(self._completed_milestones),
            current_goal=current_goal[:200],
            verified_inputs=sorted(verified_inputs),
            remaining_inputs=sorted(remaining_inputs),
        )

    def export_prompt_context(
        self,
        *,
        current_goal: str,
        verified_inputs: list[str],
        remaining_inputs: list[str],
    ) -> dict[str, Any]:
        """LLM 프롬프트에 주입할 Layer 1(스크래치패드) + Layer 2(최근 3턴 버퍼) 직렬화."""
        scratchpad = self.build_scratchpad(
            current_goal=current_goal,
            verified_inputs=verified_inputs,
            remaining_inputs=remaining_inputs,
        )
        return {
            "scratchpad": scratchpad.model_dump(mode="json"),
            "recent_turns_buffer": [turn.model_dump(mode="json") for turn in self.recent_turns],
        }
