"""批量箱号校验 API 的 Pydantic 模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

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
    # 严格布尔：只接受 JSON true/false；"true"、1 等类型错误的值一律
    # 进入 Pydantic 请求校验 422（detail 数组），不做宽松转换。
    include_owner_summary: StrictBool = Field(
        default=False,
        description=(
            "可选箱主汇总开关。为 true 时响应附带 owner_summary：按箱主"
            "首次出现顺序给出每组总数、通过数与未通过数；省略或为 false "
            "时响应与不携带该开关的旧版请求逐字段一致。"
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


class OwnerSummaryOut(BaseModel):
    """单个箱主的班组统计。"""

    owner_code: str = Field(..., description="箱主代码（前 3 位大写字母）")
    total: int = Field(..., description="该箱主在本批中的箱数")
    passed: int = Field(..., description="该箱主校验通过的箱数")
    failed: int = Field(..., description="该箱主校验未通过的箱数")


class VerifyResponse(BaseModel):
    """结构全部合法时的逐项可复算结果。"""

    status: Literal["ok"] = "ok"
    count: int = Field(..., description="本批箱号数量")
    passed_count: int
    failed_count: int
    results: list[ContainerResult]
    # 仅当请求开启箱主汇总开关时赋值；为 None 时由路由的
    # response_model_exclude_none 剔除，旧客户端看不到该字段。
    owner_summary: list[OwnerSummaryOut] | None = Field(
        default=None,
        description=(
            "按箱主首次出现顺序的班组统计（重复箱主只一项）；"
            "仅当请求开启 include_owner_summary 时携带。"
        ),
    )


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


# ------------------------------------------------------------- 一次性清单核对


class ReconcileRequest(BaseModel):
    """一次性清单核对请求：当班作业清单（预期）与现场扫描清单。

    两份清单均为 1..100 个箱号，原样传递，不做任何字符串归一化；清单
    内部顺序即输入顺序，领域层按此顺序配对并保持结果顺序。
    """

    model_config = ConfigDict(extra="forbid")

    expected_container_numbers: list[str] = Field(
        ...,
        min_length=MIN_BATCH,
        max_length=MAX_BATCH,
        description=(
            "当班作业清单（预期箱号列表，含端点 1 至 100 个）。字符串原样"
            "使用，不进行大小写或空白归一化；顺序即原索引顺序。"
        ),
    )
    onsite_container_numbers: list[str] = Field(
        ...,
        min_length=MIN_BATCH,
        max_length=MAX_BATCH,
        description=(
            "现场扫描清单（实际扫到的箱号列表，含端点 1 至 100 个）。字符串"
            "原样使用，不进行大小写或空白归一化；顺序即原索引顺序。"
        ),
    )


class MatchedContainerOut(BaseModel):
    """一对成功配对的箱号及其在两份清单中的原索引。"""

    container_number: str = Field(..., description="完整箱号（两侧一致）")
    expected_index: int = Field(
        ..., description="在预期清单中的原索引（从 0 起）"
    )
    onsite_index: int = Field(
        ..., description="在现场清单中的原索引（从 0 起）"
    )


class MissingContainerOut(BaseModel):
    """预期清单中存在、现场缺少对应次数的箱号。"""

    container_number: str = Field(..., description="完整箱号")
    expected_index: int = Field(
        ..., description="在预期清单中的原索引（从 0 起）"
    )


class ExtraContainerOut(BaseModel):
    """现场清单中存在、预期清单没有对应次数的箱号。"""

    container_number: str = Field(..., description="完整箱号")
    onsite_index: int = Field(
        ..., description="在现场清单中的原索引（从 0 起）"
    )


class ReconcileResponse(BaseModel):
    """两份清单全部结构合法且校验通过时的配对结论。

    配对按完整箱号以重复次数逐次进行；``matched`` 按预期清单顺序、
    ``missing`` 按预期清单顺序、``extra`` 按现场清单顺序排列。恒有
    ``matched_count + missing_count == expected_count`` 与
    ``matched_count + extra_count == onsite_count``。
    """

    status: Literal["ok"] = "ok"
    expected_count: int = Field(..., description="预期清单项数")
    onsite_count: int = Field(..., description="现场清单项数")
    matched_count: int = Field(..., description="两侧成功配对的箱数")
    missing_count: int = Field(..., description="预期中缺少（现场未扫到）的项数")
    extra_count: int = Field(..., description="现场多出（预期无此项）的项数")
    matched: list[MatchedContainerOut] = Field(
        ..., description="已匹配项，按预期清单中的先后顺序"
    )
    missing: list[MissingContainerOut] = Field(
        ..., description="预期中缺少项，按预期清单中的先后顺序"
    )
    extra: list[ExtraContainerOut] = Field(
        ..., description="现场多出项，按现场清单中的先后顺序"
    )


class ReconcileInvalidItemResponse(BaseModel):
    """任一清单含无效箱号时整次核对拒绝：明确清单来源、最小输入索引与原校验结论。

    无效原因有两种，字段互斥地承载各自的原校验结论：

    * **结构非法**：``error_code`` / ``position`` / ``message`` 给出与批量
      校验 :class:`InvalidBatchResponse` 同口径的首个损坏位置，两个校验位
      字段为 ``null``（结构非法时不做校验位复算）；
    * **校验位不符**：结构合法但末位校验码错误，三个结构字段为 ``null``，
      ``expected_check_digit`` / ``actual_check_digit`` 给出与批量校验逐项
      结论同口径的期望与实际校验位。

    两种情形 ``passed`` 均为 ``false``。定位顺序为：先按索引扫描预期清单，
    预期清单全部有效后再扫描现场清单，故索引为该来源清单中的最小无效索引。
    """

    status: Literal["invalid_item"] = "invalid_item"
    expected_count: int = Field(..., description="本次提交的预期清单项数")
    onsite_count: int = Field(..., description="本次提交的现场清单项数")
    list_source: Literal["expected", "onsite"] = Field(
        ...,
        description="无效项来源：expected=当班作业清单，onsite=现场扫描清单",
    )
    index: int = Field(
        ..., description="无效项在其来源清单中的最小输入索引（从 0 起）"
    )
    container_number: str = Field(..., description="该无效项的原样输入")
    error_code: str | None = Field(
        ...,
        description="结构错误代码（与批量校验一致）；校验位不符时为 null",
    )
    position: int | None = Field(
        ...,
        description=(
            f"箱号内首个损坏字符的位置（从 1 起，共 {CONTAINER_LENGTH} 位）；"
            "校验位不符时为 null"
        ),
    )
    message: str | None = Field(
        ..., description="结构错误说明；校验位不符时为 null"
    )
    expected_check_digit: int | None = Field(
        ..., description="结构合法但校验位不符时的期望校验位；结构非法时为 null"
    )
    actual_check_digit: int | None = Field(
        ..., description="结构合法但校验位不符时的实际校验位；结构非法时为 null"
    )
    passed: Literal[False] = Field(
        ..., description="原校验结论：无效项恒为 false"
    )
