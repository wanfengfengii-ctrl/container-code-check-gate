"""集装箱箱号（ISO 6346 风格）校验位算法与结构校验。

规则（与需求逐字对应，不做任何大小写或空白归一化）：

* 箱号恰为 11 个字符：3 个大写字母（箱主代码）+ 1 个设备类别码
  （仅 U/J/Z）+ 6 位数字（顺序号）+ 1 位校验数字。
* 前 10 位从左到右编号 0..9 参与加权：
  - 数字取其原值 0..9；
  - 字母从 A=10 起递增，凡 11 的倍数一律跳过，因此 B=12、K=21、
    L=23、Z=38（没有任何字母取到 11/22/33）；
  - 第 i 位字符值乘以 2**i，求和后对 11 取余；余数为 10 时期望
    校验位为 0，其余余数即为期望校验位。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

CONTAINER_LENGTH = 11
CATEGORY_IDENTIFIERS = frozenset("UJZ")
MODULUS = 11
REMAINDER_FOR_ZERO_DIGIT = 10  # 余数 10 映射为校验位 0


def _build_letter_values() -> dict[str, int]:
    """构造字母->数值映射：A=10，递增并跳过所有 11 的倍数。"""
    values: dict[str, int] = {}
    value = 10
    for code in range(ord("A"), ord("Z") + 1):
        values[chr(code)] = value
        value += 1
        if value % MODULUS == 0:
            value += 1
    return values


#: 26 个大写字母的字符值，例如 {"A": 10, "B": 12, ..., "Z": 38}
LETTER_VALUES: dict[str, int] = _build_letter_values()


def letter_value(letter: str) -> int:
    """返回单个大写字母的字符值；非大写 A-Z 抛出 KeyError。"""
    return LETTER_VALUES[letter]


def character_value(char: str) -> int:
    """返回单个参与加权字符的值：数字取原值，大写字母按跳号表取值。"""
    if "0" <= char <= "9":
        return ord(char) - ord("0")
    return LETTER_VALUES[char]


@dataclass(frozen=True)
class ChecksumStep:
    """前 10 位中单个字符的加权计算步骤（不可变）。

    ``position`` 从 1 起计，与 :class:`StructureError` 的位置口径一致；
    ``weight`` 为 ``2 ** (position - 1)``；``product`` 为
    ``value * weight``。现场复核时按此逐步骤对账。
    """

    position: int
    character: str
    value: int
    weight: int
    product: int


def checksum_steps(first_ten: str) -> tuple[ChecksumStep, ...]:
    """把参与加权的字符逐位展开为不可变计算步骤（按原位置顺序）。

    这是加权计算的唯一来源：:func:`weighted_sum` 与
    :func:`explain_check_digit` 的汇总值都由这些步骤的 ``product``
    求和得到，避免明细与校验各算一套而产生分歧。
    """
    steps: list[ChecksumStep] = []
    for index, char in enumerate(first_ten):
        value = character_value(char)
        weight = 2 ** index
        steps.append(
            ChecksumStep(
                position=index + 1,
                character=char,
                value=value,
                weight=weight,
                product=value * weight,
            )
        )
    return tuple(steps)


def weighted_sum(first_ten: str) -> int:
    """对前 10 位字符按 2**0 .. 2**9 加权求和（由计算步骤汇总）。"""
    return sum(step.product for step in checksum_steps(first_ten))


def _check_digit_from_remainder(remainder: int) -> int:
    """把取模余数折叠为期望校验位：余数 10 记为 0，其余余数原样。"""
    if remainder == REMAINDER_FOR_ZERO_DIGIT:
        return 0
    return remainder


def expected_check_digit(first_ten: str) -> int:
    """由前 10 位计算期望校验位（余数 10 折叠为 0）。"""
    return _check_digit_from_remainder(weighted_sum(first_ten) % MODULUS)


@dataclass(frozen=True)
class ChecksumExplanation:
    """单箱校验位计算的完整明细：不可变步骤序列与由步骤派生的汇总。

    ``weighted_sum`` 是 ``steps`` 各项 ``product`` 之和，``remainder``
    是合计对 11 的余数，``expected_check_digit`` 是余数折叠结果；与
    :func:`weighted_sum`、:func:`expected_check_digit` 同源同口径。
    """

    container_number: str
    steps: tuple[ChecksumStep, ...]
    weighted_sum: int
    remainder: int
    expected_check_digit: int
    actual_check_digit: int
    passed: bool


def explain_check_digit(container_number: str) -> ChecksumExplanation:
    """生成单箱逐字符计算明细，供校验结论争议时现场复核。

    输入必须恰为 11 位且结构合法（路由层以 :func:`structure_error`
    把关）；不做任何大小写或空白归一化。汇总值全部由本次生成的步骤
    求和、取模、折叠得到，保证明细与结论永远一致。
    """
    if len(container_number) != CONTAINER_LENGTH:
        raise ValueError(
            f"container number must be exactly {CONTAINER_LENGTH} "
            f"characters, got {len(container_number)}"
        )

    steps = checksum_steps(container_number[:10])
    total = sum(step.product for step in steps)
    remainder = total % MODULUS
    expected = _check_digit_from_remainder(remainder)
    actual = int(container_number[10])
    return ChecksumExplanation(
        container_number=container_number,
        steps=steps,
        weighted_sum=total,
        remainder=remainder,
        expected_check_digit=expected,
        actual_check_digit=actual,
        passed=expected == actual,
    )


@dataclass(frozen=True)
class StructureError:
    """结构非法时定位首个损坏位置（position 从 1 起计）。"""

    code: str
    position: int
    message: str


def structure_error(container_number: str) -> StructureError | None:
    """返回箱号的首个结构问题；全部合法时返回 None。

    逐位、从左到右检查，保证报出的是该字符串中最先损坏的位置。
    """
    if len(container_number) != CONTAINER_LENGTH:
        return StructureError(
            code="invalid_length",
            position=min(len(container_number), CONTAINER_LENGTH) + 1,
            message=(
                f"container number must be exactly {CONTAINER_LENGTH} "
                f"characters, got {len(container_number)}"
            ),
        )

    for position in range(0, 3):
        char = container_number[position]
        if not ("A" <= char <= "Z"):
            return StructureError(
                code="not_uppercase_letter",
                position=position + 1,
                message=(
                    f"character at position {position + 1} must be an "
                    "uppercase letter A-Z (no case normalization is applied)"
                ),
            )

    category = container_number[3]
    if category not in CATEGORY_IDENTIFIERS:
        return StructureError(
            code="invalid_category_identifier",
            position=4,
            message=(
                "character at position 4 must be one of U, J, Z "
                f"(equipment category identifier), got {category!r}"
            ),
        )

    for position in range(4, 11):
        char = container_number[position]
        if not ("0" <= char <= "9"):
            return StructureError(
                code="not_digit",
                position=position + 1,
                message=(
                    f"character at position {position + 1} must be a digit "
                    "0-9"
                ),
            )

    return None


@dataclass(frozen=True)
class ContainerParts:
    """合法箱号拆分出的字段。"""

    owner_code: str
    category_identifier: str
    serial_number: str
    check_digit: str


def split_container_number(container_number: str) -> ContainerParts:
    """拆分已通过结构校验的箱号。"""
    return ContainerParts(
        owner_code=container_number[0:3],
        category_identifier=container_number[3],
        serial_number=container_number[4:10],
        check_digit=container_number[10],
    )


@dataclass(frozen=True)
class OwnerSummary:
    """单个箱主的班组统计（不可变）。

    ``total`` 为该箱主在本批中的箱数，``passed`` / ``failed`` 为其中
    校验通过与未通过的数量；恒有 ``passed + failed == total``。
    """

    owner_code: str
    total: int
    passed: int
    failed: int


def summarize_by_owner(
    verdicts: Iterable[tuple[str, bool]],
) -> tuple[OwnerSummary, ...]:
    """把逐箱（箱主代码, 是否通过）结论按箱主聚合成班组统计。

    输入来自已完成的逐箱校验结论，本函数只负责计数，不重新拆分箱号、
    不复算校验位。按箱主**首次出现顺序**返回，重复箱主只形成一项；
    每项给出总数、通过数与未通过数。
    """
    totals: dict[str, int] = {}
    passes: dict[str, int] = {}
    for owner_code, passed in verdicts:
        if owner_code not in totals:
            totals[owner_code] = 0
            passes[owner_code] = 0
        totals[owner_code] += 1
        passes[owner_code] += int(passed)
    # dict 依插入顺序迭代，即箱主首次出现顺序。
    return tuple(
        OwnerSummary(
            owner_code=owner_code,
            total=total,
            passed=passes[owner_code],
            failed=total - passes[owner_code],
        )
        for owner_code, total in totals.items()
    )


@dataclass(frozen=True)
class CorrectionCandidate:
    """单字符纠错候选：差异位置、原字符、新字符与完整候选号。

    ``position`` 从 1 起计，与 :class:`StructureError` 的位置口径一致。
    """

    position: int
    original_character: str
    replacement_character: str
    container_number: str


def _allowed_characters(position: int) -> str:
    """返回 0 起计位置上允许出现的字符全集（升序，保证枚举顺序稳定）。"""
    if position < 3:
        return "".join(sorted(LETTER_VALUES))
    if position == 3:
        return "".join(sorted(CATEGORY_IDENTIFIERS))
    return "0123456789"


def correction_candidates(container_number: str) -> list[CorrectionCandidate]:
    """枚举与原值仅一位不同且结构、校验位均合法的候选箱号。

    输入必须恰为 11 位（路由层按请求校验保证）；不做任何大小写或空白
    归一化。逐位、逐字符枚举汉明距离恰为 1 的串，复用本模块的字符映射、
    :func:`structure_error` 结构判定与 :func:`expected_check_digit` 校验位
    计算筛选；原号自身（差异 0 位）与差异多位的号码一律不进入结果。
    返回按（差异位置, 替换字符）稳定排序的候选列表。
    """
    if len(container_number) != CONTAINER_LENGTH:
        raise ValueError(
            f"container number must be exactly {CONTAINER_LENGTH} "
            f"characters, got {len(container_number)}"
        )

    candidates: list[CorrectionCandidate] = []
    for position in range(CONTAINER_LENGTH):
        original = container_number[position]
        for replacement in _allowed_characters(position):
            if replacement == original:
                continue  # 跳过原字符，保证汉明距离恰为 1
            candidate = (
                container_number[:position]
                + replacement
                + container_number[position + 1 :]
            )
            if structure_error(candidate) is not None:
                continue
            if expected_check_digit(candidate[:10]) != int(candidate[10]):
                continue
            candidates.append(
                CorrectionCandidate(
                    position=position + 1,
                    original_character=original,
                    replacement_character=replacement,
                    container_number=candidate,
                )
            )
    candidates.sort(key=lambda c: (c.position, c.replacement_character))
    return candidates


# --------------------------------------------------------------- 清单核对

#: 清单来源标识：expected=当班作业清单（预期清单），onsite=现场扫描清单。
EXPECTED_LIST: Literal["expected"] = "expected"
ONSITE_LIST: Literal["onsite"] = "onsite"


@dataclass(frozen=True)
class ReconcileInvalidItem:
    """导致整次核对被拒绝的无效箱号定位（结构非法或校验位不符）。

    ``list_source`` 为 :data:`EXPECTED_LIST` / :data:`ONSITE_LIST`，指明
    无效项来自哪一份清单；``index`` 为该项在该清单中的最小输入索引
    （从 0 起）；``container_number`` 原样回显。``passed`` 恒为
    ``False``，即原校验结论；无效原因有两种：

    * 结构非法：``structure_error`` 为首个损坏位置（:class:`StructureError`，
      与批量校验同一来源），``expected_check_digit`` /
      ``actual_check_digit`` 均为 ``None``（结构非法时不做校验位复算）；
    * 校验位不符：``structure_error`` 为 ``None``，两个校验位字段给出
      原批量校验同口径的期望校验位与实际校验位。
    """

    list_source: Literal["expected", "onsite"]
    index: int
    container_number: str
    structure_error: StructureError | None
    expected_check_digit: int | None
    actual_check_digit: int | None
    passed: bool


@dataclass(frozen=True)
class MatchedContainer:
    """一对成功配对的箱号（同一完整箱号在两侧各出现一次）。

    ``expected_index`` / ``onsite_index`` 分别为其在预期清单与现场清单
    中的原索引（从 0 起）。
    """

    container_number: str
    expected_index: int
    onsite_index: int


@dataclass(frozen=True)
class MissingContainer:
    """预期清单中存在、但现场扫不到对应次数的箱号（预期侧差异）。"""

    container_number: str
    expected_index: int


@dataclass(frozen=True)
class ExtraContainer:
    """现场清单中存在、但预期清单没有对应次数的箱号（现场侧差异）。"""

    container_number: str
    onsite_index: int


@dataclass(frozen=True)
class ReconcileResult:
    """一次性清单核对结论（全部箱号结构合法且校验通过时才产生）。

    配对以重复次数参与：同一箱号按出现次序逐次配成一对，超出另一侧
    次数的部分才归入差异。因此一侧出现三次、另一侧两次时，只有**最后
    一次**（按各自清单顺序）成为差异。

    三个结果列表均保持对应清单中的先后顺序：

    * ``matched``：按**预期清单**中被配对项的顺序；
    * ``missing``：按**预期清单**顺序；
    * ``extra``：按**现场清单**顺序。

    配对数量守恒：``len(matched) + len(missing) == len(expected)`` 且
    ``len(matched) + len(extra) == len(onsite)``。
    """

    expected_count: int
    onsite_count: int
    matched_count: int
    missing_count: int
    extra_count: int
    matched: tuple[MatchedContainer, ...]
    missing: tuple[MissingContainer, ...]
    extra: tuple[ExtraContainer, ...]


def _first_invalid_item(
    expected: Sequence[str], onsite: Sequence[str]
) -> ReconcileInvalidItem | None:
    """按各自输入顺序找出首个无效箱号（先预期清单，后现场清单）。

    每份清单内部按索引从小到大检查；只有预期清单全部有效后才开始检查
    现场清单。结构判定复用 :func:`structure_error`，结构合法者再复用
    :func:`expected_check_digit` 判断校验位，与批量校验同一来源、同一
    口径。结构非法或校验位不符都使整次核对被拒绝。
    """
    for list_source, numbers in (
        (EXPECTED_LIST, expected),
        (ONSITE_LIST, onsite),
    ):
        for index, container_number in enumerate(numbers):
            error = structure_error(container_number)
            if error is not None:
                return ReconcileInvalidItem(
                    list_source=list_source,
                    index=index,
                    container_number=container_number,
                    structure_error=error,
                    expected_check_digit=None,
                    actual_check_digit=None,
                    passed=False,
                )
            expected_digit = expected_check_digit(container_number[:10])
            actual_digit = int(container_number[10])
            if expected_digit != actual_digit:
                return ReconcileInvalidItem(
                    list_source=list_source,
                    index=index,
                    container_number=container_number,
                    structure_error=None,
                    expected_check_digit=expected_digit,
                    actual_check_digit=actual_digit,
                    passed=False,
                )
    return None


def reconcile_container_numbers(
    expected: Sequence[str], onsite: Sequence[str]
) -> ReconcileResult | ReconcileInvalidItem:
    """把当班作业清单（预期）与现场扫描清单按完整箱号逐次配对。

    调用方提交两份清单后：

    1. **先校验**：按各自输入顺序（预期优先于现场，清单内按索引）复用
       :func:`structure_error` 与 :func:`expected_check_digit` 逐项判断；
       任一项结构非法或校验位不符，整次核对拒绝，返回
       :class:`ReconcileInvalidItem`，指出清单来源、最小输入索引与原校验
       结论（结构错误或校验位不符），不产生任何配对结果。
    2. **后配对**：全部有效时，按完整箱号以重复次数逐次配对（FIFO）：
       同一箱号的第 k 次出现与另一侧的第 k 次出现配成一对，超出另一侧
       次数的最后若干次才归入差异——一侧三次、另一侧两次时，仅最后一次
       成为差异。

    返回 :class:`ReconcileResult`，其 ``matched`` 按预期清单顺序、
    ``missing`` 按预期清单顺序、``extra`` 按现场清单顺序排列，并携带各
    项在原清单中的索引。本函数不做任何大小写或空白归一化。
    """
    invalid = _first_invalid_item(expected, onsite)
    if invalid is not None:
        return invalid

    # 现场清单按完整箱号建 FIFO 队列：同一箱号的多次出现按现场顺序排队，
    # 预期清单逐项消费队首，实现“按重复次数逐次配对”。
    onsite_queues: dict[str, deque[int]] = {}
    for index, container_number in enumerate(onsite):
        onsite_queues.setdefault(container_number, deque()).append(index)

    matched: list[MatchedContainer] = []
    missing: list[MissingContainer] = []
    # 按预期顺序消费；未被消费的现场索引即多出项，稍后按现场顺序筛出。
    consumed_onsite: set[int] = set()
    for expected_index, container_number in enumerate(expected):
        queue = onsite_queues.get(container_number)
        if queue:
            onsite_index = queue.popleft()
            consumed_onsite.add(onsite_index)
            matched.append(
                MatchedContainer(
                    container_number=container_number,
                    expected_index=expected_index,
                    onsite_index=onsite_index,
                )
            )
        else:
            missing.append(
                MissingContainer(
                    container_number=container_number,
                    expected_index=expected_index,
                )
            )

    # 现场侧未被任何预期项消费的出现即多出项；重扫现场清单以保持现场顺序。
    extra = [
        ExtraContainer(
            container_number=container_number,
            onsite_index=onsite_index,
        )
        for onsite_index, container_number in enumerate(onsite)
        if onsite_index not in consumed_onsite
    ]

    return ReconcileResult(
        expected_count=len(expected),
        onsite_count=len(onsite),
        matched_count=len(matched),
        missing_count=len(missing),
        extra_count=len(extra),
        matched=tuple(matched),
        missing=tuple(missing),
        extra=tuple(extra),
    )


# --------------------------------------------------------------- 闸口多读数共识

#: 共识结果携带的最优解条数上限：最优解总数是候选域上的精确计数，可能
#: 极大；结果只携带按完整箱号字典序排列的前 CONSENSUS_SOLUTION_LIMIT 个。
CONSENSUS_SOLUTION_LIMIT = 100


@dataclass(frozen=True)
class ConsensusResult:
    """同一箱体多条原始读数的共识求解结论（不可变）。

    ``reading_count`` 为参与计票的原始读数条数（重复读数重复计票）。
    ``minimum_cost`` 为最优解对全部读数的逐位不一致总数；**无解时为
    ``None``**——任一位置无合法观测（该位置候选域为空），或候选域中
    不存在满足校验位的组合。``optimal_count`` 为达到最小代价的合法
    箱号总数（精确计数，可能远大于返回条数；无解时为 0）。
    ``solutions`` 为按完整箱号字典序排列的前
    :data:`CONSENSUS_SOLUTION_LIMIT` 个最优解；``truncated`` 表示
    最优解总数超出返回条数。
    """

    reading_count: int
    minimum_cost: int | None
    optimal_count: int
    solutions: tuple[str, ...]
    truncated: bool


def consensus_container_number(readings: Sequence[str]) -> ConsensusResult:
    """对同一箱体的多条原始读数求校验位约束下的全局最优共识箱号。

    求解口径：

    * **候选域**：各位置取“实际出现且符合该位置字符域”的字符（字符域
      与纠错候选同源，复用 :func:`_allowed_characters`）；非法字符（小写、
      全角、未配对代理字符等）只计入不一致数，绝不进入候选域；
    * **代价**：候选箱号对全部读数的逐位不一致总数，重复读数重复计票；
    * **全局最优**：反向动态规划——前 10 位按加权余数（mod 11）做状态
      转移，第 11 位由余数唯一确定且必须落在候选域中；每个状态记录
      （最小代价, 达到该代价的完整箱号数）。**绝不**先逐位取多数再修补
      末位：局部多数拼装号可能违反校验位，即使修补合法也未必全局最优；
    * **无解**：任一位置无合法观测，或候选域中不存在满足校验位的组合；
      两类边界同口径，``minimum_cost`` 为 None、``optimal_count`` 为 0。

    输入每条必须恰为 11 位（契约层按请求校验保证；直接调用违反时抛出
    ValueError）；不做任何大小写或空白归一化。读数条数上下限（2..100）
    由契约层保证，领域层不限制。
    """
    for reading in readings:
        if len(reading) != CONTAINER_LENGTH:
            raise ValueError(
                f"each reading must be exactly {CONTAINER_LENGTH} "
                f"characters, got {len(reading)}"
            )

    # 逐位置候选域与不一致计票：候选为“实际出现 ∩ 字符域”，按码位升序
    # 固定枚举顺序（精确计数与字典序枚举的确定性都依赖该顺序）。
    candidates: list[list[str]] = []
    disagreement: list[dict[str, int]] = []
    for position in range(CONTAINER_LENGTH):
        domain = set(_allowed_characters(position))
        observed = {reading[position] for reading in readings}
        legal = sorted(observed & domain)
        candidates.append(legal)
        disagreement.append(
            {
                char: sum(
                    1 for reading in readings if reading[position] != char
                )
                for char in legal
            }
        )

    # 反向 DP：suffix[position][remainder] = (最小附加代价, 方案数)，表示
    # 前 position 位加权余数为 remainder 时，完成第 position..10 位且满足
    # 校验位约束的全局最优；None 表示该状态不可行。终止层（第 11 位）：
    # 余数唯一确定期望校验位，且必须落在候选域中。
    suffix: list[list[tuple[int, int] | None]] = [
        [None] * MODULUS for _ in range(CONTAINER_LENGTH)
    ]
    terminal = suffix[CONTAINER_LENGTH - 1]
    for remainder in range(MODULUS):
        digit = str(_check_digit_from_remainder(remainder))
        if digit in disagreement[CONTAINER_LENGTH - 1]:
            terminal[remainder] = (
                disagreement[CONTAINER_LENGTH - 1][digit],
                1,
            )
    for position in range(CONTAINER_LENGTH - 2, -1, -1):
        weight = 2 ** position
        layer = suffix[position]
        following = suffix[position + 1]
        for remainder in range(MODULUS):
            best_cost: int | None = None
            best_count = 0
            for char in candidates[position]:
                nxt = (remainder + character_value(char) * weight) % MODULUS
                sub = following[nxt]
                if sub is None:
                    continue
                cost = disagreement[position][char] + sub[0]
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_count = sub[1]
                elif cost == best_cost:
                    best_count += sub[1]
            if best_cost is not None:
                layer[remainder] = (best_cost, best_count)

    root = suffix[0][0]
    if root is None:
        # 两类无解边界同口径：候选域为空的位置使 DP 天然不可行；候选域
        # 非空但无任何组合满足校验位时，终止层全部不可达。
        return ConsensusResult(
            reading_count=len(readings),
            minimum_cost=None,
            optimal_count=0,
            solutions=(),
            truncated=False,
        )

    minimum_cost, optimal_count = root
    solutions = tuple(
        _enumerate_consensus_solutions(
            candidates, disagreement, suffix, CONSENSUS_SOLUTION_LIMIT
        )
    )
    return ConsensusResult(
        reading_count=len(readings),
        minimum_cost=minimum_cost,
        optimal_count=optimal_count,
        solutions=solutions,
        truncated=optimal_count > len(solutions),
    )


def _enumerate_consensus_solutions(
    candidates: list[list[str]],
    disagreement: list[dict[str, int]],
    suffix: list[list[tuple[int, int] | None]],
    limit: int,
) -> list[str]:
    """按完整箱号字典序枚举前 ``limit`` 个最优解（回溯反向 DP 表）。

    从（位置 0, 余数 0）出发，逐位置按码位升序尝试候选字符，只沿
    “仍能达到全局最小代价”的分支深入；第 11 位由余数唯一确定。
    候选列表已按码位升序，故枚举顺序即完整箱号的字典序。
    """

    def walk(position: int, remainder: int, remaining: int) -> list[str]:
        if remaining == 0:
            return []
        if position == CONTAINER_LENGTH - 1:
            # 可行性由父分支保证：该余数的期望校验位必在候选域中。
            return [str(_check_digit_from_remainder(remainder))]
        state = suffix[position][remainder]
        assert state is not None  # 只沿可行分支深入
        budget = state[0]
        found: list[str] = []
        for char in candidates[position]:
            nxt = (remainder + character_value(char) * (2**position)) % MODULUS
            sub = suffix[position + 1][nxt]
            if sub is None:
                continue
            if disagreement[position][char] + sub[0] != budget:
                continue  # 该字符不在任何全局最优路径上
            for tail in walk(position + 1, nxt, remaining - len(found)):
                found.append(char + tail)
            if len(found) >= remaining:
                break
        return found

    return walk(0, 0, limit)
