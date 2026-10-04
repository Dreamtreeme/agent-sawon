"""Configurable limits for the private, relay-only agent."""

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
    cache: CacheSettings = Field(default_factory=CacheSettings)
    visual_match_cache: VisualMatchCacheSettings = Field(default_factory=VisualMatchCacheSettings)
    methods: MethodSettings = Field(default_factory=MethodSettings)
    recovery_enabled: bool = False
    perception_mode: Literal["legacy_uia", "visual_only"] = "legacy_uia"
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

    @classmethod
    def load(cls, path: Path) -> "BrainSettings":
        with path.open("rb") as stream:
            settings = cls.model_validate(tomllib.load(stream))
        if settings.procedure_path is not None and not settings.procedure_path.is_absolute():
            settings = settings.model_copy(
                update={"procedure_path": (path.parent / settings.procedure_path).resolve()}
            )
        if settings.task_path is not None and not settings.task_path.is_absolute():
            settings = settings.model_copy(
                update={"task_path": (path.parent / settings.task_path).resolve()}
            )
        if not settings.visual_detector.weights.is_absolute():
            settings = settings.model_copy(
                update={
                    "visual_detector": settings.visual_detector.model_copy(
                        update={
                            "weights": (path.parent / settings.visual_detector.weights).resolve()
                        }
                    )
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
