"""批量箱号校验 API 的 Pydantic 模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
