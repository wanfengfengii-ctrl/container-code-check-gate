"""闸口箱号批量校验 API。

区分两类结论：

* 批次无法解析（HTTP 422 语义，由自定义 ``invalid_batch`` 负载返回）：
  批内任一项结构非法即整批拒绝，定位到最小输入索引及该箱号内首个
  损坏字符位置；
* 批次可解析（HTTP 200）：逐项给出拆分字段、加权和、期望/实际校验位
  与通过与否，由调用方据此决定放行或拦截。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.checksum import (
    expected_check_digit,
    split_container_number,
    structure_error,
    weighted_sum,
)
from app.schemas import (
    ContainerPartsOut,
    ContainerResult,
    InvalidBatchResponse,
    VerifyRequest,
    VerifyResponse,
)

app = FastAPI(
    title="Container Gate Check-Digit Verification API",
    version="1.0.0",
    description=(
        "纯后端批量箱号校验位复算服务。每次接收 1-100 个恰为 11 位的 "
        "箱号；不做大小写或空白归一化。"
    ),
)


@app.get("/health")
def health() -> dict[str, str]:
    """存活探针，供容器编排与一次性验收服务使用。"""
    return {"status": "ok"}


@app.post(
    "/api/v1/container-numbers/verify",
    response_model=VerifyResponse,
    summary="批量复算箱号校验位",
    responses={
        422: {
            "model": InvalidBatchResponse,
            "description": (
                "批次结构非法：任一箱号不满足结构规则，整批拒绝，"
                "并返回最小输入索引与首个损坏位置。"
            ),
        }
    },
)
def verify_container_numbers(request: VerifyRequest) -> VerifyResponse | JSONResponse:
    container_numbers = request.container_numbers

    # 第一遍：找到最小的结构非法索引即整批拒绝（不继续看后面的项）。
    for index, container_number in enumerate(container_numbers):
        error = structure_error(container_number)
        if error is not None:
            payload = InvalidBatchResponse(
                count=len(container_numbers),
                index=index,
                container_number=container_number,
                error_code=error.code,
                position=error.position,
                message=error.message,
            ).model_dump()
            return JSONResponse(status_code=422, content=payload)

    # 第二遍：结构全部合法，逐项复算，结论互不影响。
    results: list[ContainerResult] = []
    passed_count = 0
    for index, container_number in enumerate(container_numbers):
        parts = split_container_number(container_number)
        first_ten = container_number[:10]
        total = weighted_sum(first_ten)
        expected = expected_check_digit(first_ten)
        actual = int(container_number[10])
        passed = expected == actual
        passed_count += int(passed)
        results.append(
            ContainerResult(
                index=index,
                container_number=container_number,
                parts=ContainerPartsOut(
                    owner_code=parts.owner_code,
                    category_identifier=parts.category_identifier,
                    serial_number=parts.serial_number,
                    check_digit=parts.check_digit,
                ),
                weighted_sum=total,
                expected_check_digit=expected,
                actual_check_digit=actual,
                passed=passed,
            )
        )

    return VerifyResponse(
        count=len(container_numbers),
        passed_count=passed_count,
        failed_count=len(container_numbers) - passed_count,
        results=results,
    )
