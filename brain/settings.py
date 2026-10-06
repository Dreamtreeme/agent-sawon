"""사설 에이전트의 런타임 제약 및 설정 모델 정의."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from brain.visual.detector import DetectorSettings
from brain.visual.markers import MergeSettings


class CacheSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    path: Path = Path("cases/semantic-cache.json")
    max_retries: int = Field(default=1, ge=0, le=3)
    matching_mode: Literal["exact", "rebind"] = "exact"
    min_anchor_fields: int = Field(default=2, ge=2, le=11)
    rebind_model_max_calls: int = Field(default=1, ge=0, le=3)


class VisualMatchCacheSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    path: Path = Path("cases/visual-match-cache.json")


class MethodSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    path: Path = Path("cases/methods.json")
    promotion_path: Path | None = None


class BrainSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    summary_strategy: Literal["flat_fields", "label_grouped"] = "label_grouped"
    max_steps: int = Field(default=80, ge=1, le=500)
    max_subgoal_failures: int = Field(default=2, ge=1, le=10)
    max_input_target_retries: int = Field(default=1, ge=0, le=3)
    max_cost_usd: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    model_call_reserve_usd: float = Field(default=0.025, ge=0, allow_inf_nan=False)
    http_timeout_seconds: float = Field(default=30, gt=0, le=120)
    observation_max_depth: int = Field(default=12, ge=1, le=30)
    max_summary_elements: int = Field(default=500, ge=20, le=2000)
    navigation_history_size: int = Field(default=12, ge=4, le=64)

    # Anthropic식 3계층 컨텍스트 축소 옵션
    visual_rolling_buffer_size: int = Field(
        default=3,
        ge=1,
        le=8,
        description="최근 턴 델타 유지 개수 (이전 턴의 마커/스크린샷은 프롬프트에서 자동 드롭)",
    )
    max_feedback_history: int = Field(
        default=12,
        ge=1,
        le=24,
        description="프롬프트 주입용 최대 에러 피드백 개수 (기존 12개 )",
    )

    cache: CacheSettings = Field(default_factory=CacheSettings)
    visual_match_cache: VisualMatchCacheSettings = Field(default_factory=VisualMatchCacheSettings)
    methods: MethodSettings = Field(default_factory=MethodSettings)
    recovery_enabled: bool = False

    # [수정] 'hybrid' 모드 추가 (시각 좌표 클릭 메인 + 최소 OS 신호 보조)
    perception_mode: Literal["legacy_uia", "visual_only", "hybrid"] = "legacy_uia"
    hybrid_assist_focus: bool = Field(
        default=True, description="창 포커스 상실 시 OS 레벨 포커스 자동 복원 보조"
    )
    hybrid_assist_ime: bool = Field(
        default=True, description="영문/숫자 입력 시 OS 한/영 IME 꼬임 방지 보조"
    )
    hybrid_assist_scroll: bool = Field(
        default=True, description="마우스 휠 스크롤 시 OS 스크롤바 메타데이터 보조"
    )

    visual_min_confidence: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    visual_input_blur_recheck: bool = True
    visual_click_refinement: bool = True
    visual_screen_fill: bool = False
    visual_model_led_semantics: bool = False
    visual_model_only_review: bool = False
    visual_action_phash_distance: int = Field(default=16, ge=0, le=64)
    screen_matching: bool = False
    separate_observation_decision: bool = True
    jev_request_max_bytes: int = Field(default=48_000, ge=8_000, le=48_000)
    task_path: Path | None = None
    procedure_path: Path | None = None
    visual_detector: DetectorSettings = Field(default_factory=DetectorSettings)
    visual_merge: MergeSettings = Field(default_factory=MergeSettings)

    @property
    def is_visual(self) -> bool:
        """순수 비전 또는 하이브리드 시각 모드 활성화 여부."""
        return self.perception_mode in {"visual_only", "hybrid"}
    @classmethod
    def load(cls, path: Path) -> "BrainSettings":
        with path.open("rb") as stream:
            settings = cls.model_validate(tomllib.load(stream))

        # 설정 파일 위치 기준 상대 경로들을 절대 경로로 변환
        if settings.procedure_path is not None and not settings.procedure_path.is_absolute():
            settings = settings.model_copy(
                update={"procedure_path": (path.parent / settings.procedure_path).resolve()}
            )
        if settings.task_path is not None and not settings.task_path.is_absolute():
            settings = settings.model_copy(
                update={"task_path": (path.parent / settings.task_path).resolve()}
            )

        # 검출기 모델 가중치 경로 보정 (weights 및 caption_weights)
        detector_updates = {}
        if not settings.visual_detector.weights.is_absolute():
            detector_updates["weights"] = (path.parent / settings.visual_detector.weights).resolve()
        if (
            settings.visual_detector.caption_weights is not None
            and not settings.visual_detector.caption_weights.is_absolute()
        ):
            detector_updates["caption_weights"] = (
                path.parent / settings.visual_detector.caption_weights
            ).resolve()

        if detector_updates:
            settings = settings.model_copy(
                update={
                    "visual_detector": settings.visual_detector.model_copy(update=detector_updates)
                }
            )

        if not settings.cache.path.is_absolute():
            settings = settings.model_copy(
                update={
                    "cache": settings.cache.model_copy(
                        update={"path": (path.parent / settings.cache.path).resolve()}
                    )
                }
            )
        if not settings.visual_match_cache.path.is_absolute():
            settings = settings.model_copy(
                update={
                    "visual_match_cache": settings.visual_match_cache.model_copy(
                        update={"path": (path.parent / settings.visual_match_cache.path).resolve()}
                    )
                }
            )
        if not settings.methods.path.is_absolute():
            settings = settings.model_copy(
                update={
                    "methods": settings.methods.model_copy(
                        update={
                            "path": (path.parent / settings.methods.path).resolve(),
                            "promotion_path": (
                                (path.parent / settings.methods.promotion_path).resolve()
                                if settings.methods.promotion_path is not None
                                and not settings.methods.promotion_path.is_absolute()
                                else settings.methods.promotion_path
                            ),
                        }
                    )
                }
            )
        elif (
            settings.methods.promotion_path is not None
            and not settings.methods.promotion_path.is_absolute()
        ):
            settings = settings.model_copy(
                update={
                    "methods": settings.methods.model_copy(
                        update={
                            "promotion_path": (
                                path.parent / settings.methods.promotion_path
                            ).resolve()
                        }
                    )
                }
            )
        return settings