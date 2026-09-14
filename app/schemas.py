"""批量箱号校验 API 的 Pydantic 模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.checksum import CONTAINER_LENGTH

MIN_BATCH = 1
MAX_BATCH = 100


class VerifyRequest(BaseModel):
    """一次校验请求：1..100 个箱号，原样传递，不做任何字符串归一化。"""

    # 禁止额外字段，使“字段拼错/结构不对”的请求立即暴露。
    model_config = ConfigDict(extra="forbid")

    container_numbers: list[str] = Field(
        ...,
        min_length=MIN_BATCH,
        max_length=MAX_BATCH,
        description=(
            "待校验箱号列表（含端点 1 至 100 个）。字符串原样使用，"
            "不进行大小写或空白归一化。"
        ),
    )


class ContainerPartsOut(BaseModel):
    """箱号拆分字段。"""

    owner_code: str = Field(..., description="前 3 位箱主代码（A-Z）")
    category_identifier: str = Field(
        ..., description="第 4 位设备类别码（仅 U/J/Z）"
    )
    serial_number: str = Field(..., description="第 5-10 位顺序号（6 位数字）")
    check_digit: str = Field(..., description="第 11 位实际校验数字（原样字符）")


class ContainerResult(BaseModel):
    """单只箱体的可复算结论。"""

    index: int = Field(..., description="在请求数组中的索引（从 0 起）")
    container_number: str = Field(..., description="原样回显的输入箱号")
    parts: ContainerPartsOut
    weighted_sum: int = Field(
        ..., description="前 10 位按 2**0..2**9 加权得到的总和"
    )
    expected_check_digit: int = Field(
        ..., description="按加权和对 11 取余得到的期望校验位"
    )
    actual_check_digit: int = Field(..., description="箱号末位携带的实际校验位")
    passed: bool = Field(
        ..., description="期望校验位与实际校验位是否一致"
    )


class VerifyResponse(BaseModel):
    """结构全部合法时的逐项可复算结果。"""

    status: Literal["ok"] = "ok"
    count: int = Field(..., description="本批箱号数量")
    passed_count: int
    failed_count: int
    results: list[ContainerResult]


class InvalidBatchResponse(BaseModel):
    """批内存在结构非法项：整批拒绝并指出最小输入索引。"""

    status: Literal["invalid_batch"] = "invalid_batch"
    count: int = Field(..., description="本批提交的箱号数量")
    index: int = Field(
        ..., description="首个结构非法项在请求数组中的索引（从 0 起）"
    )
    container_number: str = Field(..., description="该非法项的原样输入")
    error_code: str
    position: int = Field(
        ...,
        description=(
            f"箱号内首个损坏字符的位置（从 1 起，共 {CONTAINER_LENGTH} 位）；"
            "长度错误时为越界位置"
        ),
    )
    message: str


class CorrectRequest(BaseModel):
    """单箱纠错请求：一个恰为 11 位的箱号，原样使用，不做任何归一化。"""

    model_config = ConfigDict(extra="forbid")

    # 不用 min_length/max_length 约束：pydantic-core 对带约束的字符串
    # 会先做 Unicode 标量转换，把含未配对代理字符（如首位为 \ud800 的
    # 箱号）的合法长度输入误判为请求形状错误（string_unicode），使其
    # 永远无法进入领域层生成纠错候选。恰为 11 位的检查改由下方的
    # Python 校验器完成，错误形态仍是请求校验 422（detail 数组）。
    container_number: str = Field(
        ...,
        description=(
            f"待纠错箱号，必须恰为 {CONTAINER_LENGTH} 个字符。"
            "字符串原样使用，不进行大小写或空白归一化。"
        ),
    )

    @field_validator("container_number")
    @classmethod
    def _exactly_eleven_characters(cls, value: str) -> str:
        if len(value) != CONTAINER_LENGTH:
            raise ValueError(
                f"container number must be exactly {CONTAINER_LENGTH} "
                f"characters, got {len(value)}"
            )
        return value


class CorrectionCandidateOut(BaseModel):
    """一个单字符纠错候选。"""

    position: int = Field(..., description="差异字符位置（从 1 起）")
    original_character: str = Field(..., description="原号在该位置的字符")
    replacement_character: str = Field(..., description="候选号在该位置的字符")
    container_number: str = Field(
        ..., description="完整候选箱号（结构合法且校验位通过）"
    )


class CorrectResponse(BaseModel):
    """单箱纠错结论：唯一候选、多个候选或未找到。"""

    status: Literal["unique", "multiple", "not_found"] = Field(
        ...,
        description=(
            "unique=唯一候选；multiple=多个候选；not_found=未找到候选"
        ),
    )
    container_number: str = Field(..., description="原样回显的输入箱号")
    candidate_count: int = Field(..., description="候选数量")
    candidates: list[CorrectionCandidateOut] = Field(
        ..., description="按（差异位置, 替换字符）稳定排序的候选列表"
    )


class ExplainRequest(BaseModel):
    """单箱计算明细请求：一个箱号，原样使用，不做任何归一化。

    不限制字符串长度：长度等结构问题由业务层按首个损坏位置拒绝，
    与批量校验的结构错误口径一致；此处只保证字段形状正确。
    """

    model_config = ConfigDict(extra="forbid")

    container_number: str = Field(
        ...,
        description=(
            "待复算明细的箱号。字符串原样使用，"
            "不进行大小写或空白归一化。"
        ),
    )


class ChecksumStepOut(BaseModel):
    """前 10 位中单个字符的加权计算步骤。"""

    position: int = Field(..., description="字符在箱号中的位置（从 1 起）")
    character: str = Field(..., description="该位置的原样字符")
    value: int = Field(
        ..., description="字符映射值（数字取原值，字母按跳号表取值）"
    )
    weight: int = Field(..., description="二次幂权重 2**(position-1)")
    product: int = Field(..., description="映射值与权重的乘积")


class ExplainResponse(BaseModel):
    """单箱逐字符计算明细与汇总结论（汇总由步骤求和派生）。"""

    status: Literal["ok"] = "ok"
    container_number: str = Field(..., description="原样回显的输入箱号")
    steps: list[ChecksumStepOut] = Field(
        ..., description="前 10 位按原位置顺序的计算步骤（恰 10 项）"
    )
    weighted_sum: int = Field(..., description="十项乘积的合计")
    remainder: int = Field(
        ..., description="合计对 11 取余的原始余数（0..10，未折叠）"
    )
    expected_check_digit: int = Field(
        ..., description="期望校验位（余数 10 折叠为 0）"
    )
    actual_check_digit: int = Field(..., description="箱号末位携带的实际校验位")
    passed: bool = Field(
        ..., description="期望校验位与实际校验位是否一致"
    )


class InvalidContainerResponse(BaseModel):
    """单箱结构非法：指出首个损坏位置（与批量结构错误同一来源）。"""

    status: Literal["invalid_container"] = "invalid_container"
    container_number: str = Field(..., description="原样回显的输入箱号")
    error_code: str
    position: int = Field(
        ...,
        description=(
            f"箱号内首个损坏字符的位置（从 1 起，共 {CONTAINER_LENGTH} 位）；"
            "长度错误时为越界位置"
        ),
    )
    message: str
