from __future__ import annotations

import datetime as _dt

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# These constants intentionally duplicate the ones in catalog/models.py.
# Both are constraints enforced at separate boundaries (DB and YAML ingest).
VALID_QUANT_BITS = {4, 8, 16}
VALID_TP_SIZES = {1, 2, 4, 8}


class QuantizationYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    weight_bits: float
    kv_cache_bits: float
    requires_nvidia_arch: list[str] = Field(default_factory=list)
    requires_amd_arch: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("weight_bits", "kv_cache_bits")
    @classmethod
    def _bits_must_be_valid(cls, v: float) -> float:
        if v not in VALID_QUANT_BITS:
            raise ValueError(f"bits must be one of {VALID_QUANT_BITS}")
        return v


class GPUYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    vendor: str
    architecture: str
    vram_gb: int
    memory_bandwidth_gbs: int
    fp16_tflops: float
    fp8_tflops: float | None = None
    int8_tops: float | None = None
    tdp_watts: int
    interconnect: str
    nvlink_bandwidth_gbs: int | None = None
    supports_fp8_native: bool = False
    supports_fp4_native: bool = False

    @field_validator("vendor")
    @classmethod
    def _vendor_valid(cls, v: str) -> str:
        if v not in {"nvidia", "amd"}:
            raise ValueError("vendor must be 'nvidia' or 'amd'")
        return v

    @field_validator("tdp_watts")
    @classmethod
    def _tdp_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("tdp_watts must be > 0")
        return v


class ModelYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    family: str
    architecture: str
    total_params_b: float
    active_params_b: float
    num_layers: int
    num_attention_heads: int
    num_kv_heads: int
    head_dim: int
    max_context: int
    hf_repo: str
    license: str
    is_coding_specialist: bool
    recommended_quant: str  # slug reference resolved at load time
    recommended_tp: int

    @field_validator("architecture")
    @classmethod
    def _arch_valid(cls, v: str) -> str:
        if v not in {"dense", "moe"}:
            raise ValueError("architecture must be 'dense' or 'moe'")
        return v

    @field_validator("recommended_tp")
    @classmethod
    def _tp_valid(cls, v: int) -> int:
        if v not in VALID_TP_SIZES:
            raise ValueError(f"recommended_tp must be one of {VALID_TP_SIZES}")
        return v

    @model_validator(mode="after")
    def _dense_total_equals_active(self) -> ModelYAML:
        if self.architecture == "dense" and self.total_params_b != self.active_params_b:
            raise ValueError("dense models must have total_params_b == active_params_b")
        return self


class BenchmarkSourceYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    url: str
    publisher: str
    published_at: _dt.date
    engine: str
    engine_version: str
    notes: str = ""


class BenchmarkPointYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    gpu: str
    quantization: str
    tp_size: int
    batch_size: int
    context_length: int
    prefill_tps_aggregate: float
    decode_tps_aggregate: float
    ttft_ms: float | None = None

    @field_validator("tp_size")
    @classmethod
    def _tp_valid(cls, v: int) -> int:
        if v not in VALID_TP_SIZES:
            raise ValueError(f"tp_size must be one of {VALID_TP_SIZES}")
        return v

    @field_validator("prefill_tps_aggregate", "decode_tps_aggregate")
    @classmethod
    def _tps_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("throughput must be positive")
        return v


class BenchmarkFileYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: BenchmarkSourceYAML
    points: list[BenchmarkPointYAML]
